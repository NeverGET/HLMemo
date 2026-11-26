"""Session capture for agy (Google's Antigravity CLI): dialect detection in the hook, the step-based
reducer, the per-turn Stop threshold, segment idempotency, the idle flush, and the Claude path unchanged.

All transcripts are synthetic. No network, no LLM call, no writer (the summarizer and the sender are
test doubles)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from hlmemo.capture import config as C
from hlmemo.capture import hook as H
from hlmemo.capture import reduce_agy as RA
from hlmemo.capture import run as RUN
from hlmemo.capture import summarize as S
from hlmemo.capture import write as W

CID = "7e1d0c2b-aaaa-4bbb-8ccc-0123456789ab"
CLAUDE_SID = "0a1b2c3d-1111-2222-3333-444455556666"
SUMMARY = {
    "notes": "The importer dedupe was fixed and committed.",
    "decisions": ["Dedupe by sha256 of the path."],
    "lessons": [],
    "uncertain": [],
}


# --------------------------------------------------------------------------- synthetic agy steps
def _step(i: int, typ: str, **kw: Any) -> str:
    rec = {
        "step_index": i,
        "source": "MODEL",
        "type": typ,
        "status": "DONE",
        "created_at": f"2026-11-20T{10 + i // 60:02d}:{i % 60:02d}:00Z",
    }
    rec.update(kw)
    return json.dumps(rec)


def _user(i: int, text: str) -> str:
    content = (
        f"<USER_REQUEST>\n{text}\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\n"
        "The current local time is: 2026-11-20T13:00:00+03:00.\n</ADDITIONAL_METADATA>"
    )
    return _step(i, "USER_INPUT", source="USER_EXPLICIT", content=content)


def _plan(i: int, text: str | None = None, *, calls: list[dict[str, Any]] | None = None, **kw: Any) -> str:
    extra: dict[str, Any] = dict(kw)
    if text is not None:
        extra["content"] = text
    if calls:
        extra["tool_calls"] = calls
    return _step(i, "PLANNER_RESPONSE", **extra)


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": {**args, "toolAction": "x", "toolSummary": "y"}}


def first_turn(i: int = 0) -> list[str]:
    """One rich turn: edit, commit (command + result), tool noise, thinking, a final answer."""
    commit = 'git add -A && git commit -q -m "fix importer dedupe"'
    return [
        _user(i, "Please fix the importer bug in src/imp.py and commit it"),
        _step(i + 1, "CONVERSATION_HISTORY", source="SYSTEM", content="# Conversation History\nOTHER CHAT"),
        _plan(
            i + 2, "I will view the importer.", calls=[_call("view_file", AbsolutePath="/w/proj/src/imp.py")]
        ),
        _step(
            i + 3, "VIEW_FILE", content="Created At: x\nFile Path: /w/proj/src/imp.py\n" + "HUGE FILE " * 400
        ),
        _plan(
            i + 4,
            thinking="SECRET THINKING",
            calls=[_call("replace_file_content", TargetFile="/w/proj/src/imp.py", TargetContent="a")],
        ),
        _step(i + 5, "CODE_ACTION", content="Created At: x\nEdited file"),
        _plan(i + 6, calls=[_call("run_command", CommandLine=commit, Cwd="/w/proj")]),
        _step(i + 7, "RUN_COMMAND", content="Created At: x\nOutput:\n[main abc1234] fix importer dedupe\n"),
        _step(i + 8, "SYSTEM_MESSAGE", source="SYSTEM", content="<SYSTEM_MESSAGE>noise</SYSTEM_MESSAGE>"),
        _plan(i + 9, "Decided: dedupe by sha256 (commit abc1234). Root cause: the key ignored paths. " * 6),
    ]


def turns(n: int, start_step: int, tag: str = "turn") -> list[str]:
    out: list[str] = []
    for k in range(n):
        i = start_step + 3 * k
        out.append(_user(i, f"{tag} owner message {k}: ok continue"))
        out.append(_plan(i + 1, "short narration", calls=[_call("list_dir", DirectoryPath="/w/proj")]))
        out.append(_plan(i + 2, f"{tag} result {k}: step done, nothing else is open. " * 12))
    return out


def transcript(n_owner: int) -> list[str]:
    return first_turn(0) + turns(n_owner - 1, 10)


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _logdir(base: Path) -> Path:
    return base / "brain" / CID / ".system_generated" / "logs"


# --------------------------------------------------------------------------- doubles + fixtures
class Sender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload, cfg, dry, name):  # noqa: ANN001
        self.calls.append(payload)
        return W.WriteOutcome("ok", "test", "detail")


class Summarizer:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text, cwd, date, cfg):  # noqa: ANN001
        self.texts.append(text)
        return SUMMARY, S.LlmRun(data=SUMMARY, latency_s=1.0, cost_usd=0.01)


@pytest.fixture(autouse=True)
def _no_quiet_wait(monkeypatch):
    """The synthetic logs are written right before each run: don't wait for them to go quiet (the
    quiet checks have their own tests below)."""
    monkeypatch.setattr(RUN, "STOP_QUIET_S", 0.0)
    monkeypatch.setattr(RUN, "SWEEP_QUIET_S", 0.0)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated state + capture.toml mapping /w/proj -> hlmemo (default agy thresholds)."""
    monkeypatch.delenv(C.KILL_ENV, raising=False)
    monkeypatch.delenv(C.DRYRUN_ENV, raising=False)
    monkeypatch.setenv(C.STATE_ENV, str(tmp_path / "state"))
    cfgp = tmp_path / "capture.toml"
    cfgp.write_text('[projects]\n"/w/proj" = "hlmemo"\n', encoding="utf-8")
    monkeypatch.setenv(C.CONFIG_ENV, str(cfgp))
    return tmp_path


