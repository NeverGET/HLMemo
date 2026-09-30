"""GOAL-PLAN B1 session capture: reduction, scrub, idempotency/lock, validation fallback, no-op, kill switch.

No network, no prod, no real ``claude``: the summarizer and the writer are injected.
"""

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
from hlmemo.capture import reduce as R
from hlmemo.capture import run as RUN
from hlmemo.capture import summarize as S
from hlmemo.capture import write as W
from hlmemo.capture.scrub import ScrubReport, line_secret, residual_findings, scrub_text

# Secret-shaped strings are assembled at runtime so that no scanner flags this file.
FAKE_SK = "sk-" + "ant-" + "A1b2C3d4" * 4
FAKE_GH = "gh" + "p_" + "Ab3" * 13
FAKE_HLM = "hlm" + "_" + "Zx9" * 14 + "Q"
FAKE_EMAIL = "someone.private" + "@" + "example-corp.de"
SID = "0a1b2c3d-1111-2222-3333-444455556666"


# --------------------------------------------------------------------------- transcript builders
def _u(text: Any, ts: str = "2026-09-30T10:00:00.000Z", **kw: Any) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": ts,
            "sessionId": SID,
            "cwd": "/w/proj",
            "message": {"role": "user", "content": text},
            **kw,
        }
    )


def _a(blocks: list[dict[str, Any]], ts: str = "2026-09-30T10:01:00.000Z", **kw: Any) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "sessionId": SID,
            "message": {"role": "assistant", "content": blocks},
            **kw,
        }
    )


