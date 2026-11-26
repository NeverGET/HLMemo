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

agy (``client = "agy"``): the same disjoint segments, keyed by the agy ``conversationId``, with the
step index as the cursor (``captured_lines`` holds the NEXT step index; see ``reduce_agy``). agy's Stop
hook fires at the end of every turn, so a live Stop (``mode = "stop"``) writes only when the uncaptured
segment passes the size threshold (``agy_min_owner_messages`` OR ``agy_min_chars``, with the usual
``min_owner_messages`` floor); otherwise the segment keeps growing. The tail after the last written
segment is written by the idle flush (``sweep_agy``): any agy worker (Stop or SessionStart) and
``--flush`` look for conversations idle for ``agy_idle_minutes`` with an uncaptured tail and capture it
(``mode = "flush"``: the floor only, or any owner message once the conversation has a segment).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
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
from hlmemo.capture import reduce_agy as RA
from hlmemo.capture import summarize as S
from hlmemo.capture import write as W
from hlmemo.capture.scrub import ScrubReport, residual_findings, scrub_text

LOCK_TIMEOUT_S = 900.0
AGY = "agy"
AGY_LOCK_TIMEOUT_S = 5.0  # a Stop fires every turn: a busy lock means "the next Stop will see it"
AGY_META_KEYS = (
    "client", "transcript_path", "cwd", "model_name", "last_stop_at", "swept_stop",
    "errors", "retry_after", "failed_fp",
)  # fmt: skip
SWEEP_MAX = 5  # idle conversations flushed per worker run (each is one summarizer call)
STOP_QUIET_S = 2.0  # a Stop worker waits until the agy log has been quiet this long ...
STOP_MAX_WAIT_S = 20.0  # ... at most this long (else "busy": the next Stop or the flush covers it)
SWEEP_QUIET_S = 120.0  # the idle flush skips a conversation whose log changed this recently
BACKOFF_BASE_S = 600.0  # an agy error waits 10 min, doubling per repeat ...
BACKOFF_MAX_S = 6 * 3600.0  # ... up to 6 h
UNLOGGED = {"killed", "unmapped", "below_threshold", "backoff"}  # per-turn agy statuses: no log line

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


def wait_quiet(tpath: str, quiet_s: float, max_wait_s: float) -> bool:
    """True once the agy logs have not changed for ``quiet_s`` seconds (wall clock), waiting at most
    ``max_wait_s``."""
    deadline = time.monotonic() + max_wait_s
    while True:
        m = RA.newest_mtime(tpath)
        age = time.time() - m if m is not None else quiet_s
        if age >= quiet_s:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, min(quiet_s - age, 0.5, deadline - time.monotonic())))