def agy_hook(tp: Path, *, model: str | None = "gemini-test-pro", cwd: str = "/w/proj") -> dict[str, Any]:
    return {
        "client": "agy",
        "mode": "stop",
        "hook_event_name": "Stop",
        "session_id": CID,
        "transcript_path": str(tp),
        "cwd": cwd,
        "model_name": model,
    }


def agy_stdin(tp: Path | str, event: str = "Stop", **kw: Any) -> dict[str, Any]:
    return {
        "conversationId": CID,
        "transcriptPath": str(tp),
        "workspacePaths": ["/w/proj"],
        "cwd": "/w/proj",
        "hookEventName": event,
        "modelName": "gemini-test-pro",
        "terminationReason": "NO_TOOL_CALL",
        "fullyIdle": True,
        "executionNum": 3,
        **kw,
    }


# --------------------------------------------------------------------------- reduction
def test_agy_reduce_maps_steps_to_the_reduced_form():
    red = RA.reduce_steps(transcript(3), cwd_hint="/w/proj")
    assert red.owner_messages == 3
    t = red.text
    assert "Please fix the importer bug" in t and "turn owner message 1" in t
    assert "Root cause: the key ignored paths" in t  # the turn's final model text
    for noise in ("<USER_REQUEST>", "ADDITIONAL_METADATA", "current local time", "SECRET THINKING"):
        assert noise not in t
    for noise in ("HUGE FILE", "OTHER CHAT", "noise", "short narration"):
        assert noise not in t  # tool outputs, history, system messages, short narration
    assert red.files == ["src/imp.py"]  # editor tools only, relative to the agent's cwd
    assert ("abc1234", "fix importer dedupe") in red.commits
    assert "fix importer dedupe" in red.commit_messages
    assert "abc1234 fix importer dedupe" in t  # the programmatic facts header
    assert red.first_ts == "2026-11-20T10:00:00Z" and red.end_line == 10 + 3 * 2
    assert red.segments_total > 0


def test_agy_reduce_cursor_disorder_duplicates_and_partial_line():
    lines = transcript(3)
    swapped = [*lines[:-2], lines[-1], lines[-2]]  # agy sometimes writes a step after its successor
    red = RA.reduce_steps([*swapped, '{"step_index": 99, "type": "USER_INP'], start=13, seen=range(13))
    assert red.owner_messages == 1 and "turn owner message 1" in red.text
    assert "turn owner message 0" not in red.text and "Please fix" not in red.text
    assert red.end_line == 16  # the half-written line is not counted: the next run re-reads it
    dup = [_user(0, "old truncated"), _user(0, "the full record wins")]
    assert "the full record wins" in RA.reduce_steps(dup).text
    assert RA.reduce_steps(lines, start=500).end_line == 500  # nothing new