def _text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def _tool_use(name: str, **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": "t1", "name": name, "input": inp}


def _result(text: str) -> str:
    return _u([{"type": "tool_result", "tool_use_id": "t1", "content": text}])


def sample_transcript(n_owner: int = 4) -> list[str]:
    lines = [_u("Please fix the importer bug in src/imp.py and commit it")]
    lines.append(
        _a([{"type": "thinking", "thinking": "SECRET THINKING"}, _text("I will look at the importer.")])
    )
    lines.append(_a([_tool_use("Edit", file_path="/w/proj/src/imp.py", old_string="a", new_string="b")]))
    lines.append(_result("HUGE TOOL OUTPUT " * 500))
    commit_cmd = 'git add -A && git commit -q -m "fix importer dedupe"'
    lines.append(_a([_tool_use("Bash", command=commit_cmd)]))
    lines.append(_result("[main abc1234] fix importer dedupe\n 1 file changed, 2 insertions(+)"))
    lines.append(
        _a([_text("Decided: dedupe by sha256 (commit abc1234). Root cause: the old key ignored paths. " * 6)])
    )
    for i in range(1, n_owner):
        lines.append(_u(f"owner follow-up number {i}: ok continue"))
        lines.append(_a([_text(f"done step {i}, nothing else is open" * 20)]))
    return lines


def _write(tmp_path: Path, lines: list[str], name: str = "t.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated state dir + capture.toml mapping /w/proj -> hlmemo; kill switch cleared."""
    monkeypatch.delenv(C.KILL_ENV, raising=False)
    monkeypatch.delenv(C.DRYRUN_ENV, raising=False)
    monkeypatch.setenv(C.STATE_ENV, str(tmp_path / "state"))
    cfgp = tmp_path / "capture.toml"
    cfgp.write_text('[projects]\n"/w/proj" = "hlmemo"\n', encoding="utf-8")
    monkeypatch.setenv(C.CONFIG_ENV, str(cfgp))
    return tmp_path


# --------------------------------------------------------------------------- reduction
def test_reduce_keeps_owner_and_assistant_drops_noise():
    lines = sample_transcript()
    lines.insert(1, _u("<system-reminder>hook text should vanish</system-reminder>"))
    lines.insert(2, _u("<task-notification>agent finished</task-notification>"))
    lines.insert(3, _u("Stop hook feedback: nope"))
    lines.insert(4, _u("sidechain owner text", isSidechain=True))
    red = R.reduce_lines(lines)
    assert red.owner_messages == 4
    assert "fix the importer bug" in red.text
    assert "Decided: dedupe by sha256" in red.text
    for noise in (
        "HUGE TOOL OUTPUT",
        "SECRET THINKING",
        "hook text should vanish",
        "agent finished",
        "Stop hook feedback",
        "sidechain owner text",
    ):
        assert noise not in red.text
    assert "src/imp.py" in red.files[0]
    assert ("abc1234", "fix importer dedupe") in red.commits
    assert red.commit_messages == ["fix importer dedupe"]
    assert "abc1234 fix importer dedupe" in red.text
    assert red.first_ts and red.last_ts


def test_reduce_caps_size_and_keeps_recent_and_decisions():
    lines = [_u("the goal of this session: migrate the store")]
    for i in range(400):
        lines.append(_u(f"owner message {i}"))
        body = f"filler paragraph {i} " * 120
        if i == 37:
            body = "We decided to reject option B because of data loss (D-123). " + body
        lines.append(_a([_text(body)]))
    lines.append(_u("FINAL owner message"))
    lines.append(_a([_text("FINAL assistant conclusion")]))
    red = R.reduce_lines(lines, cap=40_000)
    assert len(red.text) <= 40_000
    assert "the goal of this session" in red.text  # first owner message kept
    assert "FINAL assistant conclusion" in red.text  # tail kept
    assert "omitted" in red.text
    assert red.segments_kept < red.segments_total


def test_reduce_resume_offset_and_partial_last_line():
    lines = sample_transcript()
    red1 = R.reduce_lines(lines + ['{"type": "user", "message": {"con'])  # live writer, half a line
    assert red1.end_line == len(lines)  # the partial line is left for the next segment
    more = [_u("new owner ask after the checkpoint"), _a([_text("new conclusion")])]
    red2 = R.reduce_lines([*lines, *more], start=red1.end_line)
    assert "new owner ask" in red2.text and "fix the importer bug" not in red2.text
    assert red2.owner_messages == 1


def test_reduce_compact_summary_skipped_in_later_segment():
    lines = [
        _u("first"),
        _u("This session is being continued from a previous conversation ... SUMMARY"),
        _u("second owner message"),
    ]
    assert "SUMMARY" in R.reduce_lines(lines).text
    assert "SUMMARY" not in R.reduce_lines(lines, start=1).text


def test_reduce_mcp_edit_tool_paths():
    lines = [_u("edit"), _a([_tool_use("mcp__filesystem-with-morph__edit_file", path="/w/proj/a.py")])]
    assert R.reduce_lines(lines, cwd_hint="/w/proj").files == ["a.py"]


# --------------------------------------------------------------------------- scrub
def test_scrub_drops_secret_lines_and_masks_emails():
    text = "\n".join(
        [
            "keep this line",
            f"export ANTHROPIC_KEY={FAKE_SK}",
            f"token {FAKE_GH} leaked",
            f"use {FAKE_HLM} to register",
            f"contact {FAKE_EMAIL} about it",
            "postgresql://hlm:Sup3rSecretPw@db.internal:5432/hlm",
            "Authorization: Bearer abcdefghijklmnop1234567890",
            "-----BEGIN RSA " + "PRIVATE KEY-----",
            "keep this too",
        ]
    )
    rep = ScrubReport()
    out = scrub_text(text, rep)
    assert out.split("\n")[0] == "keep this line" and out.split("\n")[-1] == "keep this too"
    assert FAKE_SK not in out and FAKE_GH not in out and FAKE_HLM not in out and "Sup3rSecretPw" not in out
    assert FAKE_EMAIL not in out and "[email]" in out
    assert rep.lines_dropped == 6 and rep.emails_masked == 1
    assert residual_findings(out) == []
    assert scrub_text(out) == out  # idempotent


def test_scrub_credential_pair_across_lines_and_false_positives():
    pair = f"login: {FAKE_EMAIL}\npassword: Xy7!longpass99"
    out = scrub_text("a\n" + pair + "\nb")
    assert out == "a\n[email]\nb" or out == "a\nb"  # the password line is gone in both cases
    assert "longpass99" not in out
    ok = "commit 1f2e3d4c5b6a79881f2e3d4c5b6a79881f2e3d4c and path /Users/x/Projects/HLMemo/src/hlmemo/a.py"
    assert line_secret(ok) is None
    assert line_secret("see docs/decisions/DECISIONS.md D-216 and the token budget of 1500") is None


# --------------------------------------------------------------------------- summarizer validation
GOOD = {
    "notes": "Fixed the importer dedupe in src/imp.py (commit abc1234).",
    "decisions": ["Dedupe by sha256 of the content."],
    "lessons": [
        {
            "title": "Dedupe key must include path",
            "body": "Rule: ...",
            "tags": ["importer"],
            "evidence": "Root cause: the old key ignored paths.",
        }
    ],
    "uncertain": [],
}


def test_validate_summary_ok_and_grounding():
    text = "Decided: dedupe by sha256 (commit abc1234). Root cause: the old key ignored paths."
    s = S.validate_summary(GOOD, text)
    assert len(s.lessons) == 1 and s.uncertain == [] and s.dropped == {}


def test_validate_summary_drops_ungrounded_lesson_and_flags_unknown_refs():
    bad = json.loads(json.dumps(GOOD))
    bad["lessons"][0]["evidence"] = "a sentence that never occurred in the transcript at all"
    bad["notes"] += " Also see commit deadbeef1 and D-999."
    s = S.validate_summary(bad, "Decided: dedupe (commit abc1234).")
    assert s.lessons == [] and s.dropped["lessons_ungrounded"] == 1
    assert any("deadbeef1" in u and "D-999" in u for u in s.uncertain)


@pytest.mark.parametrize(
    "obj",
    [
        None,
        [],
        "x",
        {"notes": ""},
        {"notes": "n", "decisions": "no", "lessons": [], "uncertain": []},
        {"notes": "n", "decisions": [], "lessons": [1], "uncertain": []},
        {**GOOD, "extra": 1},
    ],
)
def test_validate_summary_rejects_malformed(obj):
    with pytest.raises(S.SummaryInvalid):
        S.validate_summary(obj, "t")


def test_validate_summary_caps():
    big = {"notes": "w " * 2000, "decisions": [f"d{i}" for i in range(20)], "lessons": [], "uncertain": []}
    s = S.validate_summary(big, "t")
    assert len(s.decisions) == 12 and len(s.notes.split()) <= 1510 and "clipped" in s.notes


# --------------------------------------------------------------------------- pipeline: idempotency, lock
class Sender:
    def __init__(self, statuses: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.statuses = statuses or []

    def __call__(self, payload, cfg, dry, name):  # noqa: ANN001
        self.calls.append(payload)
        st = self.statuses.pop(0) if self.statuses else "ok"
        return W.WriteOutcome(st, "test", "detail")


def llm_ok(text, cwd, date, cfg):  # noqa: ANN001
    return GOOD, S.LlmRun(data=GOOD, latency_s=1.5, cost_usd=0.02, input_tokens=9000, output_tokens=600)


def llm_garbage(text, cwd, date, cfg):  # noqa: ANN001
    return {"notes": 42}, S.LlmRun(data={"notes": 42}, latency_s=1.0)


def llm_down(text, cwd, date, cfg):  # noqa: ANN001
    return None, S.LlmRun(error="claude_exit_1")


def hook_for(tp: Path, event: str = "SessionEnd") -> dict[str, Any]:
    return {"session_id": SID, "transcript_path": str(tp), "cwd": "/w/proj", "hook_event_name": event}


def test_same_session_written_once_and_payload_shape(env):
    tp = _write(env, sample_transcript())
    send = Sender()
    r1 = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=send, lock_timeout_s=1)
    r2 = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=send, lock_timeout_s=1)
    assert r1["status"] == "ok" and r2["status"] == "nothing_new"
    assert len(send.calls) == 1
    p = send.calls[0]
    assert p["project"] == "hlmemo" and p["client"] == "claude-code/capture-hook"
    assert p["session_id"] == W.session_key(SID, "hlmemo")  # stable uuid5
    assert W.session_key(SID, "hlmemo") == W.session_key(SID, "hlmemo") != W.session_key(SID, "other-proj")
    assert "card_update" not in p and p["decisions"] == GOOD["decisions"]
    assert "auto-capture" in p["lessons"][0]["tags"]
    assert "abc1234 fix importer dedupe" in p["notes"]  # programmatic commit facts
    W.validate_payload(p)  # the server's own CloseRequest accepts it
    assert r1["cost_usd"] == 0.02 and r1["lessons"] == 1


def test_later_checkpoint_writes_only_new_segment(env):
    lines = sample_transcript()
    tp = _write(env, lines)
    send = Sender()
    RUN.process(hook_for(tp, "PreCompact"), summarize_fn=llm_ok, send_fn=send, lock_timeout_s=1)
    more = [_u(f"second-part owner msg {i}") for i in range(3)] + [_a([_text("second part conclusion")])]
    _write(env, [*lines, *more])
    r = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=send, lock_timeout_s=1)
    assert r["status"] == "ok" and r["segment"] == 1
    assert len(send.calls) == 2
    assert send.calls[0]["session_id"] != send.calls[1]["session_id"]
    assert send.calls[1]["session_id"] == W.session_key(SID, "hlmemo", 1)


