"""Compose the ``memory.call_the_day`` payload and write it (direct hlm client path, relay fallback).

The session note is composed from PROGRAMMATIC facts (date, session, commits, files: exact) plus the
validated LLM summary, plus an explicit provenance line. No card update is ever sent.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hlmemo.capture.config import CLIENT_NAME, CaptureConfig
from hlmemo.capture.reduce import Reduced, commit_rows
from hlmemo.capture.scrub import ScrubReport, scrub_text
from hlmemo.capture.summarize import Summary

NS = uuid.UUID("6c1f5c0e-7d3a-5b7e-9a51-4c4a5d6f8e11")  # fixed namespace for capture session keys
FUTURE_SLACK = timedelta(minutes=5)  # server rule: occurred_at must not be > 5 min in the future


def session_key(claude_session_id: str, slug: str, segment: int = 0) -> str:
    """Stable uuid5 of (Claude session id, project slug[, segment]). Segment 0 (the common, single-write
    session) has no suffix, so its key is exactly ``uuid5(ns, "<sid>:<slug>")``."""
    name = f"{claude_session_id}:{slug}" + (f":seg{segment}" if segment else "")
    return str(uuid.uuid5(NS, name))


def occurred_at(last_ts: str | None, now: datetime | None = None) -> str:
    """The session's last transcript timestamp, never later than now (server: <= now + 5 min)."""
    now = now or datetime.now(UTC)
    when = now
    if last_ts:
        try:
            when = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            when = now
    if when > now:
        when = now
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(last_ts: str | None, first_ts: str | None) -> str:
    return ((last_ts or first_ts or datetime.now(UTC).isoformat())[:10]) or "unknown-date"


def _facts_block(red: Reduced, *, sid: str, segment: int, event: str, model: str | None) -> str:
    span = f"{(red.first_ts or '?')[:16]} to {(red.last_ts or '?')[:16]} UTC"
    head = [
        "AUTO-CAPTURED session note (claude-code capture hook); "
        + (f"summarised by {model}" if model else "NO summary (minimal note)")
        + "; not reviewed by the owner.",
        f"- Claude session: {sid}"
        + (f" (segment {segment}, {event})" if segment or event != "SessionEnd" else ""),
        f"- Span: {span}; owner messages: {red.owner_messages}",
    ]
    rows = commit_rows(red, 25)
    if rows:
        head.append("- Commits made in this span:\n" + "\n".join("  " + r for r in rows))
    else:
        head.append("- Commits made in this span: none seen")
    if red.files:
        shown = ", ".join(red.files[:25])
        more = f" (+{len(red.files) - 25} more)" if len(red.files) > 25 else ""
        head.append(f"- Files edited or written via editor tools (not exhaustive): {shown}{more}")
    return "\n".join(head)


def compose(
    red: Reduced,
    summary: Summary | None,
    *,
    sid: str,
    segment: int,
    event: str,
    model: str,
    report: ScrubReport | None = None,
) -> dict[str, Any]:
    """notes / decisions / lessons (all scrubbed again: the summary is model output)."""
    date = _date(red.last_ts, red.first_ts)
    facts = _facts_block(red, sid=sid, segment=segment, event=event, model=model if summary else None)
    if summary is None:
        tail = "The automatic summary was unavailable; only the facts above are recorded."
        notes = f"Session {date}\n\n{facts}\n\n{tail}"
        return {"notes": scrub_text(notes, report), "decisions": [], "lessons": []}
    body = summary.notes
    if summary.uncertain:
        body += "\n\n## Uncertain / unverified\n" + "\n".join(f"- {u}" for u in summary.uncertain)
    notes = f"Session {date}\n\n{facts}\n\n{body}"
    lessons = []
    for ls in summary.lessons:
        lb = (
            f'{ls["body"]}\n\nEvidence (verbatim from the session): "{ls["evidence"]}"\n'
            f"Source: auto-captured from Claude session {sid[:8]}, {date}; unreviewed."
        )
        lessons.append(
            {
                "title": scrub_text(ls["title"], report)[:200] or "Lesson",
                "body": scrub_text(lb, report),
                "tags": sorted({*ls["tags"], "auto-capture"})[:32],
            }
        )
    return {
        "notes": scrub_text(notes, report),
        "decisions": [d for d in (scrub_text(x, report).strip() for x in summary.decisions) if d],
        "lessons": [ls for ls in lessons if ls["body"].strip()],
    }


