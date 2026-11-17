#!/usr/bin/env python3
"""Cemal's desk: a local page that collects the questions and approvals waiting for him (idea v7959).

Agents write question batches as JSON files; Cemal answers them in the page; the answers are appended to a
JSONL file next to them, where agents read them back. Nothing leaves the machine: the server binds 127.0.0.1,
refuses other Host headers (DNS rebinding) and needs a per-run token on every write (a cross-site form
cannot set the header).

Layout of the data directory (default docs/private/desk, git-ignored):
    questions/<batch>.json   written by agents, see validate_batch()
    answers/<batch>.jsonl    appended by the page, one record per answer; the newest one per question counts

Usage (stdlib only, Python 3.12):
    python3 -I tools/desk/desk.py serve [--data DIR] [--port 8765] [--place home|work]
    python3 -I tools/desk/desk.py check [--data DIR]            validate every batch file
    python3 -I tools/desk/desk.py status [--data DIR]           open and answered counts per batch
    python3 -I tools/desk/desk.py answers BATCH [--data DIR]    the current answer per question, as JSON
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_DATA = REPO / "docs" / "private" / "desk"
PLACES = ("home", "work", "any")
KINDS = ("question", "approval", "decision", "info")
ANSWER_TYPES = (
    "answer",  # a free-text or chosen answer
    "dont_know",  # blank / "I don't know": the agent falls back to inference and labels it as such
    "later",  # keeps the question open
    "rule",  # a rule instead of single answers (text holds the rule)
    "approve",
    "approve_conditional",  # text holds the conditions
    "reject",
    "reopen",  # withdraws the previous answer
)
OPEN_TYPES = {"later", "reopen"}
BATCH_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
QID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$")
MAX_TEXT = 20_000


class DeskError(ValueError):
    pass


class DeskConflict(DeskError):
    """The question changed since the page loaded it (HTTP 409)."""


def default_place() -> str:
    env = os.environ.get("HLM_DESK_PLACE")
    if env in PLACES:
        return env
    return "home" if sys.platform == "darwin" else "work"


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- batches


def validate_batch(doc: Any, batch_id: str) -> list[str]:
    """Return the problems of one batch document (empty list = valid)."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["the file is not a JSON object"]
    if doc.get("batch") != batch_id:
        errs.append(f"'batch' must equal the file name ({batch_id!r})")
    for key in ("title",):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            errs.append(f"'{key}' must be a non-empty string")
    for key in ("intro", "project", "created", "from"):
        if key in doc and not isinstance(doc[key], str):
            errs.append(f"'{key}' must be a string")
    qs = doc.get("questions")
    if not isinstance(qs, list) or not qs:
        errs.append("'questions' must be a non-empty list")
        return errs
    seen: set[str] = set()
    for i, q in enumerate(qs):
        where = f"questions[{i}]"
        if not isinstance(q, dict):
            errs.append(f"{where} is not an object")
            continue
        qid = q.get("id")
        if not isinstance(qid, str) or not QID.match(qid):
            errs.append(f"{where}.id must match {QID.pattern}")
        elif qid in seen:
            errs.append(f"{where}.id {qid!r} is repeated")
        else:
            seen.add(qid)
        if not isinstance(q.get("title"), str) or not q["title"].strip():
            errs.append(f"{where}.title must be a non-empty string")
        if "body" in q and not isinstance(q["body"], str):
            errs.append(f"{where}.body must be a string")
        if q.get("place", "any") not in PLACES:
            errs.append(f"{where}.place must be one of {PLACES}")
        if q.get("kind", "question") not in KINDS:
            errs.append(f"{where}.kind must be one of {KINDS}")
        if "group" in q and not isinstance(q["group"], str):
            errs.append(f"{where}.group must be a string")
        opts = q.get("options", [])
        if not isinstance(opts, list) or not all(isinstance(o, str) and o.strip() for o in opts):
            errs.append(f"{where}.options must be a list of non-empty strings")
        refs = q.get("refs", [])
        if not isinstance(refs, list):
            errs.append(f"{where}.refs must be a list")
            continue
        for j, r in enumerate(refs):
            rw = f"{where}.refs[{j}]"
            if not isinstance(r, dict) or not isinstance(r.get("label"), str):
                errs.append(f"{rw} needs a string 'label'")
                continue
            targets = [k for k in ("url", "path", "clue", "note") if k in r]
            if len(targets) != 1:
                errs.append(f"{rw} needs exactly one of url, path, clue, note")
                continue
            if "url" in r and not str(r["url"]).startswith("https://"):
                errs.append(f"{rw}.url must start with https://")
            if "clue" in r and not re.match(r"^v\d+(\.\d+)?$", str(r["clue"])):
                errs.append(f"{rw}.clue must look like v123 or v123.4")
            if r.get("where", "any") not in PLACES:
                errs.append(f"{rw}.where must be one of {PLACES}")
    return errs


