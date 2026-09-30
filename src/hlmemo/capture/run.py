"""The detached worker: lock, per-session state, the pipeline. Never raises; always exits 0.

Idempotency (PreCompact checkpoints + SessionEnd + resume): ``call_the_day`` closes a ``session_id``
once per project, so a checkpoint cannot be revised. The worker therefore writes DISJOINT SEGMENTS of
the transcript: state remembers how many transcript lines are already captured; every run captures
only the lines after that offset under the segment's own stable key. A session that never compacts
has exactly one segment (key = uuid5(claude session id + slug)). A PreCompact checkpoint is a segment
boundary, so very long sessions are summarised piece by piece (each piece gets its own 40k-char
window: nothing is lost to the cap) and nothing is ever duplicated. A retry after a crash resends the
stored pending payload unchanged (same request_id and key), and E_SESSION_CLOSED means "already
written".
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from hlmemo.capture import config as C
from hlmemo.capture import reduce as R
from hlmemo.capture import summarize as S
from hlmemo.capture import write as W
from hlmemo.capture.scrub import ScrubReport, residual_findings, scrub_text

LOCK_TIMEOUT_S = 900.0

SummarizeFn = Callable[[str, str, str, C.CaptureConfig], "tuple[dict[str, Any] | None, S.LlmRun]"]


def _default_summarize(
    text: str, cwd: str, date: str, cfg: C.CaptureConfig
) -> tuple[dict[str, Any] | None, S.LlmRun]:
    run = S.run_claude(
        S.build_user_prompt(text, cwd=cwd, date=date), model=cfg.model, claude_bin=cfg.claude_bin
    )
    return run.data, run


# --------------------------------------------------------------------------- lock + state


@contextlib.contextmanager
def session_lock(root: Path, sid: str, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[bool]:
    """Exclusive per-session lock (flock: released by the OS if the worker dies). Waits up to
    ``timeout_s`` (a PreCompact worker may still run when SessionEnd fires); yields False on timeout."""
    d = root / "locks"
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / f"{sid}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    got = False
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.2)
        yield got
    finally:
        if got:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _state_path(root: Path, sid: str) -> Path:
    return root / "sessions" / f"{sid}.json"


def load_state(root: Path, sid: str) -> dict[str, Any]:
    try:
        v = json.loads(_state_path(root, sid).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(root: Path, sid: str, state: dict[str, Any]) -> None:
    p = _state_path(root, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, p)


# --------------------------------------------------------------------------- pipeline


def _send(payload: dict[str, Any], cfg: C.CaptureConfig, dry_dir: str | None, name: str) -> W.WriteOutcome:
    if dry_dir:
        return W.write_dry(payload, dry_dir, name=name)
    out = W.write_direct(payload, cfg)
    if out is not None and out.status != "error":
        return out
    if cfg.relay_fallback:
        rel = W.write_relay(payload, cfg)
        if rel.status != "error" or out is None:
            return rel
    return out or W.WriteOutcome("error", "none", "no_writer")


def process(
    hook: dict[str, Any],
    *,
    cfg: C.CaptureConfig | None = None,
    dry_dir: str | None = None,
    summarize_fn: SummarizeFn | None = None,
    send_fn: Callable[[dict[str, Any], C.CaptureConfig, str | None, str], W.WriteOutcome] | None = None,
    lock_timeout_s: float = LOCK_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one capture. Returns a stats dict (``status`` in: killed, unmapped, bad_input, locked,
    skip_small, nothing_new, ok, error). No content is ever logged or returned except the payload in
    dry-run mode (written to a 0600 file)."""
    t0 = time.monotonic()
    cfg = cfg or C.load_config()
    if C.killed():
        return {"status": "killed"}
    cwd = str(hook.get("cwd") or "")
    slug = C.map_cwd(cfg, cwd)
    if slug is None:
        return {"status": "unmapped"}
    sid = str(hook.get("session_id") or "")
    tpath = str(hook.get("transcript_path") or "")
    event = str(hook.get("hook_event_name") or "SessionEnd")
    if not sid or not tpath or not os.path.isfile(tpath) or not all(ch.isalnum() or ch in "-_" for ch in sid):
        return {"status": "bad_input"}
    root = Path(dry_dir) / ".state" if dry_dir else C.state_dir()
    summarize_fn = summarize_fn or _default_summarize
    send_fn = send_fn or _send
    stats: dict[str, Any] = {"event": event, "slug": slug, "sid": sid[:8]}
    with session_lock(root, sid, lock_timeout_s) as got:
        if not got:
            return {**stats, "status": "locked"}
        state = load_state(root, sid)
        seg = int(state.get("segments", 0))
        start = int(state.get("captured_lines", 0))
        pending = state.get("pending")
        if isinstance(pending, dict) and isinstance(pending.get("payload"), dict):
            out = send_fn(pending["payload"], cfg, dry_dir, f"{sid}-seg{seg}")
            stats["resent_pending"] = True
            if out.status == "error":
                return {**stats, "status": "error", "detail": out.detail, "latency_s": time.monotonic() - t0}
            seg += 1
            start = int(pending.get("end_line", start))
            state = {"segments": seg, "captured_lines": start}
            save_state(root, sid, state)
        red = R.read_segment(tpath, start=start, cap=cfg.cap_chars, cwd_hint=cwd)
        stats.update(owner=red.owner_messages, segment=seg, reduced_chars=len(red.text))
        if red.end_line <= start or red.segments_total == 0:
            return {**stats, "status": "nothing_new", "latency_s": time.monotonic() - t0}
        if red.owner_messages < cfg.min_owner_messages:
            return {**stats, "status": "skip_small", "latency_s": time.monotonic() - t0}
        rep = ScrubReport()
        text = scrub_text(red.text, rep)
        stats.update(lines_dropped=rep.lines_dropped, emails_masked=rep.emails_masked)
        first, last = (red.first_ts or "")[:10], (red.last_ts or red.first_ts or "")[:10]
        date = last if first == last or not first else f"{first} to {last}"
        data, run = summarize_fn(text, cwd, date, cfg)
        stats.update(
            llm_latency_s=round(run.latency_s, 2),
            cost_usd=run.cost_usd,
            in_tokens=run.input_tokens,
            out_tokens=run.output_tokens,
        )
        summary: S.Summary | None = None
        if data is not None:
            try:
                summary = S.validate_summary(data, text)
                stats["dropped"] = summary.dropped
            except S.SummaryInvalid as exc:
                stats["llm_error"] = f"invalid:{exc}"[:80]
        else:
            stats["llm_error"] = run.error or "no_data"
        composed = W.compose(red, summary, sid=sid, segment=seg, event=event, model=cfg.model, report=rep)
        payload = W.build_payload(
            composed,
            slug=slug,
            claude_sid=sid,
            segment=seg,
            last_ts=red.last_ts,
            request_id=str(uuid.uuid4()),
        )
        leaks = residual_findings(W.payload_text(payload))
        if leaks:  # defence in depth: never write something the scrub would still flag
            return {
                **stats,
                "status": "error",
                "detail": "residual_secret",
                "latency_s": time.monotonic() - t0,
            }
        try:
            W.validate_payload(payload)
        except Exception as exc:  # noqa: BLE001
            return {**stats, "status": "error", "detail": f"payload_invalid:{type(exc).__name__}"}
        stats.update(
            decisions=len(payload.get("decisions", [])),
            lessons=len(payload.get("lessons", [])),
            llm="ok" if summary else "fallback",
        )
        if not dry_dir:
            save_state(
                root,
                sid,
                {
                    "segments": seg,
                    "captured_lines": start,
                    "pending": {"payload": payload, "end_line": red.end_line},
                },
            )
        out = send_fn(payload, cfg, dry_dir, f"{sid}-seg{seg}")
        stats["path"] = out.path
        if out.status == "error":
            return {**stats, "status": "error", "detail": out.detail, "latency_s": time.monotonic() - t0}
        save_state(root, sid, {"segments": seg + 1, "captured_lines": red.end_line})
        return {
            **stats,
            "status": "ok" if out.status == "ok" else out.status,
            "latency_s": round(time.monotonic() - t0, 2),
            "out": out.detail if dry_dir else "",
        }