def test_agy_reads_both_logs_merged_by_step_index(tmp_path):
    d = _logdir(tmp_path)
    lines = transcript(4)
    # transcript.jsonl: complete window, but long fields cut; transcript_full.jsonl: head missing.
    cut = [_user(0, "Please fix the importer [truncated]"), *lines[1:]]
    tp = _write(d / "transcript.jsonl", cut)
    _write(d / RA.FULL_NAME, lines[12:])
    red = RA.read_segment(tp, cwd_hint="/w/proj")
    assert red.owner_messages == 4 and "Please fix the importer [truncated]" in red.text
    _write(d / RA.FULL_NAME, lines)  # now the full log covers the step: its record wins
    assert "Please fix the importer bug" in RA.read_segment(tp, cwd_hint="/w/proj").text
    assert RA.transcript_files(d / RA.FULL_NAME) == [d / RA.FULL_NAME]


def test_agy_checkpoint_kept_only_when_the_window_starts_mid_conversation():
    ckpt = _step(
        0, "CHECKPOINT", source="SYSTEM", content="{{ CHECKPOINT 3 }}\n# Previous Session Summary: X"
    )
    red = RA.reduce_steps([ckpt, *turns(3, 1)])
    assert "EARLIER-SUMMARY" in red.text and "Previous Session Summary" in red.text
    late = _step(40, "CHECKPOINT", source="SYSTEM", content="{{ CHECKPOINT 0 }}\n# User Requests: X")
    assert "EARLIER-SUMMARY" not in RA.reduce_steps([*transcript(3), late]).text  # restates the window
    mid = _step(
        50, "CHECKPOINT", source="SYSTEM", content="{{ CHECKPOINT 4 }}\n# Previous Session Summary: Y"
    )
    later = RA.reduce_steps([*transcript(3), mid, *turns(3, 51)], start=50, seen=range(50))  # a later segment
    assert later.owner_messages == 3 and "EARLIER-SUMMARY" not in later.text


# --------------------------------------------------------------------------- hook (dialect detection)
def test_hook_detects_agy_and_queues_a_normalized_record(env):
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(2))
    assert H.handle(json.dumps(agy_stdin(tp)), spawn=False) == "spawned"
    (q,) = list((C.state_dir() / "queue").glob("*.json"))
    rec = json.loads(q.read_text())
    assert oct(q.stat().st_mode & 0o777) == "0o600"
    assert rec["client"] == "agy" and rec["mode"] == "stop" and rec["session_id"] == CID
    assert rec["transcript_path"] == str(tp) and rec["cwd"] == "/w/proj"
    assert rec["model_name"] == "gemini-test-pro"
    q.unlink()
    no_cwd = {k: v for k, v in agy_stdin(tp).items() if k != "cwd"}  # falls back to workspacePaths[0]
    assert H.handle(json.dumps(no_cwd), spawn=False) == "spawned"
    (q2,) = list((C.state_dir() / "queue").glob("*.json"))
    assert json.loads(q2.read_text())["cwd"] == "/w/proj"
    q2.unlink()
    start = agy_stdin(tp, event="SessionStart")
    assert H.handle(json.dumps(start), spawn=False) == "spawned"
    (q3,) = list((C.state_dir() / "queue").glob("*.json"))
    assert json.loads(q3.read_text())["mode"] == "sweep"
    q3.unlink()
    for ev in ("PreToolUse", "PostInvocation"):
        assert H.handle(json.dumps(agy_stdin(tp, event=ev)), spawn=False) == "ignored"
    assert H.handle(json.dumps(agy_stdin(tp, cwd="/somewhere/else", workspacePaths=[])), spawn=False) == (
        "unmapped"
    )
    assert not list((C.state_dir() / "queue").glob("*.json"))


def test_hook_subprocess_prints_json_for_agy_only_and_exits_fast(env):
    base = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    cases = [
        (json.dumps(agy_stdin("/nope", cwd="/not/mapped")), "{}"),  # agy unmapped: still a JSON reply
        (json.dumps(agy_stdin("/nope", event="PreToolUse")), "{}"),
        (json.dumps({"hook_event_name": "SessionEnd", "cwd": "/nope", "session_id": "x"}), ""),  # Claude
        ("not json at all", ""),
    ]
    for stdin, want in cases:
        t0 = time.monotonic()
        p = subprocess.run(
            [sys.executable, "-m", "hlmemo.capture.hook"],
            input=stdin,
            text=True,
            capture_output=True,
            env=base,
            timeout=10,
            cwd="/",  # agy runs hooks in the hooks.json folder: the hook must not depend on its own cwd
        )
        assert p.returncode == 0 and time.monotonic() - t0 < 1.0
        assert p.stdout.strip() == want
        if want:
            assert json.loads(p.stdout).get("decision") != "continue"  # never blocks the agent's stop