def load_batches(data: Path) -> dict[str, dict[str, Any]]:
    """All batch files by id; an invalid file is returned with its problems instead of its questions."""
    out: dict[str, dict[str, Any]] = {}
    qdir = data / "questions"
    if not qdir.is_dir():
        return out
    for f in sorted(qdir.glob("*.json")):
        bid = f.stem
        if not BATCH_ID.match(bid):
            out[bid] = {
                "batch": bid,
                "title": bid,
                "problems": ["file name is not a valid batch id"],
                "questions": [],
            }
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # ValueError covers JSON and UTF-8 decode errors
            out[bid] = {"batch": bid, "title": bid, "problems": [f"unreadable: {e}"], "questions": []}
            continue
        problems = validate_batch(doc, bid)
        if problems:
            out[bid] = {
                "batch": bid,
                "title": doc.get("title", bid) if isinstance(doc, dict) else bid,
                "problems": problems,
                "questions": [],
            }
        else:
            out[bid] = doc
    return out


# ---------------------------------------------------------------- answers


def read_answers(data: Path, batch_id: str) -> list[dict[str, Any]]:
    f = data / "answers" / f"{batch_id}.jsonl"
    if not f.exists():
        return []
    recs = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a torn last line from a crash must not hide the rest
        if isinstance(rec, dict) and isinstance(rec.get("qids"), list):
            recs.append(rec)
    return recs


