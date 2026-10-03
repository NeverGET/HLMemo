"""``hlm curate`` workers: output schemas, briefs, the agent command template, retry-once and
resumability. The "agents" are tiny local Python scripts; no model is ever called."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from hlmemo.curate import workers as w
from hlmemo.curate.bundle import HISTORY_POLICY_HEADING

SLICE1 = {"candidates": [{"cid": "c0001"}, {"cid": "c0002"}]}
SLICE2 = {"records": [{"i": 0}, {"i": 1}]}


def p1(cid: str, verdict: str = "NO_CONFLICT", **kw: Any) -> dict[str, Any]:
    return {"cid": cid, "verdict": verdict, "why": "w", **kw}


def p2(i: int, verdict: str = "KEEP", **kw: Any) -> dict[str, Any]:
    return {"i": i, "verdict": verdict, "failed_tests": [], "reason": "r", **kw}


# ------------------------------------------------------------------ validation
def test_pass1_validation() -> None:
    ok = {"verdicts": [p1("c0001"), p1("c0002", "SUPERSESSION", newer_logical_id=2, older_logical_id=1)]}
    assert w.validate_pass1(ok, SLICE1) == []
    assert any("missing cid" in e for e in w.validate_pass1({"verdicts": [p1("c0001")]}, SLICE1))
    dup = {"verdicts": [p1("c0001"), p1("c0001"), p1("c0002")]}
    assert any("answered twice" in e for e in w.validate_pass1(dup, SLICE1))
    extra = {"verdicts": [p1("c0001"), p1("c0002"), p1("c0009")]}
    assert any("not in your slice" in e for e in w.validate_pass1(extra, SLICE1))
    assert any(
        "enum" in e or "is not one of" in e
        for e in w.validate_pass1({"verdicts": [p1("c0001", "MAYBE")]}, SLICE1)
    )
    no_ids = {"verdicts": [p1("c0001"), p1("c0002", "SUPERSESSION")]}
    assert any("needs newer_logical_id" in e for e in w.validate_pass1(no_ids, SLICE1))
    assert w.validate_pass1({"not": "it"}, SLICE1) == ["(root): 'verdicts' is a required property"]


def test_pass2_validation() -> None:
    assert w.validate_pass2({"verdicts": [p2(0), p2(1, "DROP", failed_tests=[1])]}, SLICE2) == []
    assert any(
        "names its failed_tests" in e for e in w.validate_pass2({"verdicts": [p2(0), p2(1, "DROP")]}, SLICE2)
    )
    nofix = {"verdicts": [p2(0), p2(1, "FIX", fix=None)]}
    assert any("a FIX needs" in e for e in w.validate_pass2(nofix, SLICE2))
    novid = {"verdicts": [p2(0), p2(1, "FIX", fix={"src_logical_id": 3})]}
    assert any("needs src_vid" in e for e in w.validate_pass2(novid, SLICE2))
    bad_test = {"verdicts": [p2(0, "DROP", failed_tests=[6]), p2(1)]}
    assert w.validate_pass2(bad_test, SLICE2)
    good_fix = {"verdicts": [p2(0), p2(1, "FIX", failed_tests=[4], fix={"older_span": "s"})]}
    assert w.validate_pass2(good_fix, SLICE2) == []


def test_map_validation() -> None:
    ok = {
        "pairs": [{"newer_logical_id": 2, "older_logical_id": 1, "why": "w", "area": "a"}],
        "file_fixes": [],
    }
    assert w.validate_map(ok, {"files": []}) == []
    assert w.validate_map({"pairs": [{"newer_logical_id": "2", "older_logical_id": 1, "why": "w"}]}, {})


# ------------------------------------------------------------------ briefs
def test_history_policy_is_verbatim_in_every_brief() -> None:
    blocks = []
    for stage in w.BRIEFS:
        text = w.brief_text(stage)
        start = text.index(HISTORY_POLICY_HEADING)
        end = text.index("\n\n", start + len(HISTORY_POLICY_HEADING) + 1)
        blocks.append(text[start:end])
        assert "Dated findings" in blocks[-1] and "PRESENT-tense" in blocks[-1]
    assert len(set(blocks)) == 1 and len(blocks) == 3


def test_render_brief_fills_every_placeholder() -> None:
    values = {
        "PROJECT": "demo",
        "TODAY": "2026-10-03",
        "EXPORT_DIR": "/x/export",
        "SLICE_FILE": "/x/slice.json",
        "OUTPUT_FILE": "/x/out.json",
        "REFERENCES": "- No other files are available.",
    }
    for stage in w.BRIEFS:
        text = w.render_brief(stage, values)
        assert "{{" not in text and "/x/out.json" in text and "/x/slice.json" in text
        assert "/Users/" not in text and "/private/" not in text  # no absolute paths baked in
    with pytest.raises(ValueError, match="unfilled placeholder"):
        w.render_brief("pass1", {k: v for k, v in values.items() if k != "TODAY"})


def test_render_argv_placeholders() -> None:
    argv = w.render_argv(
        "claude -p --model sonnet --add-dir {export} --allowedTools 'Read,Write'",
        {"export": "/e x", "workdir": "/w", "slice": "/s", "out": "/o"},
    )
    assert argv == ["claude", "-p", "--model", "sonnet", "--add-dir", "/e x", "--allowedTools", "Read,Write"]
    with pytest.raises(ValueError, match="no agent command"):
        w.render_argv("  ", {})


# ------------------------------------------------------------------ running
AGENT = r"""
import json, os, sys
from pathlib import Path
prompt = sys.stdin.read()
wd = Path(os.environ["HLM_CURATE_WORKDIR"])
calls = wd.parent / "calls.txt"
calls.write_text((calls.read_text() if calls.exists() else "") + os.environ["HLM_CURATE_ATTEMPT"] + "\n")
mode = sys.argv[1]
sl = json.loads(Path(os.environ["HLM_CURATE_SLICE"]).read_text())
out = Path(os.environ["HLM_CURATE_OUT"])
if mode == "never" or (mode == "once" and os.environ["HLM_CURATE_ATTEMPT"] == "1"):
    out.write_text("not json")
    sys.exit(0)