# --------------------------------------------------------------------------- pipeline: threshold + segments
def test_stop_below_threshold_writes_nothing_and_keeps_the_segment_growing(env):
    d = _logdir(env)
    tp = _write(d / "transcript.jsonl", transcript(4))
    send, summ = Sender(), Summarizer()
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)
    assert r["status"] == "below_threshold" and r["client"] == "agy" and send.calls == []
    st = RUN.load_state(C.state_dir(), CID)
    assert st["captured_lines"] == 0 and st["client"] == "agy" and "last_stop_at" in st
    _write(d / "transcript.jsonl", transcript(12))  # the same conversation, more turns
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)
    assert r["status"] == "ok" and r["segment"] == 0 and len(send.calls) == 1
    assert "Please fix the importer bug" in summ.texts[0]  # the first turns were not lost while waiting
    p = send.calls[0]
    assert p["session_id"] == W.session_key(CID, "hlmemo") and p["client"] == C.CLIENT_NAME_AGY
    assert "AUTO-CAPTURED session note (agy capture hook; agent model: gemini-test-pro)" in p["notes"]
    assert f"- agy conversation: {CID}\n" in p["notes"] and "Claude session" not in p["notes"]
    assert "abc1234 fix importer dedupe" in p["notes"]
    W.validate_payload(p)


def test_threshold_by_chars_and_the_owner_floor(env):
    (env / "capture.toml").write_text(
        '[projects]\n"/w/proj" = "hlmemo"\n[capture]\nagy_min_chars = 2000\n', encoding="utf-8"
    )
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(2))
    send = Sender()
    assert RUN.process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=send)["status"] == "below_threshold"
    _write(tp, transcript(3))  # 3 owner messages (the floor) and > 2000 reduced chars
    assert RUN.process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=send)["status"] == "ok"


def test_two_stops_write_disjoint_segments_and_nothing_twice(env):
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)["status"] == "ok"
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)["status"] == "nothing_new"
    nxt = 10 + 3 * 11
    more = turns(12, nxt, tag="second")
    # the head of the log is dropped (agy windows): the step-index cursor still finds the new part
    _write(d / "transcript.jsonl", lines[20:] + more)
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)
    assert r["status"] == "ok" and r["segment"] == 1 and len(send.calls) == 2
    assert send.calls[1]["session_id"] == W.session_key(CID, "hlmemo", 1) != send.calls[0]["session_id"]
    assert "second owner message 0" in summ.texts[1] and "turn owner message" not in summ.texts[1]
    assert "Please fix" not in summ.texts[1]
    assert "(segment 1, Stop)" in send.calls[1]["notes"]


def test_idle_flush_writes_the_tail_once(env):
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    send, summ = Sender(), Summarizer()
    t0 = 1_000_000.0
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=t0)["status"] == "ok"
    tail = turns(2, 10 + 3 * 11, tag="tail")
    _write(d / "transcript.jsonl", lines + tail)
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=t0 + 60)["status"] == (
        "below_threshold"
    )
    kw = {"summarize_fn": summ, "send_fn": send}
    assert RUN.sweep_agy(now=t0 + 60 + 600, **kw) == []  # not idle yet (default 30 min)
    (res,) = RUN.sweep_agy(now=t0 + 60 + 1800, **kw)
    assert res["status"] == "ok" and res["segment"] == 1 and len(send.calls) == 2
    assert "tail owner message 1" in summ.texts[-1] and "(segment 1, idle flush)" in send.calls[1]["notes"]
    assert RUN.sweep_agy(now=t0 + 9999, **kw) == []  # flushed once: no rescans, no second write
    # the conversation resumes: a new Stop makes it eligible again, and only the new part is written
    _write(d / "transcript.jsonl", lines + tail + turns(1, 200, tag="resumed"))
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=t0 + 20000)["status"] == (
        "below_threshold"
    )
    (res,) = RUN.sweep_agy(now=t0 + 20000 + 1800, **kw)
    assert res["status"] == "ok" and res["segment"] == 2 and "tail owner" not in summ.texts[-1]