def question_hash(q: dict[str, Any]) -> str:
    """What the answer was given to: title, body and options. A rewritten question gets a new hash."""
    blob = json.dumps([q.get("title", ""), q.get("body", ""), q.get("options", [])], ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def current_answers(
    records: list[dict[str, Any]], qhashes: dict[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """The newest record per question id (records are in append order); a 'reopen' clears it.

    With ``qhashes`` (the batch's current questions), a record given to an earlier wording of a question does
    not count: the question is open again."""
    cur: dict[str, dict[str, Any]] = {}
    for rec in records:
        given = rec.get("qhash") if isinstance(rec.get("qhash"), dict) else {}
        for qid in rec["qids"]:
            if not isinstance(qid, str):
                continue
            if qhashes is not None and qid in given and given[qid] != qhashes.get(qid):
                continue
            if rec.get("type") == "reopen":
                cur.pop(qid, None)
            else:
                cur[qid] = rec
    return cur


def is_open(answer: dict[str, Any] | None) -> bool:
    return answer is None or answer.get("type") in OPEN_TYPES


_append_lock = threading.Lock()


def append_answer(data: Path, batches: dict[str, dict[str, Any]], payload: Any, place: str) -> dict[str, Any]:
    """Validate one answer from the page and append it; returns the stored record."""
    if not isinstance(payload, dict):
        raise DeskError("answer must be a JSON object")
    bid = payload.get("batch")
    if not isinstance(bid, str) or bid not in batches or batches[bid].get("problems"):
        raise DeskError("unknown or invalid batch")
    known = {q["id"] for q in batches[bid]["questions"]}
    qids = payload.get("qids")
    if not isinstance(qids, list) or not qids or not all(isinstance(q, str) and q in known for q in qids):
        raise DeskError("qids must name questions of this batch")
    if len(set(qids)) != len(qids):
        raise DeskError("qids repeat")
    typ = payload.get("type")
    if typ not in ANSWER_TYPES:
        raise DeskError(f"type must be one of {ANSWER_TYPES}")
    text = payload.get("text", "")
    option = payload.get("option")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise DeskError("text must be a string of at most 20000 characters")
    if option is not None and not isinstance(option, str):
        raise DeskError("option must be a string")
    if typ == "answer" and not text.strip() and not option:
        raise DeskError("an answer needs text or an option")
    if typ in ("rule", "approve_conditional") and not text.strip():
        raise DeskError(f"{typ} needs text")
    by_id = {q["id"]: q for q in batches[bid]["questions"]}
    seen = payload.get("qhash")
    if not isinstance(seen, dict) or any(seen.get(q) != question_hash(by_id[q]) for q in qids):
        raise DeskConflict("the question changed since the page loaded it; reload the page")
    if typ != "answer":
        option = None  # only an answer carries a choice
    if typ == "reopen":
        text = ""
    if option is not None and any(option not in by_id[q].get("options", []) for q in qids):
        raise DeskError("option is not offered by every selected question")
    rec = {
        "ts": _now(),
        "batch": bid,
        "qids": qids,
        "qhash": {q: question_hash(by_id[q]) for q in qids},
        "type": typ,
        "text": text.strip(),
        "option": option,
        "not_for_memory": bool(payload.get("not_for_memory", False)),
        "place": place,
        "by": "cemal",
    }
    adir = data / "answers"
    with _append_lock:
        adir.mkdir(parents=True, exist_ok=True)
        f = adir / f"{bid}.jsonl"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def batch_hashes(doc: dict[str, Any]) -> dict[str, str]:
    return {q["id"]: question_hash(q) for q in doc.get("questions", [])}


def batch_view(data: Path, doc: dict[str, Any]) -> dict[str, Any]:
    cur = current_answers(read_answers(data, doc["batch"]), batch_hashes(doc))
    qs = []
    for q in doc.get("questions", []):
        qs.append({**q, "qhash": question_hash(q), "answer": cur.get(q["id"])})
    view = {k: v for k, v in doc.items() if k != "questions"}
    view["questions"] = qs
    return view


def summary(data: Path, batches: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for bid, doc in batches.items():
        cur = current_answers(read_answers(data, bid), batch_hashes(doc))
        qs = doc.get("questions", [])
        open_q = [q for q in qs if is_open(cur.get(q["id"]))]
        rows.append(
            {
                "batch": bid,
                "title": doc.get("title", bid),
                "project": doc.get("project", ""),
                "created": doc.get("created", ""),
                "total": len(qs),
                "open": len(open_q),
                "open_by_place": {p: sum(1 for q in open_q if q.get("place", "any") == p) for p in PLACES},
                "problems": doc.get("problems", []),
            }
        )
    rows.sort(key=lambda r: (r["open"] == 0, r["created"]), reverse=False)
    return rows


# ---------------------------------------------------------------- local files


SECRET_NAME = re.compile(
    r"(env|token|secret|passw|cred|key|netrc|id_rsa|id_ed25519|known_hosts|^\.git)", re.I
)
SHOWABLE = {".md", ".txt", ".json", ".jsonl", ".toml", ".yaml", ".yml", ".diff", ".stat", ".log", ".csv"}


def _resolve(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = REPO / p
    return p.resolve()


def safe_local_path(data: Path, raw: str, listed: set[Path] | None = None) -> Path:
    """The file viewer shows a file only when a loaded batch lists it as a ref (or it lies in the desk data),
    it is under the repository's docs/ or the desk data, it has a text extension, and no part of its name
    looks like a secret. Deploy state, .env files and tokens must never reach the page."""
    if not raw or "\x00" in raw or raw.startswith("~"):
        raise DeskError("bad path")
    p = _resolve(raw)
    roots = [data.resolve(), (REPO / "docs").resolve()]
    root = next((r for r in roots if r in p.parents), None)
    if root is None:
        raise DeskError("only files under docs/ or the desk data can be shown")
    if root != roots[0] and (listed is None or p not in listed):
        raise DeskError("only files that a question lists can be shown")
    if p.suffix.lower() not in SHOWABLE:
        raise DeskError("only text files can be shown")
    if any(SECRET_NAME.search(part) for part in p.relative_to(root).parts):
        raise DeskError("this file name looks like a secret; open it yourself if you must")
    if not p.is_file():
        raise DeskError("not a file")
    if p.stat().st_size > 2_000_000:
        raise DeskError("file is larger than 2 MB")
    return p


def listed_paths(batches: dict[str, dict[str, Any]]) -> set[Path]:
    out: set[Path] = set()
    for doc in batches.values():
        for q in doc.get("questions", []):
            for r in q.get("refs", []):
                if isinstance(r.get("path"), str):
                    try:
                        out.add(_resolve(r["path"]))
                    except (OSError, ValueError, RuntimeError):
                        continue
    return out


# ---------------------------------------------------------------- server


class Desk:
    def __init__(self, data: Path, place: str, port: int) -> None:
        self.data = data
        self.place = place
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}


def make_handler(desk: Desk) -> type[http.server.BaseHTTPRequestHandler]:
    page = (HERE / "desk.html").read_text(encoding="utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "desk/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet: no paths or bodies in the terminal
            return

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'",
            )
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in desk.allowed_hosts

        def _token_ok(self) -> bool:
            got = self.headers.get("X-Desk-Token", "").encode("utf-8", "replace")
            return secrets.compare_digest(got, desk.token.encode())

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._get()
            except Exception:  # noqa: BLE001 - a bad request must get an answer, not kill the thread
                self._json(400, {"error": "bad request"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._post()
            except Exception:  # noqa: BLE001
                self._json(400, {"error": "bad request"})

        def _get(self) -> None:
            if not self._host_ok():
                return self._json(421, {"error": "wrong host"})
            url = urlparse(self.path)
            if url.path == "/":
                html = page.replace("__DESK_TOKEN__", desk.token).replace("__DESK_PLACE__", desk.place)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            batches = load_batches(desk.data)
            if url.path == "/api/batches":
                return self._json(200, {"place": desk.place, "batches": summary(desk.data, batches)})
            if url.path.startswith("/api/batch/"):
                bid = url.path.removeprefix("/api/batch/")
                if bid not in batches:
                    return self._json(404, {"error": "no such batch"})
                return self._json(200, batch_view(desk.data, batches[bid]))
            if url.path == "/api/file":
                if not self._token_ok():
                    return self._json(403, {"error": "bad token"})
                raw = parse_qs(url.query).get("path", [""])[0]
                try:
                    p = safe_local_path(desk.data, raw, listed_paths(batches))
                    text = p.read_text(encoding="utf-8", errors="replace")
                except (DeskError, OSError, RuntimeError) as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"path": str(p), "text": text})
            return self._json(404, {"error": "not found"})

        def _post(self) -> None:
            if not self._host_ok():
                return self._json(421, {"error": "wrong host"})
            if not self._token_ok():
                return self._json(403, {"error": "bad token"})
            if urlparse(self.path).path != "/api/answer":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > 200_000:
                return self._json(400, {"error": "bad length"})
            try:
                payload = json.loads(self.rfile.read(n))
                rec = append_answer(desk.data, load_batches(desk.data), payload, desk.place)
            except DeskConflict as e:
                return self._json(409, {"error": str(e)})
            except (ValueError, RecursionError) as e:  # DeskError, JSON and UTF-8 errors
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True, "record": rec})

    return Handler


def serve(data: Path, port: int, place: str) -> None:
    desk = Desk(data, place, port)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(desk))
    print(f"desk: http://127.0.0.1:{port}/  (place={place}, data={data})  Ctrl-C stops it", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="desk", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--place", choices=("home", "work"), default=default_place())
    for p in (s, sub.add_parser("check"), sub.add_parser("status")):
        p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    a = sub.add_parser("answers")
    a.add_argument("batch")
    a.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = ap.parse_args(argv)
    data: Path = args.data.expanduser().resolve()

    if args.cmd == "serve":
        (data / "questions").mkdir(parents=True, exist_ok=True)
        serve(data, args.port, args.place)
        return 0
    batches = load_batches(data)
    if args.cmd == "check":
        bad = 0
        for bid, doc in batches.items():
            probs = doc.get("problems", [])
            print(f"{'FAIL' if probs else 'ok  '} {bid}")
            for p in probs:
                print(f"     - {p}")
            bad += bool(probs)
        return 1 if bad else 0
    if args.cmd == "status":
        for r in summary(data, batches):
            print(f"{r['batch']:<40} open {r['open']:>3}/{r['total']:<3} {json.dumps(r['open_by_place'])}")
        return 0
    if args.cmd == "answers":
        if args.batch not in batches:
            print(f"no such batch: {args.batch}", file=sys.stderr)
            return 1
        cur = current_answers(read_answers(data, args.batch), batch_hashes(batches[args.batch]))
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
