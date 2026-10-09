"""Claude Code SessionStart hook entry point: ``python -P -m hlmemo.brief.hook``.

Reads the hook JSON from stdin (``cwd``, ``session_id``, ``source`` = startup/resume/clear/compact),
applies the kill switch and the capture project mapping (unmapped = no-op) and, for a mapped project,
prints ``{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}`` where
<text> is:

1. the "HLMemo mode" protocol digest (``protocol_digest.txt``, a byte copy of the ``text`` block in
   ``docs/protocol/HLMEMO-PROTOCOL.md`` section 6) with ``<slug>`` filled in, unless
   ``HLM_BRIEF_DIGEST=off``; then
2. the memory brief (read-only memory calls), or, when the digest is on and no brief can be produced
   (timeout, error, nothing to show), ONE line saying the brief is unavailable and why. With the digest
   off a missing brief prints nothing, as before.

The memory calls run under ONE deadline (``FETCH_S``: session open, every read, close). When it hits
after the card and the queries came back, the brief shows what was verified in time plus a one-line
note. A fast retryable network/server failure is retried once while ``RETRY_MIN_S`` remain.

Global install (``~/.claude/settings.json``): until the cwd is known to be mapped the hook imports only
stdlib-light modules, opens no socket and prints nothing. ``-P`` keeps the session cwd off ``sys.path``,
so a project's own ``json.py`` (or any stdlib-named file) never runs inside the hook.

It must NEVER block or break a session: any error, timeout or empty result exits 0. A watchdog thread
ends the process after the hard wall clock (8 s; the registered hook timeout is 10) whatever the main
thread is doing (imports, keychain, a hung socket). Once the cwd is mapped the watchdog first prints the
digest with the "unavailable" line, so even a hung fetch still injects the rules.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hlmemo.brief import config as BC
from hlmemo.capture import config as CC

# Nothing heavy (asyncio, the fetch/assemble modules, tiktoken, the hlm client) is imported at module level:
# the unmapped path of a global install stays an interpreter start plus one small TOML read.

EVENT = "SessionStart"
DIGEST_RESOURCE = "protocol_digest.txt"
SLUG_MARK = "<slug>"
#: Claude Code caps a hook's additionalContext at 10,000 characters (beyond that it saves the text to a
#: file and shows the model only a 2,000-character preview). Digest + brief stay below, with a margin.
CONTEXT_CHARS = 9500
Fetcher = Callable[[str, BC.BriefConfig], Awaitable[Any]]
AUTH_CODES = frozenset({"E_AUTH", "E_DEVICE_PENDING"})
#: the last line of a brief the deadline cut short (the card and the queries came back, not every detail)
PARTIAL_NOTE = "(cut short by the {s:g} s time limit: items not verified in time are left out)"


@dataclass
class Outcome:
    #: killed | unmapped | ignored | dryrun, or the brief's own status: injected | empty | timeout |
    #: error:auth | error:network | error:server | error:<Type> (with the digest on, empty/timeout/error
    #: still carry an output; a timeout after the queries may carry a partial brief)
    status: str
    output: str | None = None  # the exact stdout (a JSON line) when something is to be injected
    tokens: int = 0  # the brief's tokens (0: no brief)
    sections: tuple[str, ...] = ()  # "Digest" first when the digest is part of the output
    digest: bool = False
    chars: int = 0  # len(additionalContext)
    slug: str = ""  # the mapped project
    #: why: the stage a timeout cut (session | queries | details), the error code (auth/server) or the
    #: transport error's type (network)
    cause: str = ""


def hook_output(text: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": text}}, ensure_ascii=False
    )


def digest_template() -> str:
    """The protocol digest with its ``<slug>`` placeholders (package data)."""
    from importlib.resources import files

    return files("hlmemo.brief").joinpath(DIGEST_RESOURCE).read_text(encoding="utf-8")


def digest(slug: str) -> str:
    return digest_template().replace(SLUG_MARK, slug).rstrip("\n")


def _digest_or_empty(slug: str, env: Mapping[str, str]) -> str:
    """The filled digest, or "" when switched off or unreadable (the brief still runs). Never raises."""
    if BC.digest_off(env):
        return ""
    try:
        return digest(slug)
    except Exception:  # noqa: BLE001 - a broken install must not cost the brief
        return ""


def _token(s: str) -> str:
    """A code or type name, safe for one log field or one line of context."""
    return re.sub(r"[^A-Za-z0-9._:-]", "", s)[:64]


def unavailable(slug: str, status: str, cause: str = "") -> str:
    """The one line that stands in for a brief that could not be produced, with the reason."""
    if status == "timeout":
        why = f"timed out after {BC.FETCH_S:g} s; memory.query still works"
    elif status == "error:auth" and cause == "E_DEVICE_PENDING":
        why = "this device is not approved yet"
    elif status == "error:auth":
        why = "the server refused this device's token"
    elif status == "error:network":
        why = "server unreachable"
    elif status == "error:server":
        why = f"server error {_token(cause)}".rstrip()
    elif status.startswith("error"):
        why = "the memory read failed"
    else:
        why = "nothing to show: no device token, or no current card or reviewed lessons yet"
    return (
        f"# Memory brief: project {slug}: unavailable this session ({why}). "
        "Use memory.query (token_budget 3000) for your task."
    )


def failure(exc: BaseException) -> tuple[str, str]:
    """``(status, cause)`` of a failed fetch: ``timeout``; ``error:auth`` and ``error:server`` with the
    code; ``error:network`` (no answer from the server) with the transport error's type; anything else
    ``error:<Type>``. Never raises."""
    if isinstance(exc, TimeoutError):
        return "timeout", ""
    if isinstance(exc, ConnectionError):
        return "error:network", type(exc).__name__
    try:
        from hlmemo.cli.mcp_client import ToolCallError
    except Exception:  # noqa: BLE001 - the client cannot load, so it raised nothing
        return f"error:{type(exc).__name__}", ""
    if not isinstance(exc, ToolCallError):
        return f"error:{type(exc).__name__}", ""
    if exc.code in AUTH_CODES:
        return "error:auth", exc.code
    if exc.transport == "TimeoutError":
        return "timeout", ""
    if exc.transport:
        return "error:network", exc.transport
    return "error:server", exc.code


def _retryable(status: str, exc: BaseException) -> bool:
    """Network or server failures the client/server flagged retryable (never auth, never a timeout)."""
    return status in ("error:network", "error:server") and getattr(exc, "retryable", False) is True


def compose(head: str, body: str) -> str:
    return f"{head}\n\n{body}" if head and body else head or body


def fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


async def default_fetcher(slug: str, cfg: BC.BriefConfig, partial: Any = None) -> Any:
    """The snapshot, read in one MCP session (no timer here: ``fetch_snapshot`` owns the deadline).
    ``partial`` (a ``Snapshot``) is filled as the reads come back, for a deadline that cuts them."""
    from hlmemo.brief import fetch as F

    cm = F.open_call_factory(cfg.capture, timeout_s=BC.FETCH_S)
    if cm is None:  # no device token: the relay fallback runs an LLM, which this hook never does
        return None
    async with cm as call:
        return await F.gather_snapshot(call, slug, into=partial)


async def fetch_snapshot(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None) -> tuple[str, str, Any]:
    """``(status, cause, Snapshot | None)``, status = ok | empty | timeout | error:... (``failure``),
    under ONE deadline ``BC.FETCH_S`` from now. A retryable network/server failure is retried once while
    ``BC.RETRY_MIN_S`` remain. When the deadline cuts the default fetcher after the card and the queries,
    the snapshot of what was verified by then comes back with status ``timeout`` (cause: the stage)."""
    import asyncio

    from hlmemo.brief import fetch as F

    loop = asyncio.get_running_loop()
    deadline = loop.time() + BC.FETCH_S
    partial: Any = None
    try:
        async with asyncio.timeout_at(deadline):
            retried = False
            while True:
                partial = F.Snapshot(project=slug) if fetcher is None else None
                try:
                    snap = await (fetcher(slug, cfg) if fetcher else default_fetcher(slug, cfg, partial))
                    return ("ok", "", snap) if snap is not None else ("empty", "", None)
                except Exception as exc:
                    if loop.time() >= deadline:  # the deadline's cancellation, turned into an error
                        raise TimeoutError from exc
                    status, cause = failure(exc)
                    if retried or not _retryable(status, exc) or deadline - loop.time() < BC.RETRY_MIN_S:
                        return status, cause, None
                    retried = True
    except TimeoutError:
        stage = partial.stage if partial is not None else ""
        return "timeout", stage, F.settle(partial) if stage == "details" else None


def build_brief(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None, room: int) -> tuple[str, str, Any]:
    """``(status, cause, Brief | None)``; status = injected | empty | timeout | error:... (``failure``).
    A timeout that cut only the details still gives a brief of what was verified, ending in
    ``PARTIAL_NOTE``. The brief keeps its token budget AND fits in ``room`` characters (what the digest
    leaves). Never raises."""
    try:
        import asyncio

        from hlmemo.brief.assemble import assemble, count_tokens

        status, cause, snap = asyncio.run(fetch_snapshot(slug, cfg, fetcher))
        if snap is None:
            return status, cause, None
        note = PARTIAL_NOTE.format(s=BC.FETCH_S) if status == "timeout" else ""
        budget = cfg.budget_tokens - (count_tokens(note) + 1 if note else 0)
        room -= (len(note) + 1) if note else 0

        def counter(text: str) -> int:  # over the room = over budget: assemble drops lines until it fits
            n = count_tokens(text)
            return n if len(text) <= room else max(n, budget + 1)

        brief = assemble(
            snap,
            budget=budget,
            max_age_days=cfg.decisions_max_age_days,
            include_auto=cfg.include_auto,
            decisions_max_lines=cfg.decisions_max_lines,
            lesson_body_chars=cfg.lesson_body_chars,
            counter=counter,
        )
        if brief is None:
            return ("timeout" if note else "empty"), cause, None
        if len(brief.text) > room:  # last resort; the drop loop normally prevents it
            brief.text, brief.truncated = fit(brief.text, room), True
        if note:
            brief.text += "\n" + note
        brief.tokens = count_tokens(brief.text)
        return ("timeout" if note else "injected"), cause, brief
    except BaseException as exc:  # noqa: BLE001 - fail open, always
        return (*failure(exc), None)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", s)[:40] or "nosession"


def write_dryrun(directory: str, data: dict[str, Any], text: str, meta: dict[str, Any]) -> Path:
    """The would-be additionalContext (``.brief.txt``) and its metadata (``.meta.json``)."""
    d = Path(directory).expanduser()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    sid = _safe(str(data.get("session_id") or ""))[:8]
    stem = f"{sid}-{_safe(str(data.get('source') or ''))}-{int(time.time())}"
    path = d / f"{stem}.brief.txt"
    path.write_text(text + "\n", encoding="utf-8")
    (d / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def handle(
    raw: str,
    *,
    fetcher: Fetcher | None = None,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
    arm: Callable[[str], None] | None = None,
) -> Outcome:
    """Never raises. ``fetcher(slug, cfg)`` returns a ``Snapshot`` (or None); injectable for tests.
    ``arm(line)`` receives, as soon as the cwd is mapped, the digest-only output that the watchdog prints
    if it has to end the process before the brief is ready."""
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

        # Mapped: from here on the digest is always injected (unless switched off), whatever the brief does.
        head = _digest_or_empty(slug, e)
        dry = e.get(BC.DRYRUN_ENV, "").strip()
        if head and arm is not None and not dry:
            arm(hook_output(compose(head, unavailable(slug, "timeout"))))
        room = CONTEXT_CHARS - (len(head) + 2 if head else 0)
        status, cause, brief = build_brief(slug, cfg, fetcher, room)
        body = brief.text if brief is not None else (unavailable(slug, status, cause) if head else "")
        text = compose(head, body)
        if not text:
            return Outcome(status, slug=slug, cause=cause)
        tokens = brief.tokens if brief is not None else 0
        sections = (("Digest",) if head else ()) + (tuple(brief.sections) if brief is not None else ())
        if dry:
            meta = {
                "status": status,
                "cause": cause,
                "digest": bool(head),
                "tokens": tokens,
                "sections": list(sections),
                "excluded": [list(x) for x in (brief.excluded if brief is not None else [])],
                "ms": int((time.monotonic() - t0) * 1000),
                "chars": len(text),
            }
            write_dryrun(dry, data, text, meta)
            return Outcome("dryrun", None, tokens, sections, bool(head), len(text), slug, cause)
        return Outcome(status, hook_output(text), tokens, sections, bool(head), len(text), slug, cause)
    except BaseException as exc:  # noqa: BLE001 - fail open, always
        return Outcome(f"error:{type(exc).__name__}")


def log_line(o: Outcome, ms: int) -> None:
    """One line per run, no content. Never raises."""
    try:
        d = CC.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (d / "brief.log").open("a", encoding="utf-8") as fh:
            fh.write(
                f"{stamp} {o.status} {ms}ms slug={_token(o.slug) or '-'} cause={_token(o.cause) or '-'} "
                f"tokens={o.tokens} chars={o.chars} digest={int(o.digest)} sections={','.join(o.sections)}\n"
            )
    except BaseException:  # noqa: BLE001
        pass


class Stdout:
    """The hook's single stdout line, written at most once: by the main thread, or by the watchdog (the
    armed digest-only fallback) just before it ends the process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = False
        self._fallback: str | None = None

    def arm(self, line: str) -> None:
        self._fallback = line

    def write(self, line: str) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def last_words(self) -> None:
        """Watchdog side: print the armed fallback unless the main thread already printed. Never raises."""
        try:
            if not self._lock.acquire(timeout=0.25):
                return
            if self._done or not self._fallback:
                return
            self._done = True
            data = (self._fallback + "\n").encode("utf-8")
            while data:
                data = data[os.write(1, data) :]
        except BaseException:  # noqa: BLE001
            pass


def start_watchdog(seconds: float, before_exit: Callable[[], None] | None = None) -> threading.Timer:
    """End the process (exit 0) after ``seconds`` whatever the main thread is doing; ``before_exit``
    (e.g. ``Stdout.last_words``) runs first."""

    def fire() -> None:
        try:
            if before_exit is not None:
                before_exit()
        finally:
            os._exit(0)

    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    return t


def main() -> int:
    t0 = time.monotonic()
    out = Stdout()
    watchdog = start_watchdog(BC.wall_seconds(), out.last_words)
    try:
        try:
            raw = sys.stdin.read()
        except BaseException:  # noqa: BLE001
            raw = ""
        outcome = handle(raw, arm=out.arm)
        watchdog.cancel()
        if outcome.output:
            out.write(outcome.output)
        log_line(outcome, int((time.monotonic() - t0) * 1000))
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
