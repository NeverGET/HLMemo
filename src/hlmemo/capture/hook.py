"""Claude Code SessionEnd / PreCompact hook entry point. Stdlib only; returns in well under 1 s.

Reads the hook JSON from stdin, applies the kill switch and the cwd -> project mapping (unmapped =
no-op), queues the input and starts the real work DETACHED (new session, no inherited pipes) so the
session exit / compaction is never blocked. Any error is swallowed: exit code is always 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

from hlmemo.capture import config as C

EVENTS = {"SessionEnd", "PreCompact"}


def handle(raw: str, *, spawn: bool = True) -> str:
    """Returns what happened (``killed`` / ``unmapped`` / ``ignored`` / ``spawned``); never raises."""
    try:
        if C.killed():
            return "killed"
        data = json.loads(raw or "{}")
        if not isinstance(data, dict) or data.get("hook_event_name") not in EVENTS:
            return "ignored"
        cfg = C.load_config()
        if C.map_cwd(cfg, str(data.get("cwd") or "")) is None:
            return "unmapped"
        if not data.get("session_id") or not data.get("transcript_path"):
            return "ignored"
        q = C.state_dir() / "queue"
        q.mkdir(parents=True, exist_ok=True)
        f = q / f"{uuid.uuid4()}.json"
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    k: data.get(k)
                    for k in ("session_id", "transcript_path", "cwd", "hook_event_name", "reason", "trigger")
                },
                fh,
            )
        if spawn:
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "hlmemo.capture.run", "--input", str(f)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(data.get("cwd") or "/"),
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