def log_result(stats: dict[str, Any]) -> None:
    keys = (
        "status",
        "event",
        "slug",
        "sid",
        "segment",
        "owner",
        "reduced_chars",
        "lines_dropped",
        "emails_masked",
        "llm",
        "llm_error",
        "llm_latency_s",
        "in_tokens",
        "out_tokens",
        "cost_usd",
        "decisions",
        "lessons",
        "path",
        "detail",
        "latency_s",
    )
    parts = [time.strftime("%Y-%m-%dT%H:%M:%S")] + [f"{k}={stats[k]}" for k in keys if k in stats]
    C.log_line(" ".join(parts))


def safe_process(hook: dict[str, Any], **kw: Any) -> dict[str, Any]:
    try:
        stats = process(hook, **kw)
    except BaseException as exc:  # noqa: BLE001 - never crash, never block
        stats = {"status": "error", "detail": f"unexpected:{type(exc).__name__}"}
    if stats.get("status") not in {"killed", "unmapped"}:
        log_result(stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hlmemo.capture.run")
    ap.add_argument("--input", help="queued hook-input JSON file (deleted after reading)")
    ap.add_argument("--transcript")
    ap.add_argument("--cwd")
    ap.add_argument("--session-id")
    ap.add_argument("--event", default="SessionEnd")
    ap.add_argument("--dry-run", metavar="DIR", help="write the payload JSON to DIR instead of prod")
    a = ap.parse_args(argv)
    hook: dict[str, Any] = {}
    try:
        if a.input:
            p = Path(a.input)
            hook = json.loads(p.read_text(encoding="utf-8"))
            with contextlib.suppress(OSError):
                p.unlink()
        else:
            hook = {
                "transcript_path": a.transcript,
                "cwd": a.cwd,
                "session_id": a.session_id,
                "hook_event_name": a.event,
            }
        stats = safe_process(hook, dry_dir=a.dry_run or os.environ.get(C.DRYRUN_ENV) or None)
        if a.dry_run:
            print(json.dumps(stats, default=str))
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