def build_payload(
    composed: dict[str, Any],
    *,
    slug: str,
    claude_sid: str,
    segment: int,
    last_ts: str | None,
    request_id: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project": slug,
        "request_id": request_id,
        "session_id": session_key(claude_sid, slug, segment),
        "client": CLIENT_NAME,
        "occurred_at": occurred_at(last_ts),
        "notes": composed["notes"],
    }
    if composed["decisions"]:
        payload["decisions"] = composed["decisions"]
    if composed["lessons"]:
        payload["lessons"] = composed["lessons"]
    return payload


def validate_payload(payload: dict[str, Any]) -> None:
    """Shape/limit check with the server's own request model (raises on violation)."""
    from hlmemo.core.write_models import CloseRequest

    CloseRequest.model_validate(payload)


# --------------------------------------------------------------------------- writers


@dataclass
class WriteOutcome:
    status: str  # ok | already_closed | error
    path: str = ""  # direct | relay | dry-run
    detail: str = ""


def write_direct(payload: dict[str, Any], cfg: CaptureConfig, timeout_s: float = 60.0) -> WriteOutcome | None:
    """The hlm client path: server/device from the hlm client config (./hlm.toml, HLM_* env), token from
    HLM_DEVICE_TOKEN / OS keychain / credentials file (never printed). None = not feasible (no token)."""
    try:
        from hlmemo.cli import credentials
        from hlmemo.cli.client_config import default_device_name, resolve_client_config
        from hlmemo.cli.mcp_client import TOOL_CALL_THE_DAY, MemoryClient, ToolCallError
    except Exception as exc:  # noqa: BLE001
        return WriteOutcome("error", "direct", f"import_failed:{type(exc).__name__}")
    try:
        ccfg = resolve_client_config(server_url=cfg.server_url, device_name=cfg.device_name)
        token = credentials.load_token(ccfg.server_url, ccfg.device_name or default_device_name())
    except Exception as exc:  # noqa: BLE001
        return WriteOutcome("error", "direct", f"config_failed:{type(exc).__name__}")
    if not token:
        return None
    try:
        MemoryClient(ccfg.mcp, token, timeout_s=timeout_s).call(TOOL_CALL_THE_DAY, payload)
    except ToolCallError as exc:
        if exc.code == "E_SESSION_CLOSED":
            return WriteOutcome("already_closed", "direct", exc.code)
        return WriteOutcome("error", "direct", exc.code)
    except Exception as exc:  # noqa: BLE001
        return WriteOutcome("error", "direct", f"call_failed:{type(exc).__name__}")
    return WriteOutcome("ok", "direct")


def write_relay(payload: dict[str, Any], cfg: CaptureConfig, timeout_s: int = 240) -> WriteOutcome:
    """Fallback: ``claude -p`` limited to the one MCP tool, relaying the (already scrubbed) payload."""
    prompt = (
        "Call the tool memory_call_the_day (MCP server hlm) exactly once, passing EXACTLY this JSON object "
        "as its arguments, unchanged. Then reply with only the tool's JSON result.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    cmd = [
        cfg.claude_bin, "-p", "--model", cfg.model,
        "--tools", "", "--allowed-tools", "mcp__hlm__memory_call_the_day",
        "--disable-slash-commands", "--no-session-persistence",
        "--output-format", "json",
    ]  # fmt: skip
    env = dict(os.environ)
    env["HLM_CAPTURE"] = "off"
    try:
        with tempfile.TemporaryDirectory(prefix="hlm-capture-") as tmp:
            proc = subprocess.run(  # noqa: S603
                cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, cwd=tmp, env=env
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return WriteOutcome("error", "relay", f"relay_failed:{type(exc).__name__}")
    if proc.returncode != 0:
        return WriteOutcome("error", "relay", f"relay_exit_{proc.returncode}")
    out = proc.stdout
    if "E_SESSION_CLOSED" in out:
        return WriteOutcome("already_closed", "relay", "E_SESSION_CLOSED")
    if payload["session_id"] in out and "session_note" in out.lower():
        return WriteOutcome("ok", "relay")
    return WriteOutcome("error", "relay", "relay_unverified")


def write_dry(payload: dict[str, Any], out_dir: str, *, name: str) -> WriteOutcome:
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return WriteOutcome("ok", "dry-run", path)


def payload_text(payload: dict[str, Any]) -> str:
    """Every free-text field of the payload, one per line group (what a residual-secret scan must see)."""
    parts = [payload.get("notes", ""), *payload.get("decisions", [])]
    for ls in payload.get("lessons", []):
        parts += [ls.get("title", ""), ls.get("body", ""), *ls.get("tags", [])]
    return "\n".join(parts)