def test_idle_flush_skips_a_tiny_conversation_once_and_ignores_claude_state(env):
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(2))
    send = Sender()
    RUN.process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=send, now=1000.0)
    RUN.save_state(C.state_dir(), CLAUDE_SID, {"segments": 1, "captured_lines": 40})
    (res,) = RUN.sweep_agy(now=1000.0 + 3600, summarize_fn=Summarizer(), send_fn=send)
    assert res["status"] == "skip_small" and send.calls == []  # 2 owner messages, nothing captured before
    assert RUN.sweep_agy(now=1000.0 + 7200, summarize_fn=Summarizer(), send_fn=send) == []
    assert RUN.load_state(C.state_dir(), CLAUDE_SID) == {"segments": 1, "captured_lines": 40}


def test_agy_unmapped_cwd_is_a_noop(env):
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(12))
    send = Sender()
    r = RUN.process(agy_hook(tp, cwd="/somewhere/else"), summarize_fn=Summarizer(), send_fn=send)
    assert r["status"] == "unmapped" and send.calls == []
    assert not (C.state_dir() / "sessions").exists()


def test_agy_busy_lock_returns_quickly(env):
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(12))
    with RUN.session_lock(C.state_dir(), CID, 1) as got:
        assert got
        t0 = time.monotonic()
        r = RUN.process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=Sender(), lock_timeout_s=0.3)
        assert r["status"] == "locked" and time.monotonic() - t0 < 2
    assert RUN.AGY_LOCK_TIMEOUT_S < RUN.LOCK_TIMEOUT_S


# --------------------------------------------------------------------------- the Claude path is unchanged
def test_claude_payload_and_state_are_unchanged(env):
    rec = [
        json.dumps(
            {
                "type": "user" if k % 2 == 0 else "assistant",
                "timestamp": f"2026-09-30T10:{k:02d}:00.000Z",
                "sessionId": CLAUDE_SID,
                "cwd": "/w/proj",
                "message": {
                    "role": "user" if k % 2 == 0 else "assistant",
                    "content": f"owner message {k}"
                    if k % 2 == 0
                    else [{"type": "text", "text": f"answer {k}"}],
                },
            }
        )
        for k in range(8)
    ]
    tp = env / "claude.jsonl"
    tp.write_text("\n".join(rec) + "\n", encoding="utf-8")
    send = Sender()
    hook = {
        "session_id": CLAUDE_SID,
        "transcript_path": str(tp),
        "cwd": "/w/proj",
        "hook_event_name": "SessionEnd",
    }
    r = RUN.process(hook, summarize_fn=Summarizer(), send_fn=send)
    assert r["status"] == "ok" and "client" not in r
    p = send.calls[0]
    assert p["client"] == "claude-code/capture-hook"
    assert p["session_id"] == W.session_key(CLAUDE_SID, "hlmemo")
    assert "AUTO-CAPTURED session note (claude-code capture hook); summarised by" in p["notes"]
    assert f"- Claude session: {CLAUDE_SID}\n" in p["notes"]
    assert RUN.load_state(C.state_dir(), CLAUDE_SID) == {"segments": 1, "captured_lines": 8}


def test_user_input_without_request_tag_yields_no_owner_text() -> None:
    """Review 136 B1: a USER_INPUT without <USER_REQUEST> may hold injected blocks (attached file contents,
    environment); none of it may become owner text, even when it carries something token-shaped."""
    from hlmemo.capture import reduce_agy

    fake = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    content = f"<file_contents>\nconfig.py\nTOKEN = '{fake}'\n</file_contents>\nplain trailing text"
    assert reduce_agy.user_text(content) == ""
    assert "please fix the test" in reduce_agy.user_text("<USER_REQUEST>please fix the test</USER_REQUEST>")


def test_agy_transcript_path_outside_agy_log_layout_is_refused(env, tmp_path: Path) -> None:
    """Review 136 B3: the agy transcript path comes from stdin; only agy's own log layout is read."""
    wrong = _write(tmp_path / "notes.jsonl", transcript(12))
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(wrong), summarize_fn=summ, send_fn=send)["status"] == "bad_input"
    assert send.calls == []
    assert RUN._agy_transcript_path(
        str(tmp_path / "b" / "x" / ".system_generated" / "logs" / "transcript.jsonl")
    )
    assert not RUN._agy_transcript_path(str(tmp_path / "logs" / "transcript.jsonl"))