if mode == "crash":
    sys.exit(7)
if mode == "stray":
    (wd / "extra.txt").write_text("x")
if "RETRY" in prompt:
    (wd.parent / "saw_retry").write_text("1")
verdicts = [{"cid": c["cid"], "verdict": "UNCLEAR", "why": "w"} for c in sl["candidates"]]
out.write_text(json.dumps({"verdicts": verdicts}))
"""


def _job(tmp: Path, wid: str = "w01") -> w.Job:
    return w.Job(stage="pass1", wid=wid, workdir=tmp / "pass1" / wid, slice_obj=SLICE1, prompt="BRIEF\n")


def _cmd(tmp: Path, mode: str) -> str:
    script = tmp / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    return f"{sys.executable} {script} {mode}"


def _calls(tmp: Path) -> list[str]:
    f = tmp / "pass1" / "calls.txt"
    return f.read_text().split() if f.exists() else []


def test_run_job_ok_and_resumable(tmp_path: Path) -> None:
    res = w.run_job(_job(tmp_path), _cmd(tmp_path, "ok"), tmp_path, timeout_s=60)
    assert res.status == "ok" and res.attempts == 1 and len(res.output["verdicts"]) == 2
    status = json.loads((tmp_path / "pass1" / "w01" / "status.json").read_text())
    assert status["status"] == "ok" and status["stray_files"] == []
    again = w.run_job(_job(tmp_path), _cmd(tmp_path, "ok"), tmp_path, timeout_s=60)
    assert again.status == "skipped" and _calls(tmp_path) == ["1"]  # the agent did not run again
    changed = _job(tmp_path)
    changed.slice_obj = {"candidates": [{"cid": "c0001"}]}
    assert w.run_job(changed, _cmd(tmp_path, "ok"), tmp_path, timeout_s=60).status == "ok"
    assert _calls(tmp_path) == ["1", "1"]  # a changed slice runs again


def test_run_job_retries_once_with_the_errors(tmp_path: Path) -> None:
    res = w.run_job(_job(tmp_path), _cmd(tmp_path, "once"), tmp_path, timeout_s=60)
    assert res.status == "ok" and res.attempts == 2 and _calls(tmp_path) == ["1", "2"]
    assert (tmp_path / "pass1" / "saw_retry").exists()
    retry_prompt = (tmp_path / "pass1" / "w01" / "prompt-2.md").read_text()
    assert "RETRY" in retry_prompt and "not valid JSON" in retry_prompt


@pytest.mark.parametrize(("mode", "needle"), [("never", "not valid JSON"), ("crash", "agent exited 7")])
def test_run_job_fails_after_two_attempts(tmp_path: Path, mode: str, needle: str) -> None:
    res = w.run_job(_job(tmp_path), _cmd(tmp_path, mode), tmp_path, timeout_s=60)
    assert res.status == "failed" and res.attempts == 2 and any(needle in e for e in res.errors)
    status = json.loads((tmp_path / "pass1" / "w01" / "status.json").read_text())
    assert status["status"] == "failed" and status["errors"]


def test_run_job_missing_agent_and_timeout(tmp_path: Path) -> None:
    res = w.run_job(_job(tmp_path), "/nonexistent/agent-binary", tmp_path, timeout_s=60)
    assert res.status == "failed" and "could not start the agent" in res.errors[0]
    slow = tmp_path / "slow.py"
    slow.write_text("import time, sys; sys.stdin.read(); time.sleep(5)\n")
    res = w.run_job(_job(tmp_path, "w02"), f"{sys.executable} {slow}", tmp_path, timeout_s=0.5)
    assert res.status == "failed" and "timed out" in res.errors[0]


def test_run_job_reports_stray_files(tmp_path: Path) -> None:
    res = w.run_job(_job(tmp_path), _cmd(tmp_path, "stray"), tmp_path, timeout_s=60)
    assert res.status == "ok" and res.stray_files == ["extra.txt"]


def test_run_jobs_parallel(tmp_path: Path) -> None:
    jobs = [_job(tmp_path, f"w0{k}") for k in (1, 2, 3)]
    res = w.run_jobs(jobs, _cmd(tmp_path, "ok"), tmp_path, parallel=3, timeout_s=60)
    assert [r.status for r in res] == ["ok", "ok", "ok"] and [r.wid for r in res] == ["w01", "w02", "w03"]
