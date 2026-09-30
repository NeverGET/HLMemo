#!/usr/bin/env python3
"""R4 (R-10, plan §2.2) REAL-TIME load smoke through the real stack: Caddy -> uvicorn -> MCP streamable
HTTP, against a DISPOSABLE local stack only (stdlib; never run it against production).

While 4 memory.ask calls wait on writer delays of 60 / 90 / 130 / 170 s at real time (the mock provider,
``deploy/smoke/mock_provider.py``, sleeps by the ``smoke-delay=<s>`` marker this driver puts in each
question), it sends memory.query and memory.write traffic, polls ``/ready`` every 5 s, and fires a 5th
memory.ask once the 4 hold their slots. It prints ONE JSON verdict and exits 0 on PASS, 1 on FAIL, 2 on
a usage error:

* ``asks_complete``: each of the 4 asks returns a tool result that is not an error within the research
  timeout (170 s) + 10 s (with the R4 writer timeout of 120 s, the 130/170 s writers are cut and the
  research profile writes: still an answer);
* ``query_p95`` / ``write_p95``: nearest-rank p95 of the client-side call durations < 2 s, at least one
  call and no error;
* ``ready``: every ``/ready`` sample is HTTP 200 (at least one);
* ``fifth_busy``: the 5th ask is an ``E_UNAVAILABLE`` with ``details.reason`` "busy", within 5 s.

The MCP bearer token comes from the environment variable ``--token-env`` (default HLM_SMOKE_TOKEN; a
device with write on ``--project``) and is never printed. The URL must be a local target (loopback,
a private address, or a ``.local``/``.internal``/``.test``/``.localhost`` name) unless ``--allow-host``
names its host explicitly.

Setup on the disposable VM (the orchestrator's step; nothing here touches a server by itself):
1. the smoke profiles ``deploy/smoke/profiles/*.toml`` in the api's HLM_PROFILES_DIR (the api's root file
   system is read-only: e.g. a VM-local Compose override that mounts deploy/smoke read-only and sets
   HLM_PROFILES_DIR to its profiles/; /app/profiles stays searched after it), and in llm.env
   ``HLM_RESEARCH_WRITER_PROFILE=smoke-writer`` (optionally ``HLM_PROFILE=smoke-research`` and
   ``HLM_FALLBACK_PROFILE__RESEARCH=smoke-research``: no real provider call), ``SMOKE_MOCK_KEY=x``;
   the R4 timeouts stay (writer 120 s, research 170 s);
2. the mock where the api reaches ``http://127.0.0.1:18765/v1`` (e.g. inside the api container, whose
   8765 is uvicorn's own port): ``python3 mock_provider.py --port 18765``;
3. ``HLM_SMOKE_TOKEN=<token> python3 deploy/smoke/load_smoke.py --url https://<local host:port>
   --project <slug> --insecure`` (``--insecure`` for Caddy's internal CA).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

DELAYS_S = (60, 90, 130, 170)
RESEARCH_TIMEOUT_S = 170.0
GRACE_S = 10.0
P95_LIMIT_S = 2.0
READY_EVERY_S = 5.0
BUSY_LIMIT_S = 5.0
LOCAL_SUFFIXES = (".local", ".internal", ".test", ".localhost")
QUESTION = "What is the current release status and the retrieval latency target?"


# --------------------------------------------------------------------------- the verdict (pure)
def p95(values: list[float]) -> float | None:
    """Nearest-rank p95 (the ceil(0.95 n)-th smallest); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def verdict(obs: dict[str, Any], limits: dict[str, Any] | None = None) -> dict[str, Any]:
    """The smoke's PASS/FAIL from what was observed (no I/O).

    ``obs``: ``asks`` [{delay_s, seconds, ok, error}] (the 4 long asks), ``query_s`` / ``write_s``
    (durations of the successful calls), ``query_errors`` / ``write_errors`` (strings), ``ready`` (one
    HTTP status or error string per sample), ``fifth`` ({seconds, reason} or None: the 5th ask).
    ``limits``: ``expected_asks`` (4), ``ask_limit_s`` (170 + 10), ``p95_limit_s`` (2),
    ``busy_limit_s`` (5)."""
    lim = {
        "expected_asks": len(DELAYS_S),
        "ask_limit_s": RESEARCH_TIMEOUT_S + GRACE_S,
        "p95_limit_s": P95_LIMIT_S,
        "busy_limit_s": BUSY_LIMIT_S,
        **(limits or {}),
    }
    asks = list(obs.get("asks") or [])
    late_or_failed = [
        a for a in asks if not a.get("ok") or a.get("seconds") is None or a["seconds"] > lim["ask_limit_s"]
    ]
    checks: dict[str, Any] = {
        "asks_complete": {
            "pass": len(asks) == lim["expected_asks"] and not late_or_failed,
            "limit_s": lim["ask_limit_s"],
            "asks": asks,
        }
    }
    for kind in ("query", "write"):
        durations = list(obs.get(f"{kind}_s") or [])
        errors = list(obs.get(f"{kind}_errors") or [])
        value = p95(durations)
        checks[f"{kind}_p95"] = {
            "pass": value is not None and value < lim["p95_limit_s"] and not errors,
            "p95_s": None if value is None else round(value, 3),
            "n": len(durations),
            "errors": len(errors),
            "first_errors": errors[:3],
            "limit_s": lim["p95_limit_s"],
        }
    ready = list(obs.get("ready") or [])
    bad = [s for s in ready if s != 200]
    checks["ready"] = {"pass": bool(ready) and not bad, "samples": len(ready), "not_200": bad[:10]}
    fifth = obs.get("fifth")
    checks["fifth_busy"] = {
        "pass": bool(fifth)
        and fifth.get("reason") == "busy"
        and fifth.get("seconds") is not None
        and fifth["seconds"] <= lim["busy_limit_s"],
        "reason": (fifth or {}).get("reason"),
        "seconds": (fifth or {}).get("seconds"),
        "limit_s": lim["busy_limit_s"],
    }
    return {"pass": all(c["pass"] for c in checks.values()), "checks": checks}