# --------------------------------------------------------------------------- review 136 findings (repros)
def test_a_step_written_after_its_successor_is_still_captured(env):
    """HIGH: the cursor was "highest index + 1", so a step that arrived after its successor was lost."""
    d = _logdir(env)
    lines = transcript(12)
    last = 10 + 3 * 11
    late = _user(last, "LATE owner message: important decision")
    after = _plan(last + 1, "answer to the late message " * 5)
    tp = _write(d / "transcript.jsonl", [*lines, after])  # the successor is written first
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6)["status"] == "ok"
    assert "LATE owner" not in summ.texts[0]
    _write(d / "transcript.jsonl", [*lines, after, late, *turns(12, last + 2, tag="more")])
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 60)
    assert r["status"] == "ok" and "LATE owner message" in summ.texts[1]
    assert "answer to the late message" not in summ.texts[1]  # already captured: not twice
    st = RUN.load_state(C.state_dir(), CID)
    assert last in st["recent_steps"] and last + 1 in st["recent_steps"]
    assert (
        RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 120)["status"] == "nothing_new"
    )


def test_b_failing_segment_backs_off_instead_of_resummarizing_every_stop(env, monkeypatch):
    """MEDIUM: residual_secret / payload_invalid saved no state, so every Stop re-ran the summarizer."""
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    send, summ = Sender(), Summarizer()
    monkeypatch.setattr(RUN, "residual_findings", lambda t: ["x"])
    statuses = [
        RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + k)["status"] for k in range(4)
    ]
    assert statuses == ["error", "backoff", "backoff", "backoff"] and len(summ.texts) == 1
    st = RUN.load_state(C.state_dir(), CID)
    assert st["errors"] == 1 and st["retry_after"] == 1e6 + RUN.BACKOFF_BASE_S and st["captured_lines"] == 0
    # after the backoff, the same input is not summarized again ...
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 700)
    assert r["status"] == "unchanged_after_error" and len(summ.texts) == 1
    # ... but a grown segment is, and a second failure doubles the wait
    _write(d / "transcript.jsonl", [*lines, *turns(1, 100, tag="new")])
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 800)["status"] == "error"
    st = RUN.load_state(C.state_dir(), CID)
    assert (
        len(summ.texts) == 2 and st["errors"] == 2 and st["retry_after"] == 1e6 + 800 + 2 * RUN.BACKOFF_BASE_S
    )
    monkeypatch.setattr(RUN, "residual_findings", lambda t: [])
    _write(d / "transcript.jsonl", [*lines, *turns(2, 100, tag="new")])  # the segment grows again
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 800 + 1300)
    assert r["status"] == "ok" and len(send.calls) == 1
    st = RUN.load_state(C.state_dir(), CID)
    assert "errors" not in st and "retry_after" not in st and "failed_fp" not in st


def test_c_tail_without_owner_message_is_flushed_once_a_segment_exists(env):
    """MEDIUM: a tail with 0 owner messages (a turn's final answer) was skip_small + swept = lost."""
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6)["status"] == "ok"
    _write(d / "transcript.jsonl", [*lines, _plan(500, "FINAL CONCLUSION of the turn " * 20)])
    (res,) = RUN.sweep_agy(now=1e6 + 4000, summarize_fn=summ, send_fn=send)
    assert res["status"] == "ok" and res["segment"] == 1 and "FINAL CONCLUSION" in summ.texts[-1]
    assert RUN.sweep_agy(now=1e6 + 9000, summarize_fn=summ, send_fn=send) == []


