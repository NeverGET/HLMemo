"""Claude Code SessionStart hook entry point: ``python -m hlmemo.brief.hook``.

Reads the hook JSON from stdin (``cwd``, ``session_id``, ``source`` = startup/resume/clear/compact),
applies the kill switch and the capture project mapping (unmapped = no-op), reads memory (read-only)
and prints ``{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <brief>}}``.

It must NEVER block or break a session: any error, timeout or empty result prints nothing and exits 0.
A watchdog thread ends the process after the hard wall clock (4 s; the registered hook timeout is 5)
whatever the main thread is doing (imports, keychain, a hung socket).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hlmemo.brief import config as BC
from hlmemo.capture import config as CC

EVENT = "SessionStart"
Fetcher = Callable[[str, BC.BriefConfig], Awaitable[Any]]


@dataclass
class Outcome:
    status: str  # killed | unmapped | ignored | empty | dryrun | injected | error:<Type> | timeout
    output: str | None = None  # the exact stdout (a JSON line) when something is to be injected
    tokens: int = 0
    sections: tuple[str, ...] = ()


def hook_output(brief_text: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": brief_text}}, ensure_ascii=False
    )


async def default_fetcher(slug: str, cfg: BC.BriefConfig) -> Any:
    from hlmemo.brief import fetch as F

    cm = F.open_call_factory(cfg.capture, timeout_s=BC.FETCH_S)
    if cm is None:  # no device token: the relay fallback runs an LLM, which this hook never does
        return None
    async with cm as call:
        return await asyncio.wait_for(F.gather_snapshot(call, slug), timeout=BC.FETCH_S)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", s)[:40] or "nosession"


def write_dryrun(directory: str, data: dict[str, Any], brief: Any, ms: int) -> Path:
    d = Path(directory).expanduser()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    sid = _safe(str(data.get("session_id") or ""))[:8]
    stem = f"{sid}-{_safe(str(data.get('source') or ''))}-{int(time.time())}"
    path = d / f"{stem}.brief.txt"
    path.write_text(brief.text + "\n", encoding="utf-8")
    meta = {
        "tokens": brief.tokens,
        "sections": brief.sections,
        "excluded": [list(x) for x in brief.excluded],
        "ms": ms,
        "chars": len(brief.text),
    }
    (d / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def handle(
    raw: str,
    *,
    fetcher: Fetcher | None = None,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> Outcome:
    """Never raises. ``fetcher(slug, cfg)`` returns a ``Snapshot`` (or None); injectable for tests."""
    t0 = time.monotonic()
    e = env if env is not None else os.environ
    try:
        if BC.killed(e):
            return Outcome("killed")
        data = json.loads(raw or "{}")
        if not isinstance(data, dict) or data.get("hook_event_name") != EVENT:
            return Outcome("ignored")
        cfg = BC.load_config(config_path)
        if str(data.get("source") or "startup") not in cfg.sources:
            return Outcome("ignored")
        slug = BC.map_cwd(cfg, str(data.get("cwd") or ""))
        if slug is None:
            return Outcome("unmapped")
        from hlmemo.brief.assemble import assemble

        snap = asyncio.run(asyncio.wait_for((fetcher or default_fetcher)(slug, cfg), timeout=BC.FETCH_S))
        if snap is None:
            return Outcome("empty")
        brief = assemble(snap, budget=cfg.budget_tokens, max_age_days=cfg.decisions_max_age_days)
        if brief is None:
            return Outcome("empty")
        dry = e.get(BC.DRYRUN_ENV, "").strip()
        if dry:
            write_dryrun(dry, data, brief, int((time.monotonic() - t0) * 1000))
            return Outcome("dryrun", None, brief.tokens, tuple(brief.sections))
        return Outcome("injected", hook_output(brief.text), brief.tokens, tuple(brief.sections))
    except TimeoutError:
        return Outcome("timeout")
    except BaseException as exc:  # noqa: BLE001 - fail open, always
        return Outcome(f"error:{type(exc).__name__}")


def log_line(o: Outcome, ms: int) -> None:
    """One line per run, no content. Never raises."""
    try:
        d = CC.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (d / "brief.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {o.status} {ms}ms tokens={o.tokens} sections={','.join(o.sections)}\n")
    except BaseException:  # noqa: BLE001
        pass


def start_watchdog(seconds: float) -> threading.Timer:
    """End the process silently (exit 0) after ``seconds`` whatever the main thread is doing."""
    t = threading.Timer(seconds, lambda: os._exit(0))
    t.daemon = True
    t.start()
    return t


def main() -> int:
    t0 = time.monotonic()
    watchdog = start_watchdog(BC.wall_seconds())
    try:
        try:
            raw = sys.stdin.read()
        except BaseException:  # noqa: BLE001
            raw = ""
        outcome = handle(raw)
        watchdog.cancel()
        if outcome.output:
            sys.stdout.write(outcome.output + "\n")
            sys.stdout.flush()
        log_line(outcome, int((time.monotonic() - t0) * 1000))
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