def test_failed_write_is_retried_with_same_request_and_already_closed_counts_as_done(env):
    tp = _write(env, sample_transcript())
    send = Sender(["error"])
    r1 = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=send, lock_timeout_s=1)
    assert r1["status"] == "error"
    r2 = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=Sender(["already_closed"]), lock_timeout_s=1)
    assert r2["status"] == "nothing_new"  # pending resent (server says closed) -> done, nothing new
    send3 = Sender(["error", "ok"])
    tp2 = _write(env, sample_transcript(), "t2.jsonl")
    h = {**hook_for(tp2), "session_id": "bbbb0000-1111-2222-3333-444455556666"}
    RUN.process(h, summarize_fn=llm_ok, send_fn=send3, lock_timeout_s=1)
    RUN.process(h, summarize_fn=llm_ok, send_fn=send3, lock_timeout_s=1)
    assert len(send3.calls) == 2 and send3.calls[0] == send3.calls[1]  # identical payload, same request_id


def test_lock_blocks_concurrent_worker(env):
    tp = _write(env, sample_transcript())
    root = C.state_dir()
    with RUN.session_lock(root, SID, 1) as got:
        assert got
        r = RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=Sender(), lock_timeout_s=0.3)
    assert r["status"] == "locked"
    # lock is released afterwards
    assert (
        RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=Sender(), lock_timeout_s=1)["status"] == "ok"
    )