def process(
    hook: dict[str, Any],
    *,
    cfg: C.CaptureConfig | None = None,
    dry_dir: str | None = None,
    summarize_fn: SummarizeFn | None = None,
    send_fn: Callable[[dict[str, Any], C.CaptureConfig, str | None, str], W.WriteOutcome] | None = None,
    lock_timeout_s: float | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run one capture. Returns a stats dict (``status`` in: killed, unmapped, bad_input, locked,
    skip_small, nothing_new, ok, error; agy also: below_threshold, busy, backoff,
    unchanged_after_error). No content is ever logged or returned except the payload in dry-run mode
    (written to a 0600 file)."""
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
    agy = hook.get("client") == AGY
    mode = str(hook.get("mode") or "stop") if agy else ""
    event = str(hook.get("hook_event_name") or ("Stop" if agy else "SessionEnd"))
    if agy and mode == "flush":
        event = "idle flush"
    if not sid or not tpath or not os.path.isfile(tpath) or not all(ch.isalnum() or ch in "-_" for ch in sid):
        return {"status": "bad_input"}
    if agy and not _agy_transcript_path(tpath):  # review 136 B3: only agy's own conversation logs
        return {"status": "bad_input"}
    root = Path(dry_dir) / ".state" if dry_dir else C.state_dir()
    summarize_fn = summarize_fn or _default_summarize
    send_fn = send_fn or _send
    if lock_timeout_s is None:
        lock_timeout_s = AGY_LOCK_TIMEOUT_S if agy else LOCK_TIMEOUT_S
    stats: dict[str, Any] = {"event": event, "slug": slug, "sid": sid[:8]}
    if agy:
        stats["client"] = AGY
        # a log still being written may get a late step after this run: wait (Stop) or skip (flush)
        quiet, wait = (STOP_QUIET_S, STOP_MAX_WAIT_S) if mode == "stop" else (SWEEP_QUIET_S, 0.0)
        if not wait_quiet(tpath, quiet, wait):
            if (
                mode == "stop"
            ):  # review 135 r2: a busy Stop still tells the idle flush about this conversation
                with session_lock(root, sid, lock_timeout_s) as got:
                    if got:
                        st = load_state(root, sid)
                        st.update(
                            client=AGY,
                            transcript_path=tpath,
                            cwd=cwd,
                            last_stop_at=time.time() if now is None else now,
                        )
                        save_state(root, sid, st)
            return {**stats, "status": "busy", "latency_s": time.monotonic() - t0}
    t_now = time.time() if now is None else now
    with session_lock(root, sid, lock_timeout_s) as got:
        if not got:
            return {**stats, "status": "locked"}
        state = load_state(root, sid)
        meta: dict[str, Any] = {}
        recent: list[int] = []
        if agy:  # bookkeeping for the idle flush and backoff; Claude state stays exactly as before
            meta = {k: state[k] for k in AGY_META_KEYS if k in state}
            meta.update(client=AGY, transcript_path=tpath, cwd=cwd)
            if isinstance(hook.get("model_name"), str) and hook["model_name"]:
                meta["model_name"] = hook["model_name"]
            if mode == "stop":
                meta["last_stop_at"] = t_now
            recent = [i for i in state.get("recent_steps") or [] if isinstance(i, int)]
            if "recent_steps" not in state and int(state.get("captured_lines") or 0) > 0:
                # review 135 r2: a cursor saved without recent_steps counts its late window as captured
                done = int(state["captured_lines"])
                recent = list(range(max(0, done - RA.LATE_WINDOW), done))

        def put(st: dict[str, Any]) -> None:
            save_state(root, sid, {**st, **meta} if agy else st)

        def cursor(seg_n: int, start_n: int, recent_n: list[int]) -> dict[str, Any]:
            st: dict[str, Any] = {"segments": seg_n, "captured_lines": start_n}
            if agy:
                st["recent_steps"] = recent_n
            return st

        def fail(detail: str, fp: str | None = None) -> dict[str, Any]:
            """An agy error backs off (doubling) instead of re-summarizing at every Stop."""
            if agy:
                n = int(meta.get("errors") or 0) + 1
                meta.update(errors=n, retry_after=t_now + min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (n - 1)))
                if fp:
                    meta["failed_fp"] = fp
                cur = {"segments": 0, "captured_lines": 0, **load_state(root, sid)}  # keeps a pending
                save_state(root, sid, {**cur, **meta})
            return {**stats, "status": "error", "detail": detail, "latency_s": time.monotonic() - t0}

        def succeeded() -> None:
            for k in ("errors", "retry_after", "failed_fp"):
                meta.pop(k, None)

        retry_after = meta.get("retry_after")
        if agy and isinstance(retry_after, (int, float)) and t_now < retry_after:
            save_state(root, sid, {**state, **meta})
            return {**stats, "status": "backoff", "latency_s": time.monotonic() - t0}
        seg = int(state.get("segments", 0))
        start = int(state.get("captured_lines", 0))
        pending = state.get("pending")
        if isinstance(pending, dict) and isinstance(pending.get("payload"), dict):
            out = send_fn(pending["payload"], cfg, dry_dir, f"{sid}-seg{seg}")
            stats["resent_pending"] = True
            if out.status == "error":
                return fail(out.detail)
            succeeded()
            seg += 1
            start = int(pending.get("end_line", start))
            if agy:
                recent = [i for i in pending.get("recent_steps") or [] if isinstance(i, int)]
            state = cursor(seg, start, recent)
            put(state)
        if agy:
            red = RA.read_segment(tpath, start=start, seen=recent, cap=cfg.cap_chars, cwd_hint=cwd)
        else:
            red = R.read_segment(tpath, start=start, cap=cfg.cap_chars, cwd_hint=cwd)
        stats.update(owner=red.owner_messages, segment=seg, reduced_chars=len(red.text))
        base = cursor(seg, start, recent)
        empty = (not red.steps) if agy else red.end_line <= start
        if empty or red.segments_total == 0:
            if agy:
                if mode == "flush":
                    meta["swept_stop"] = meta.get("last_stop_at")
                put(base)
            return {**stats, "status": "nothing_new", "latency_s": time.monotonic() - t0}
        fp: str | None = None
        if agy:
            status = _agy_gate(red, cfg, mode=mode, segments_written=seg)
            fp = hashlib.sha256(f"{start}|{red.steps}|{red.text}".encode()).hexdigest()[:16]
            if not status and fp == meta.get("failed_fp"):
                status = "unchanged_after_error"  # the same input failed before: no second summary
            if status:
                if mode == "flush":
                    meta["swept_stop"] = meta.get("last_stop_at")
                put(base)
                return {**stats, "status": status, "latency_s": time.monotonic() - t0}
        elif red.owner_messages < cfg.min_owner_messages:
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
        client = AGY if agy else "claude-code"
        composed = W.compose(
            red,
            summary,
            sid=sid,
            segment=seg,
            event=event,
            model=cfg.model,
            report=rep,
            client=client,
            agent_model=meta.get("model_name") if agy else None,
        )
        payload = W.build_payload(
            composed,
            slug=slug,
            claude_sid=sid,
            segment=seg,
            last_ts=red.last_ts,
            request_id=str(uuid.uuid4()),
            client=client,
        )
        leaks = residual_findings(W.payload_text(payload))
        if leaks:  # defence in depth: never write something the scrub would still flag
            return fail("residual_secret", fp)
        try:
            W.validate_payload(payload)
        except Exception as exc:  # noqa: BLE001
            res = fail(f"payload_invalid:{type(exc).__name__}", fp)
            res.pop("latency_s", None)
            return res
        stats.update(
            decisions=len(payload.get("decisions", [])),
            lessons=len(payload.get("lessons", [])),
            llm="ok" if summary else "fallback",
        )
        new_recent = RA.recent_after(recent, red.steps, red.end_line) if agy else []
        if not dry_dir:
            pend: dict[str, Any] = {"payload": payload, "end_line": red.end_line}
            if agy:
                pend["recent_steps"] = new_recent
            put({"segments": seg, "captured_lines": start, "pending": pend})
        out = send_fn(payload, cfg, dry_dir, f"{sid}-seg{seg}")
        stats["path"] = out.path
        if out.status == "error":
            return fail(out.detail)
        succeeded()
        if agy and mode == "flush":
            meta["swept_stop"] = meta.get("last_stop_at")
        put(cursor(seg + 1, red.end_line, new_recent))
        return {
            **stats,
            "status": "ok" if out.status == "ok" else out.status,
            "latency_s": round(time.monotonic() - t0, 2),
            "out": out.detail if dry_dir else "",
        }


def _agy_transcript_path(tpath: str) -> bool:
    """agy writes its conversation log as ``<brain>/<id>/.system_generated/logs/transcript[_full].jsonl``;
    a path from stdin that does not have that shape is refused."""
    p = Path(tpath)
    return p.name in ("transcript.jsonl", "transcript_full.jsonl") and p.parent.parts[-2:] == (
        ".system_generated",
        "logs",
    )


def _agy_gate(red: R.Reduced, cfg: C.CaptureConfig, *, mode: str, segments_written: int) -> str | None:
    """None = capture this agy segment now; else the skip status. Live Stop: the floor
    (``min_owner_messages``) AND (``agy_min_owner_messages`` owner messages OR ``agy_min_chars`` reduced
    chars). Idle flush: the floor, or ANY non-empty tail once the conversation already has a segment
    (so the tail of a captured conversation is never dropped, even a final answer without a new owner
    message)."""
    owner = red.owner_messages
    if mode == "flush":
        if owner >= cfg.min_owner_messages or segments_written > 0:
            return None
        return "skip_small"
    if owner < cfg.min_owner_messages:
        return "below_threshold"
    if owner >= cfg.agy_min_owner_messages or len(red.text) >= cfg.agy_min_chars:
        return None
    return "below_threshold"


def sweep_agy(
    *,
    cfg: C.CaptureConfig | None = None,
    dry_dir: str | None = None,
    idle_s: float | None = None,
    now: float | None = None,
    exclude: str | None = None,
    limit: int = SWEEP_MAX,
    **kw: Any,
) -> list[dict[str, Any]]:
    """The idle flush: capture the uncaptured tail of agy conversations whose last Stop is at least
    ``idle_s`` (default ``agy_idle_minutes``) old and that were not flushed since. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        cfg = cfg or C.load_config()
        if C.killed():
            return out
        root = Path(dry_dir) / ".state" if dry_dir else C.state_dir()
        idle = cfg.agy_idle_minutes * 60.0 if idle_s is None else idle_s
        t = time.time() if now is None else now
        cands: list[tuple[int, float, str, dict[str, Any]]] = []
        for p in (root / "sessions").glob("*.json"):
            sid = p.stem
            if sid == exclude:
                continue
            st = load_state(root, sid)
            last = st.get("last_stop_at")
            if st.get("client") != AGY or not isinstance(last, (int, float)) or st.get("swept_stop") == last:
                continue
            if t - last < idle:
                continue
            retry = st.get("retry_after")
            if isinstance(retry, (int, float)) and t < retry:
                continue  # failing: backed off, so newer idle tails are not starved
            m = RA.newest_mtime(str(st.get("transcript_path") or ""))
            if m is not None and time.time() - m < SWEEP_QUIET_S:
                continue  # still being written (a long turn): not idle after all
            cands.append((int(st.get("errors") or 0), last, sid, st))
        for _, _, sid, st in sorted(cands)[:limit]:  # never-failing first, then oldest
            hook = {
                "client": AGY,
                "mode": "flush",
                "session_id": sid,
                "transcript_path": st.get("transcript_path"),
                "cwd": st.get("cwd"),
                "hook_event_name": "Stop",
                "model_name": st.get("model_name"),
            }
            res = safe_process(hook, cfg=cfg, dry_dir=dry_dir, now=t, **kw)
            if res.get("status") in {"bad_input", "unmapped"}:  # transcript gone / mapping removed
                with session_lock(root, sid, AGY_LOCK_TIMEOUT_S) as got:
                    if got:
                        cur = load_state(root, sid)
                        if cur.get("last_stop_at") == st.get("last_stop_at"):
                            save_state(root, sid, {**cur, "swept_stop": cur.get("last_stop_at")})
            out.append(res)
    except BaseException as exc:  # noqa: BLE001 - never crash, never block
        out.append({"status": "error", "detail": f"sweep:{type(exc).__name__}"})
    return out


def log_result(stats: dict[str, Any]) -> None:
    keys = (
        "status",
        "client",
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
    if stats.get("status") not in UNLOGGED:
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
    ap.add_argument("--client", choices=["claude-code", AGY], default="claude-code")
    ap.add_argument("--flush", action="store_true", help="agy: flush idle conversations' tails and exit")
    ap.add_argument("--idle-minutes", type=float, help="with --flush: idle threshold (default from config)")
    a = ap.parse_args(argv)
    hook: dict[str, Any] = {}
    try:
        dry = a.dry_run or os.environ.get(C.DRYRUN_ENV) or None
        if a.flush:
            idle = None if a.idle_minutes is None else a.idle_minutes * 60.0
            res = sweep_agy(dry_dir=dry, idle_s=idle)
            if a.dry_run:
                print(json.dumps(res, default=str))
            return 0
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
            if a.client == AGY:
                hook.update(client=AGY, mode="flush", hook_event_name="Stop")
        if hook.get("client") == AGY and hook.get("mode") == "sweep":  # agy SessionStart
            sweep_agy(dry_dir=dry)
            return 0
        stats = safe_process(hook, dry_dir=dry)
        if hook.get("client") == AGY:
            sweep_agy(dry_dir=dry, exclude=str(hook.get("session_id") or ""))
        if a.dry_run:
            print(json.dumps(stats, default=str))
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
