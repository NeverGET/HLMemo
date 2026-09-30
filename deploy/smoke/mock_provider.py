#!/usr/bin/env python3
"""R4 (R-10) local smoke: a tiny OpenAI-compatible chat-completions mock (stdlib only).

It answers ``POST <anything>/chat/completions`` like a well-behaved research model, per JOB (the
first line of the user message, ``JOB: <job>``; its ``INPUT: {...}`` payload):

* ``plan`` / ``refine``: ``{"queries": [...], "sections": []}``;
* ``prose``: an ANSWERED prose built from the first usable sentence of the first excerpt it was shown
  (quoted verbatim, cited by the excerpt's id), else a valid abstention; ``prose_text`` the same in the
  plain-text layout;
* ``rerank`` ``{"order": []}``, ``attribute`` ``{"cites": []}``, ``expand`` ``{"add": []}`` (each keeps
  the server's own fallback), anything else ``{}``.

``--responses FILE`` (JSON ``{job: object}``) replaces a JOB's answer with a fixed object.

Per-request delay (the R-10 writer latency): a request whose ``model`` is ``--delay-model`` (the smoke
writer's model id) and whose JOB is in ``--delay-jobs`` sleeps the seconds that the marker
``smoke-delay=<s>`` in its text names (the driver puts it in the question), else ``--default-delay``.
Every other request answers at once. ``GET /_smoke/state`` returns ``{"waiting": n, "served": n}``
(requests sleeping now, requests answered). Nothing is logged but one line per request (model, JOB,
delay); request bodies are never printed.

Run it where the api container can reach it, e.g. inside the api container itself (the smoke
profiles point at ``http://127.0.0.1:8765/v1``):
    python3 mock_provider.py --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MARKER = re.compile(r"smoke-delay=(\d+(?:\.\d+)?)")
SENTENCE = re.compile(r"(?<=[.!?])\s+")

ABSTAIN = {"status": "insufficient_evidence", "answer": "", "sources": [], "related": [], "confidence": "low"}
FIXED: dict[str, Any] = {
    "plan": {"queries": ["release status", "retrieval target"], "sections": []},
    "refine": {"queries": ["current decision"], "sections": []},
    "rerank": {"order": []},
    "attribute": {"cites": []},
    "expand": {"add": []},
}


def job_of(user: str) -> tuple[str, dict[str, Any]]:
    """``(JOB, INPUT payload)`` of a research user message (``{}`` when it has none)."""
    job = user.split("\n", 1)[0].removeprefix("JOB: ").strip()
    at = user.find("INPUT: ")
    payload: Any = {}
    if at >= 0:
        try:
            payload, _end = json.JSONDecoder().raw_decode(user[at + len("INPUT: ") :])
        except ValueError:
            payload = {}
    return job, payload if isinstance(payload, dict) else {}


def first_sentence(excerpts: list[Any]) -> tuple[str, str] | None:
    """``(excerpt id, a verbatim sentence of it)``: the first sentence of 5-60 words outside a
    markdown heading, from the first excerpt that has one."""
    for ex in excerpts:
        if not isinstance(ex, dict) or not isinstance(ex.get("text"), str) or not ex.get("id"):
            continue
        body = " ".join(ln.strip() for ln in ex["text"].splitlines() if ln.strip() and not ln.startswith("#"))
        for s in SENTENCE.split(body):
            if 5 <= len(s.split()) <= 60 and s.endswith((".", "!", "?")):
                return str(ex["id"]), s
    return None


def prose(payload: dict[str, Any]) -> dict[str, Any]:
    hit = first_sentence(payload.get("excerpts") or [])
    if hit is None:
        return dict(ABSTAIN)
    handle, sentence = hit
    return {
        "status": "answered",
        "answer": sentence,
        "sources": [handle],
        "related": [],
        "confidence": "high",
    }


def prose_text(obj: dict[str, Any]) -> str:
    return (
        f"STATUS: {obj['status']}\nCONFIDENCE: {obj.get('confidence', 'low')}\n"
        f"SOURCES: {', '.join(obj.get('sources') or [])}\nRELATED: {', '.join(obj.get('related') or [])}\n"
        f"ANSWER:\n{obj.get('answer', '')}"
    )


def answer(job: str, payload: dict[str, Any], fixed: dict[str, Any]) -> str:
    """The message content for ``job`` (a JSON object, or the prose text layout)."""
    if job in fixed:
        obj = fixed[job]
        return obj if isinstance(obj, str) else json.dumps(obj)
    if job == "prose":
        return json.dumps(prose(payload))
    if job == "prose_text":
        return prose_text(prose(payload))
    return json.dumps(FIXED.get(job, {}))


def delay_for(body: dict[str, Any], text: str, args: argparse.Namespace, job: str) -> float:
    if body.get("model") != args.delay_model or job not in args.delay_jobs:
        return 0.0
    m = MARKER.search(text)
    return float(m.group(1)) if m else float(args.default_delay)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiting = 0
        self.served = 0


def make_handler(
    args: argparse.Namespace, fixed: dict[str, Any], state: State
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *a: Any) -> None:  # the default logs the request line
            return

        def _send(self, code: int, obj: Any) -> None:
            raw = json.dumps(obj).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except OSError:  # the client gave up (e.g. the writer timeout cut it): nothing to do
                pass

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/_smoke/state":
                with state.lock:
                    self._send(200, {"waiting": state.waiting, "served": state.served})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send(404, {"error": {"message": "not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                messages = body.get("messages") or []
                user = (
                    next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
                )
            except (ValueError, AttributeError):
                self._send(400, {"error": {"message": "bad request"}})
                return
            job, payload = job_of(str(user))
            wait = delay_for(body, str(user), args, job)
            sys.stdout.write(f"mock {body.get('model')} job={job} delay={wait:g}s\n")
            sys.stdout.flush()
            if wait > 0:
                with state.lock:
                    state.waiting += 1
                try:
                    time.sleep(wait)
                finally:
                    with state.lock:
                        state.waiting -= 1
            content = answer(job, payload, fixed)
            with state.lock:
                state.served += 1
            self._send(
                200,
                {
                    "id": f"smoke-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
                },
            )

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R4 R-10 smoke: an OpenAI-compatible mock provider")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--delay-model", default="smoke/writer", help="the model id whose requests are delayed")
    ap.add_argument("--delay-jobs", default="prose,prose_text", help="comma-separated JOBs to delay")
    ap.add_argument("--default-delay", type=float, default=0.0, help="seconds, when no smoke-delay marker")
    ap.add_argument("--responses", help="JSON file {job: object}: fixed answers per JOB")
    args = ap.parse_args(argv)
    args.delay_jobs = {j.strip() for j in args.delay_jobs.split(",") if j.strip()}
    fixed: dict[str, Any] = {}
    if args.responses:
        with open(args.responses, encoding="utf-8") as fh:
            fixed = json.load(fh)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args, fixed, State()))
    server.daemon_threads = True
    sys.stdout.write(
        f"mock provider on http://{args.host}:{args.port}/v1 (delayed model {args.delay_model})\n"
    )
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