# --------------------------------------------------------------------------- the MCP client
class McpError(Exception):
    """A transport or JSON-RPC failure (never carries the token)."""


class Mcp:
    """One MCP streamable-HTTP session (initialize, then tools/call) over urllib."""

    def __init__(self, url: str, token: str, context: ssl.SSLContext | None, timeout_s: float) -> None:
        self.url, self._token, self.context, self.timeout_s = url, token, context, timeout_s
        self.session: str | None = None
        self.protocol = "2025-03-26"
        self.seq = 0

    def _post(self, body: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
            "MCP-Protocol-Version": self.protocol,
            "X-HLM-Client": "r4-load-smoke/1",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url + "/mcp", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=timeout_s) as resp:
                self.session = resp.headers.get("Mcp-Session-Id", self.session)
                raw = resp.read().decode()
                sse = "text/event-stream" in resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise McpError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise McpError(f"transport: {type(exc).__name__}") from None
        if not raw:
            return None
        if not sse:
            return json.loads(raw)
        for event in raw.split("\n\n"):
            data = "\n".join(ln[5:].lstrip() for ln in event.splitlines() if ln.startswith("data:"))
            if data:
                parsed = json.loads(data)
                if "result" in parsed or "error" in parsed:
                    return parsed
        raise McpError("SSE response without a JSON-RPC result")

    def rpc(
        self, method: str, params: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> Any:
        self.seq += 1
        answer = self._post(
            {"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}},
            timeout_s or self.timeout_s,
        )
        if not answer or "error" in answer:
            raise McpError(f"JSON-RPC {method} failed")
        return answer["result"]

    def initialize(self) -> None:
        init = self.rpc(
            "initialize",
            {
                "protocolVersion": self.protocol,
                "capabilities": {},
                "clientInfo": {"name": "r4-load-smoke", "version": "1"},
            },
        )
        self.protocol = init.get("protocolVersion", self.protocol)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, self.timeout_s)

    def tool(self, name: str, arguments: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        """The tool result: ``{"error": bool, "body": <the JSON text block>}``."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments}, timeout_s=timeout_s)
        text = (result.get("content") or [{}])[0].get("text") or "{}"
        try:
            body = json.loads(text)
        except ValueError:
            body = {}
        return {"error": bool(result.get("isError")), "body": body}


# --------------------------------------------------------------------------- the run
def local_target(url: str, allow_host: str | None) -> str:
    """The origin of ``url`` if it is a local target (module doc), else raise ValueError."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.path.strip("/"):
        raise ValueError("--url must be an http(s) origin without credentials or path")
    host = (parts.hostname or "").lower()
    if allow_host and host == allow_host.lower():
        return f"{parts.scheme}://{parts.netloc}"
    try:
        ip = ipaddress.ip_address(host)
        local = ip.is_loopback or ip.is_private
    except ValueError:
        local = host == "localhost" or host.endswith(LOCAL_SUFFIXES)
    if not local:
        raise ValueError(f"refusing a non-local target {host!r} (pass --allow-host {host} on purpose)")
    return f"{parts.scheme}://{parts.netloc}"


def run(args: argparse.Namespace, token: str) -> dict[str, Any]:
    context = None
    if args.url.startswith("https"):
        context = ssl.create_default_context(cafile=args.ca_file)
        if args.insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
    ask_timeout = args.research_timeout_s + args.grace_s + 30.0
    stop = threading.Event()
    lock = threading.Lock()
    obs: dict[str, Any] = {
        "asks": [],
        "query_s": [],
        "write_s": [],
        "query_errors": [],
        "write_errors": [],
        "ready": [],
        "fifth": None,
    }

    def client(timeout_s: float) -> Mcp:
        c = Mcp(args.url, token, context, timeout_s)
        c.initialize()
        return c

    def ask(delay_s: float) -> dict[str, Any]:
        q = f"{QUESTION} [smoke-delay={delay_s:g}] ({uuid.uuid4().hex[:6]})"
        t0 = time.perf_counter()
        try:
            res = client(30.0).tool(
                "memory.ask", {"question": q, "project": args.project}, timeout_s=ask_timeout
            )
            reason = (res["body"].get("details") or {}).get("reason") if res["error"] else None
            out = {
                "ok": not res["error"],
                "error": reason or (res["body"].get("code") if res["error"] else None),
            }
        except (McpError, ValueError) as exc:
            out = {"ok": False, "error": str(exc)}
        return {"delay_s": delay_s, "seconds": round(time.perf_counter() - t0, 3), **out}

    def ready_poller() -> None:
        while True:
            try:
                req = urllib.request.Request(args.url + "/ready")
                with urllib.request.urlopen(req, context=context, timeout=args.ready_every_s) as resp:
                    status: Any = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            except (urllib.error.URLError, OSError) as exc:
                status = f"transport: {type(exc).__name__}"
            with lock:
                obs["ready"].append(status)
            if stop.wait(args.ready_every_s):
                return

    def traffic(kind: str, worker: int) -> None:
        try:
            c = client(30.0)
        except (McpError, ValueError) as exc:
            with lock:
                obs[f"{kind}_errors"].append(f"initialize: {exc}")
            return
        i = 0
        while not stop.is_set():
            i += 1
            if kind == "query":
                name = "memory.query"
                arguments: dict[str, Any] = {
                    "project": args.project,
                    "query": f"release status retrieval target {worker}-{i}",
                    "token_budget": 2000,
                }
            else:
                name = "memory.write"
                arguments = {
                    "project": args.project,
                    "request_id": str(uuid.uuid4()),
                    "client": "r4-load-smoke/1",
                    "items": [
                        {"kind": "fact", "title": f"load smoke note {worker}-{i}", "body": "R-10 smoke."}
                    ],
                }
            t0 = time.perf_counter()
            try:
                res = c.tool(name, arguments)
                seconds = time.perf_counter() - t0
                with lock:
                    if res["error"]:
                        obs[f"{kind}_errors"].append(str(res["body"].get("code")))
                    else:
                        obs[f"{kind}_s"].append(seconds)
            except (McpError, ValueError) as exc:
                with lock:
                    obs[f"{kind}_errors"].append(str(exc))
            if stop.wait(args.query_pause_s if kind == "query" else args.write_every_s):
                return

    with ThreadPoolExecutor(max_workers=len(args.delays) + args.query_workers + 4) as pool:
        poller = pool.submit(ready_poller)
        asks = [pool.submit(ask, d) for d in args.delays]
        workers = [pool.submit(traffic, "query", w) for w in range(args.query_workers)]
        workers.append(pool.submit(traffic, "write", 0))
        time.sleep(args.fifth_after_s)  # the 4 asks hold their slots from their start to their answer
        t0 = time.perf_counter()
        try:
            res = client(30.0).tool("memory.ask", {"question": f"{QUESTION} (5th)", "project": args.project})
            reason = (res["body"].get("details") or {}).get("reason") if res["error"] else "answered"
        except (McpError, ValueError) as exc:
            reason = str(exc)
        obs["fifth"] = {"reason": reason, "seconds": round(time.perf_counter() - t0, 3)}
        obs["asks"] = [f.result() for f in asks]
        stop.set()
        for f in [*workers, poller]:
            f.result()
    return obs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="R4 R-10: the real-time memory.ask load smoke (local stack only)"
    )
    ap.add_argument("--url", required=True, help="the local stack's origin, e.g. https://localhost:8443")
    ap.add_argument("--project", required=True)
    ap.add_argument("--token-env", default="HLM_SMOKE_TOKEN", help="env var holding the MCP bearer token")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (Caddy's internal CA)")
    ap.add_argument("--ca-file")
    ap.add_argument("--allow-host", help="accept this non-local host on purpose (a disposable VM's name)")
    ap.add_argument("--delays", default=",".join(str(d) for d in DELAYS_S), help="writer delays (s)")
    ap.add_argument("--research-timeout-s", type=float, default=RESEARCH_TIMEOUT_S)
    ap.add_argument("--grace-s", type=float, default=GRACE_S)
    ap.add_argument("--p95-limit-s", type=float, default=P95_LIMIT_S)
    ap.add_argument("--busy-limit-s", type=float, default=BUSY_LIMIT_S)
    ap.add_argument("--ready-every-s", type=float, default=READY_EVERY_S)
    ap.add_argument("--fifth-after-s", type=float, default=20.0, help="when the 5th ask is sent (s)")
    ap.add_argument("--query-workers", type=int, default=4)
    ap.add_argument("--query-pause-s", type=float, default=0.5)
    ap.add_argument("--write-every-s", type=float, default=2.0)
    try:
        args = ap.parse_args(argv)
        args.url = local_target(args.url, args.allow_host)
        args.delays = [float(x) for x in args.delays.split(",") if x.strip()]
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        sys.stderr.write(f"error: set the MCP token in ${args.token_env} (it is never printed)\n")
        return 2
    obs = run(args, token)
    out = verdict(
        obs,
        {
            "expected_asks": len(args.delays),
            "ask_limit_s": args.research_timeout_s + args.grace_s,
            "p95_limit_s": args.p95_limit_s,
            "busy_limit_s": args.busy_limit_s,
        },
    )
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