@pytest.mark.parametrize("fn", [llm_garbage, llm_down])
def test_invalid_or_failed_summary_writes_minimal_note_only(env, fn):
    tp = _write(env, sample_transcript())
    send = Sender()
    r = RUN.process(hook_for(tp), summarize_fn=fn, send_fn=send, lock_timeout_s=1)
    assert r["status"] == "ok" and r["llm"] == "fallback" and r["llm_error"]
    p = send.calls[0]
    assert "automatic summary was unavailable" in p["notes"]
    assert "abc1234 fix importer dedupe" in p["notes"]
    assert "decisions" not in p and "lessons" not in p
    assert "Decided: dedupe" not in p["notes"]  # no transcript prose without a validated summary
    W.validate_payload(p)


def test_small_session_skipped_and_residual_secret_blocks_write(env):
    tp = _write(env, [_u("just one prompt"), _a([_text("ok")])])
    assert (
        RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=Sender(), lock_timeout_s=1)["status"]
        == "skip_small"
    )
    tp2 = _write(env, sample_transcript(), "t3.jsonl")
    leak = {**GOOD, "decisions": [f"use {FAKE_SK} for the api"]}  # model echoes a secret back
    send = Sender()
    r = RUN.process(
        hook_for(tp2), summarize_fn=lambda *a: (leak, S.LlmRun(data=leak)), send_fn=send, lock_timeout_s=1
    )
    assert r["status"] == "ok" and FAKE_SK not in json.dumps(send.calls[0])  # the output scrub dropped it


def test_summary_and_payload_carry_no_secret_or_email(env):
    lines = sample_transcript() + [_u(f"my mail is {FAKE_EMAIL} and key {FAKE_SK}"), _a([_text("noted")])]
    tp = _write(env, lines)
    seen: list[str] = []

    def spy(text, cwd, date, cfg):  # noqa: ANN001
        seen.append(text)  # what the LLM would receive
        return GOOD, S.LlmRun(data=GOOD)

    send = Sender()
    RUN.process(hook_for(tp), summarize_fn=spy, send_fn=send, lock_timeout_s=1)
    assert FAKE_SK not in seen[0] and FAKE_EMAIL not in seen[0]
    assert residual_findings(seen[0]) == [] and residual_findings(W.payload_text(send.calls[0])) == []


