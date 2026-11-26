"""Capture hook entry point (Claude Code and agy). Stdlib only; returns in well under 1 s.

Reads the hook JSON from stdin, applies the kill switch and the cwd -> project mapping (unmapped =
no-op), queues the input and starts the real work DETACHED (new session, no inherited pipes) so the
session exit / compaction / agent loop is never blocked. Any error is swallowed: exit code is always 0.

Two dialects, told apart by the key style of the input:

- Claude Code (snake_case ``hook_event_name``): SessionEnd / PreCompact; stdout stays empty.
- agy, Google's Antigravity CLI (camelCase ``hookEventName``): ``Stop`` fires at the end of every turn
  and queues a capture check of the conversation (the worker applies the size threshold);
  ``SessionStart`` only queues the idle flush of other conversations' tails. agy reads a JSON object
  from stdout, so ``{}`` is always printed (for Stop it means "let the agent stop"). The agent's
  directory is the input's ``cwd`` (the hook process itself runs in the hooks.json folder).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import Any

from hlmemo.capture import config as C

EVENTS = {"SessionEnd", "PreCompact"}
AGY_EVENTS = {"Stop", "SessionStart"}


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def is_agy(data: dict[str, Any] | None) -> bool:
    return isinstance(data, dict) and "hookEventName" in data and "hook_event_name" not in data


def _agy_cwd(data: dict[str, Any]) -> str:
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    ws = data.get("workspacePaths")
    return ws[0] if isinstance(ws, list) and ws and isinstance(ws[0], str) else ""


def normalize(data: dict[str, Any]) -> dict[str, Any] | None:
    """The queued record (the worker's input), or None when the event is not ours."""
    if is_agy(data):
        event = data.get("hookEventName")
        if event not in AGY_EVENTS:
            return None
        rec: dict[str, Any] = {
            "client": "agy",
            "mode": "stop" if event == "Stop" else "sweep",
            "hook_event_name": event,
            "session_id": data.get("conversationId"),
            "transcript_path": data.get("transcriptPath"),
            "cwd": _agy_cwd(data),
            "model_name": data.get("modelName") if isinstance(data.get("modelName"), str) else None,
            "reason": data.get("terminationReason"),
        }
        if event == "Stop" and (not rec["session_id"] or not rec["transcript_path"]):
            return None
        return rec
    if data.get("hook_event_name") not in EVENTS:
        return None
    if not data.get("session_id") or not data.get("transcript_path"):
        return None
    keys = ("session_id", "transcript_path", "cwd", "hook_event_name", "reason", "trigger")
    return {k: data.get(k) for k in keys}


def handle(raw: str, *, spawn: bool = True) -> str:
    """Returns what happened (``killed`` / ``unmapped`` / ``ignored`` / ``spawned``); never raises."""
    try:
        if C.killed():
            return "killed"
        data = _parse(raw)
        if data is None:
            return "ignored"
        if not is_agy(data) and data.get("hook_event_name") not in EVENTS:
            return "ignored"
        if is_agy(data) and data.get("hookEventName") not in AGY_EVENTS:
            return "ignored"
        cwd = _agy_cwd(data) if is_agy(data) else str(data.get("cwd") or "")
        cfg = C.load_config()
        if C.map_cwd(cfg, cwd) is None:
            return "unmapped"
        rec = normalize(data)
        if rec is None:
            return "ignored"
        q = C.state_dir() / "queue"
        q.mkdir(parents=True, exist_ok=True)
        f = q / f"{uuid.uuid4()}.json"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        if spawn:
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "hlmemo.capture.run", "--input", str(f)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=cwd or "/",
                close_fds=True,
            )
        return "spawned"
    except BaseException:  # noqa: BLE001
        return "ignored"


def main() -> int:
    try:
        raw = sys.stdin.read()
    except BaseException:  # noqa: BLE001
        raw = ""
    handle(raw)
    try:
        if is_agy(_parse(raw)):
            sys.stdout.write("{}\n")  # agy expects a JSON object; {} lets a Stop proceed
            sys.stdout.flush()
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