def test_d_failing_conversations_do_not_starve_the_sweep(env):
    """MEDIUM: the sweep took the 5 oldest candidates and an error never set swept_stop, so 5 failing
    conversations blocked every newer idle tail."""
    summ = Summarizer()
    ids = []
    for k in range(6):
        cid = f"7e1d0c2b-aaaa-4bbb-8ccc-00000000000{k}"
        tp = _write(env / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl", transcript(5))
        r = RUN.process(
            {**agy_hook(tp), "session_id": cid}, summarize_fn=summ, send_fn=Sender(), now=1000.0 + k
        )
        assert r["status"] == "below_threshold"
        ids.append(cid)
    calls: list[str] = []

    def sender(payload, cfg, dry, name):  # noqa: ANN001
        calls.append(name)
        ok = name.startswith(ids[5])
        return W.WriteOutcome("ok" if ok else "error", "test", "d" if ok else "E_INVALID_ARG")

    first = RUN.sweep_agy(now=99999.0, summarize_fn=summ, send_fn=sender)
    assert [r["status"] for r in first] == ["error"] * 5
    second = RUN.sweep_agy(now=99999.0, summarize_fn=summ, send_fn=sender)
    assert [r["status"] for r in second] == ["ok"] and calls[-1].startswith(ids[5])
    assert RUN.sweep_agy(now=99999.0, summarize_fn=summ, send_fn=sender) == []  # all backed off or done
    later = RUN.sweep_agy(now=99999.0 + RUN.BACKOFF_BASE_S, summarize_fn=summ, send_fn=sender)
    assert [r.get("resent_pending") for r in later] == [True] * 5  # retried from the stored payload
    assert len(summ.texts) == 6  # the failing five were summarized once each, never again


def test_stop_waits_for_a_quiet_log_and_the_sweep_skips_a_busy_one(env, monkeypatch):
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    monkeypatch.setattr(RUN, "STOP_QUIET_S", 30.0)
    monkeypatch.setattr(RUN, "STOP_MAX_WAIT_S", 0.2)
    send, summ = Sender(), Summarizer()
    r = RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)
    assert r["status"] == "busy" and summ.texts == []
    st = RUN.load_state(C.state_dir(), CID)  # review 135 r2: a busy Stop records itself for the idle flush
    assert st.get("last_stop_at") and "segments" not in st
    old = time.time() - 3600
    os.utime(tp, (old, old))
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6)["status"] == "ok"
    _write(d / "transcript.jsonl", [*lines, *turns(1, 100, tag="tail")])  # fresh write: still busy
    monkeypatch.setattr(RUN, "SWEEP_QUIET_S", 120.0)
    assert RUN.sweep_agy(now=1e6 + 4000, summarize_fn=summ, send_fn=send) == []
    os.utime(tp, (old, old))
    (res,) = RUN.sweep_agy(now=1e6 + 4000, summarize_fn=summ, send_fn=send)
    assert res["status"] == "ok" and "tail owner message 0" in summ.texts[-1]


def test_per_turn_statuses_write_no_log_line(env):
    tp = _write(_logdir(env) / "transcript.jsonl", transcript(4))
    log = C.state_dir() / "capture.log"
    for _ in range(3):
        assert RUN.safe_process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=Sender())["status"] == (
            "below_threshold"
        )
    assert not log.exists()
    _write(tp, transcript(12))
    assert RUN.safe_process(agy_hook(tp), summarize_fn=Summarizer(), send_fn=Sender())["status"] == "ok"
    (line,) = log.read_text().splitlines()
    assert "status=ok" in line and "client=agy" in line


def test_busy_stop_after_a_flush_still_reaches_the_idle_flush(env, monkeypatch):
    """Review 135 r2 (MEDIUM): flushed conversation resumes, its next Stop is busy, agy exits; the tail
    must still be written by a later sweep."""
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    old = time.time() - 3600
    os.utime(tp, (old, old))
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6)["status"] == "ok"
    _write(d / "transcript.jsonl", [*lines, *turns(1, 100, tag="late")])  # resumed; log still being written
    monkeypatch.setattr(RUN, "STOP_QUIET_S", 30.0)
    monkeypatch.setattr(RUN, "STOP_MAX_WAIT_S", 0.2)
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send, now=1e6 + 10)["status"] == "busy"
    os.utime(tp, (old, old))  # agy closed; the log goes quiet
    (res,) = RUN.sweep_agy(now=1e6 + 4000, summarize_fn=summ, send_fn=send)
    assert res["status"] == "ok" and "late owner message 0" in summ.texts[-1]


def test_cursor_saved_without_recent_steps_does_not_recapture_its_window(env):
    """Review 135 r2 (LOW): a state from before recent_steps existed must not capture its last
    LATE_WINDOW steps a second time."""
    d = _logdir(env)
    lines = transcript(12)
    tp = _write(d / "transcript.jsonl", lines)
    send, summ = Sender(), Summarizer()
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)["status"] == "ok"
    st = RUN.load_state(C.state_dir(), CID)
    st.pop("recent_steps")
    RUN.save_state(C.state_dir(), CID, st)
    _write(d / "transcript.jsonl", [*lines, *turns(12, 100, tag="next")])
    assert RUN.process(agy_hook(tp), summarize_fn=summ, send_fn=send)["status"] == "ok"
    assert "next owner message 0" in summ.texts[-1] and "turn owner message" not in summ.texts[-1]