def test_dry_run_writes_json_file_not_prod(env, tmp_path):
    tp = _write(env, sample_transcript())
    dry = str(tmp_path / "dry")
    r = RUN.process(hook_for(tp), summarize_fn=llm_ok, dry_dir=dry, lock_timeout_s=1)
    assert r["status"] == "ok"
    out = Path(dry) / f"{SID}-seg0.json"
    assert out.is_file() and oct(out.stat().st_mode & 0o777) == "0o600"
    assert json.loads(out.read_text())["project"] == "hlmemo"


# --------------------------------------------------------------------------- mapping, kill switch, hook
def test_unmapped_cwd_is_a_noop(env):
    tp = _write(env, sample_transcript())
    hk = {**hook_for(tp), "cwd": "/somewhere/else"}
    send = Sender()
    assert RUN.process(hk, summarize_fn=llm_ok, send_fn=send)["status"] == "unmapped"
    assert send.calls == []
    assert H.handle(json.dumps(hk), spawn=False) == "unmapped"
    assert not (C.state_dir() / "queue").exists()


def test_missing_mapping_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.delenv(C.KILL_ENV, raising=False)
    monkeypatch.setenv(C.CONFIG_ENV, str(tmp_path / "nope.toml"))
    monkeypatch.setenv(C.STATE_ENV, str(tmp_path / "state"))
    assert (
        H.handle(
            json.dumps(
                {
                    "hook_event_name": "SessionEnd",
                    "session_id": SID,
                    "transcript_path": "/x",
                    "cwd": "/w/proj",
                }
            ),
            spawn=False,
        )
        == "unmapped"
    )


def test_mapping_longest_prefix_and_component_boundary(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        '[projects]\n"/w/proj" = "hlmemo"\n"/w/proj/sub" = "other-one"\n'
        '[capture]\nexclude = [".claude/worktrees"]\n'
    )
    cfg = C.load_config(p)
    assert C.map_cwd(cfg, "/w/proj/x") == "hlmemo"
    assert C.map_cwd(cfg, "/w/proj/sub/deep") == "other-one"
    assert C.map_cwd(cfg, "/w/project2") is None  # not a path-component prefix
    assert C.map_cwd(cfg, "/w/proj/.claude/worktrees/agent-1") is None  # excluded


@pytest.mark.parametrize("val", ["off", "OFF", "0", "false"])
def test_kill_switch(env, monkeypatch, val):
    tp = _write(env, sample_transcript())
    monkeypatch.setenv(C.KILL_ENV, val)
    send = Sender()
    assert RUN.process(hook_for(tp), summarize_fn=llm_ok, send_fn=send)["status"] == "killed"
    assert send.calls == []
    assert H.handle(json.dumps(hook_for(tp)), spawn=False) == "killed"


def test_hook_queues_and_detaches_fast(env):
    tp = _write(env, sample_transcript())
    assert H.handle(json.dumps(hook_for(tp)), spawn=False) == "spawned"
    q = list((C.state_dir() / "queue").glob("*.json"))
    assert len(q) == 1 and oct(q[0].stat().st_mode & 0o777) == "0o600"
    assert json.loads(q[0].read_text())["session_id"] == SID


def test_hook_subprocess_exits_zero_fast_on_garbage_and_unmapped(env):
    base = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    for stdin in (
        "not json at all",
        json.dumps({"hook_event_name": "SessionEnd", "cwd": "/nope", "session_id": "x"}),
    ):
        t0 = time.monotonic()
        p = subprocess.run(
            [sys.executable, "-m", "hlmemo.capture.hook"],
            input=stdin,
            text=True,
            capture_output=True,
            env=base,
            timeout=10,
        )
        assert p.returncode == 0 and time.monotonic() - t0 < 1.0 and p.stdout == ""


def test_worker_never_raises_on_bad_input(env):
    assert (
        RUN.safe_process({"session_id": SID, "cwd": "/w/proj", "transcript_path": "/does/not/exist"})[
            "status"
        ]
        == "bad_input"
    )
    assert RUN.safe_process({})["status"] == "unmapped"


def test_occurred_at_never_in_the_future():
    from datetime import UTC, datetime, timedelta

    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    parsed = datetime.fromisoformat(W.occurred_at(future).replace("Z", "+00:00"))
    assert parsed <= datetime.now(UTC) + timedelta(minutes=5)
    assert W.occurred_at("2026-09-30T10:01:00.000Z") == "2026-09-30T10:01:00Z"
    assert W.occurred_at("garbage").endswith("Z")
