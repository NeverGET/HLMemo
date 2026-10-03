"""``hlm curate`` end to end with a FAKE agent (``tests/fixtures/curate/fake_agent.py``, canned valid
outputs) over a SYNTHETIC export: the whole pipeline, the preview bundle, resume, failed slices,
the mapping pass, the cross mode, the apply script and the privacy guard. No model is called."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.curate import synth
from typer.testing import CliRunner

from hlmemo.cli.curate import privacy_problem
from hlmemo.cli.hlm import app
from hlmemo.curate import bundle
from hlmemo.ops import backfill_links as bf

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "curate" / "fake_agent.py"
STAGES = [
    "export",
    "candidates",
    "pass1",
    "build",
    "gate1",
    "pass2",
    "refine",
    "gate2",
    "authority",
    "bundle",
]


def agent(answers: Path) -> str:
    return shlex.join([sys.executable, str(FAKE), "--answers", str(answers)])


def curate(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(app, ["curate", *args], env=env or {}, catch_exceptions=False)


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def set_answers(path: Path, **changes: Any) -> None:
    ans = json.loads(path.read_text())
    ans.update(changes)
    path.write_text(json.dumps(ans))


@pytest.fixture
def inp(tmp_path: Path) -> dict[str, Path]:
    return synth.write_inputs(tmp_path / "in", fail={"pass1:w01": 1})  # w01's first attempt is invalid


def new_run(inp: dict[str, Path], run: Path, *extra: str) -> Any:
    return curate(
        "--project",
        "demo",
        "--candidates",
        str(inp["candidates"]),
        "--export",
        str(inp["export"]),
        "--run-dir",
        str(run),
        "--workers",
        "2",
        "--agent-cmd",
        agent(inp["answers"]),
        *extra,
    )


def test_smoke_end_to_end(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    res = new_run(inp, run)
    assert res.exit_code == 0, res.output
    for name in ("final.jsonl", "held.jsonl", "REVIEW.md", "summary.json"):
        assert (run / name).is_file()
    s = json.loads((run / "summary.json").read_text())
    assert (s["final"], s["held"], s["dropped"]) == (2, {"authority": 1}, 1)
    st = s["stages"]
    assert st["candidates"]["candidates"] == 6 and st["candidates"]["skipped"] == {
        "duplicate_pair": 1,
        "no_link_action": 1,
        "not_in_export": 1,
    }
    assert (
        st["pass1"]["verdicts"] == {"NO_CONFLICT": 1, "SUPERSESSION": 5}
        and st["pass1"]["direction_false"] == 3
    )
    assert (st["build"]["records"], st["gate1"]["passed"], st["gate1"]["errors"]) == (
        5,
        4,
        {"already_linked": 1},
    )
    # cross mode by default: both refuters judge all 4 gated records
    assert st["pass2"]["mode"] == "cross" and st["pass2"]["slices"] == 2
    assert st["pass2"]["verdicts"] == {"DROP": 2, "FIX": 2, "KEEP": 4}
    assert st["refine"]["decisions"] == {"DROP": 1, "FIX": 1, "KEEP": 2}
    assert s["pass2_agreement"] == {
        "mode": "cross",
        "refuters_per_record": 2,
        "keep_keep": 2,
        "keep_fix": 0,
        "fix_identical": 1,
        "fix_conflict": 0,
        "drop_unanimous": 1,
        "drop_split": 0,
        "incomplete": 0,
    }
    assert (st["authority"]["final"], st["authority"]["held"]) == (2, 1)
    assert s["failed_slices"] == {"pass1": 0, "pass2": 0}

    final = jsonl(run / "final.jsonl")
    assert [(r["src_logical_id"], r["dst_logical_id"]) for r in final] == [(20, 10), (40, 30)]
    assert final[1]["older_span"] == "the project is PAUSED by the owner" and final[1]["fixed"] is True
    held = jsonl(run / "held.jsonl")
    assert [(h["src_logical_id"], h["held_reason"]) for h in held] == [(50, "authority")]
    # the real consumer accepts the file as it is
    kept, dropped = bf.select(final, "demo")
    assert len(kept) == 2 and dropped == {}
    assert bf.link_props(final[0])["quote"] == final[0]["older_span"]

    review = (run / "REVIEW.md").read_text()
    assert "### F1. v220 → v110" in review and "### F2. v440 → v330" in review
    assert "**Pass 2** (r1): FIX failed [4] (fix: older_span)" in review
    assert "**Pass 2** (r2): FIX failed [4] (fix: older_span)" in review
    assert "Pass-2 agreement (cross, 2 refuter(s) per record): keep_keep 2, keep_fix 0" in review
    assert "**HELD:** authority (src source: `notes.md#rule-1`)" in review
    assert "## Rejected by the gate (1)" in review and "already_linked" in review
    assert "| c0006 | v220 → v330 | pass2 |" in review
    w01 = json.loads((run / "pass1" / "w01" / "status.json").read_text())
    assert (w01["status"], w01["attempts"]) == ("ok", 2)  # the retry fixed the slice
    cfg = json.loads((run / "config.json").read_text())
    assert cfg["agent_cmd_sha256"] and "fake_agent" not in json.dumps(cfg)  # the command is not stored

    # resume: every stage is skipped, no agent runs
    again = curate("--run-dir", str(run))
    assert again.exit_code == 0, again.output
    assert [ln.split()[1] for ln in again.output.splitlines() if ln.startswith("skip ")] == STAGES
    assert not (run / "pass1" / "w01" / "prompt-3.md").exists()


def test_apply_writes_the_runbook_script(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    assert new_run(inp, run).exit_code == 0
    state = tmp_path / "state"
    state.mkdir()
    res = curate("--run-dir", str(run), "--apply")
    assert res.exit_code == 64 and "--state" in res.output
    res = curate("--run-dir", str(run), "--apply", "--state", str(state))
    assert res.exit_code == 64 and "no ssh config" in res.output
    (state / "ssh_config").write_text("")
    res = curate("--run-dir", str(run), "--apply", "--state", str(state))
    assert res.exit_code == 0, res.output
    script = run / "apply" / "apply.sh"
    text = script.read_text()
    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0
    assert "APPROVED=2\n" in text and bf.read_proposals(run / "final.jsonl")
    assert (
        "hlm links backfill --project demo --apply --proposals /tmp/curate-run.jsonl --dry-run --json" in text
    )
    assert "umask 077 && cat > /tmp/curate-run.jsonl" in text and "rm -f /tmp/curate-run.jsonl" in text
    assert f"bash {script} preview" in res.output
    # the owner leaves a link out: the script follows the file
    lines = (run / "final.jsonl").read_text().splitlines(keepends=True)
    (run / "final.jsonl").write_text(lines[0])
    assert curate("--run-dir", str(run), "--apply", "--state", str(state)).exit_code == 0
    assert "APPROVED=1\n" in script.read_text()
    # a file changed after the script was written: the script refuses before any ssh
    (run / "final.jsonl").write_text("".join(lines))
    out = subprocess.run(["bash", str(script), "preview"], capture_output=True, text=True, check=False)
    assert out.returncode == 3 and "changed after this script was generated" in out.stdout
    # a broken final.jsonl is refused by the re-gate
    bad = json.loads(lines[0]) | {"older_span": "this span is not in the older item"}
    (run / "final.jsonl").write_text(json.dumps(bad) + "\n")
    res = curate("--run-dir", str(run), "--apply", "--state", str(state))
    assert res.exit_code == 65 and "no longer passes the gate" in res.output


def test_failed_slices_are_held_and_retried(inp: dict[str, Path], tmp_path: Path) -> None:
    set_answers(inp["answers"], fail={"pass2:r1": 2})  # refuter 1 fails both attempts
    run = tmp_path / "run"
    res = new_run(inp, run)
    assert res.exit_code == 75, res.output
    s = json.loads((run / "summary.json").read_text())
    # cross: refuter 2's verdicts alone never settle a record, not even its DROP (held, retried)
    assert s["failed_slices"]["pass2"] == 1 and s["held"] == {"pass2_incomplete": 4}
    assert s["pass2_agreement"]["incomplete"] == 4
    assert s["final"] == 0 and "WARNING: failed worker slices" in (run / "REVIEW.md").read_text()
    assert "## Failed worker slices" in (run / "REVIEW.md").read_text()
    # fixed agent: only the failed slice runs again
    set_answers(inp["answers"], fail={})
    res = curate("--run-dir", str(run), env={"HLM_CURATE_AGENT_CMD": agent(inp["answers"])})
    assert res.exit_code == 0, res.output
    assert "skip  pass1" in res.output and "run   pass2" in res.output
    s = json.loads((run / "summary.json").read_text())
    assert (s["final"], s["held"]) == (2, {"authority": 1})
    r2 = json.loads((run / "pass2" / "r2" / "status.json").read_text())
    assert r2["attempts"] == 1  # reused, not re-run


def test_failed_pass1_slice_is_unverified_not_dropped(inp: dict[str, Path], tmp_path: Path) -> None:
    set_answers(inp["answers"], fail={"pass1:w02": 2})
    run = tmp_path / "run"
    res = new_run(inp, run)
    assert res.exit_code == 75
    s = json.loads((run / "summary.json").read_text())
    assert s["stages"]["pass1"]["unverified"] == 3 and s["failed_slices"]["pass1"] == 1
    assert {f["cid"] for f in jsonl(run / "pass1" / "failed.jsonl")} == {"c0004", "c0005", "c0006"}


def test_mapping_pass(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    res = curate(
        "--project", "demo", "--map", "--export", str(inp["export"]), "--run-dir", str(run),
        "--workers", "2", "--agent-cmd", agent(inp["answers"]),
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    s = json.loads((run / "summary.json").read_text())
    assert s["stages"]["map"]["pairs"] == 2 and s["stages"]["candidates"]["candidates"] == 2
    assert [(r["src_logical_id"], r["dst_logical_id"]) for r in jsonl(run / "final.jsonl")] == [
        (20, 10),
        (40, 30),
    ]
    assert all(r["origin"] == "map" for r in jsonl(run / "final.jsonl"))


def test_cross_default_is_strict(inp: dict[str, Path], tmp_path: Path) -> None:
    set_answers(
        inp["answers"],
        pass2_by_reviewer={
            "r2": {
                "20-10": {"verdict": "DROP", "failed_tests": [2], "reason": "overtaken"},
                "40-30": {
                    "verdict": "FIX",
                    "failed_tests": [4],
                    "reason": "other",
                    "fix": {"older_span": "the project is PAUSED"},  # nested, but not identical
                },
            }
        },
    )
    run = tmp_path / "run"
    res = new_run(inp, run)  # no --pass2-mode: cross is the default
    assert res.exit_code == 0, res.output
    s = json.loads((run / "summary.json").read_text())
    assert s["stages"]["pass2"]["slices"] == 2 and s["stages"]["pass2"]["records"] == 4
    assert s["stages"]["refine"]["decisions"] == {"DROP": 2, "FIX_CONFLICT": 1, "KEEP": 1}
    assert s["final"] == 0 and s["held"] == {"authority": 1, "refuter_fix_conflict": 1}
    agree = s["pass2_agreement"]
    assert (agree["drop_split"], agree["drop_unanimous"], agree["fix_conflict"], agree["keep_keep"]) == (
        1,
        1,
        1,
        1,
    )
    held = {h["src_logical_id"]: h["held_reason"] for h in jsonl(run / "held.jsonl")}
    assert held == {40: "refuter_fix_conflict", 50: "authority"}


def test_cross_keep_plus_fix_applies_the_fix(inp: dict[str, Path], tmp_path: Path) -> None:
    keep = {"verdict": "KEEP", "failed_tests": [], "reason": "fine as drafted"}
    set_answers(inp["answers"], pass2_by_reviewer={"r2": {"40-30": keep}})
    run = tmp_path / "run"
    assert new_run(inp, run).exit_code == 0
    s = json.loads((run / "summary.json").read_text())
    assert s["pass2_agreement"]["keep_fix"] == 1 and s["final"] == 2
    fixed = [r for r in jsonl(run / "final.jsonl") if r["src_logical_id"] == 40]
    assert fixed[0]["older_span"] == "the project is PAUSED by the owner" and fixed[0]["fixed"] is True


def test_split_mode_still_available(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    res = new_run(inp, run, "--pass2-mode", "split")
    assert res.exit_code == 0, res.output
    s = json.loads((run / "summary.json").read_text())
    assert s["stages"]["pass2"]["mode"] == "split" and s["stages"]["pass2"]["verdicts"] == {
        "DROP": 1,
        "FIX": 1,
        "KEEP": 2,
    }
    assert s["pass2_agreement"]["refuters_per_record"] == 1 and s["final"] == 2


def test_changed_export_is_refused(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    assert new_run(inp, run, "--stop-after", "pass1").exit_code == 0
    assert not (run / "summary.json").exists()
    f = next((run / "export" / "items").rglob("d-010-*.md"))
    f.write_text(f.read_text() + "tampered\n")
    res = curate("--run-dir", str(run))
    assert res.exit_code == 65 and "export changed" in res.output
    res = curate("--run-dir", str(run), "--redo", "export", "--agent-cmd", agent(inp["answers"]))
    assert res.exit_code == 0, res.output
    assert "tampered" not in f.read_text()


def test_redo_and_overrides(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    assert new_run(inp, run).exit_code == 0
    res = curate("--run-dir", str(run), "--authority", "deploy/*")  # a changed allowlist re-runs authority
    assert res.exit_code == 0
    assert "skip  gate2" in res.output and "done  authority" in res.output
    assert json.loads((run / "summary.json").read_text())["final"] == 1
    res = curate("--run-dir", str(run), "--redo", "pass2", "--agent-cmd", agent(inp["answers"]))
    assert res.exit_code == 0 and "skip  gate1" in res.output and "run   pass2" in res.output


def test_usage_errors(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    base = ["--project", "demo", "--export", str(inp["export"]), "--run-dir", str(run)]
    assert curate(*base).exit_code == 64  # neither --candidates nor --map
    assert curate(*base, "--map", "--candidates", str(inp["candidates"])).exit_code == 64
    assert curate(*base, "--map", "--execute", "apply").exit_code == 64
    assert curate(*base, "--map", "--stop-after", "nope").exit_code == 64
    res = curate(*base, "--candidates", str(inp["candidates"]), env={"HLM_CURATE_AGENT_CMD": ""})  # no agent
    assert res.exit_code == 64 and "HLM_CURATE_AGENT_CMD" in res.output
    res = curate("--run-dir", str(run), "--candidates", str(inp["candidates"]))
    assert res.exit_code == 64 and "already has its candidates" in res.output
    assert curate("--run-dir", str(run), "--project", "other").exit_code == 64


def test_privacy_guard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("docs/private/\n")
    assert privacy_problem(repo / "docs" / "private" / "curate" / "demo-1") is None
    assert "NOT gitignored" in (privacy_problem(repo / "docs" / "curate" / "demo-1") or "")
    assert privacy_problem(tmp_path / "outside") is None
    res = curate("--project", "demo", "--map", "--run-dir", str(repo / "runs" / "x"))
    assert res.exit_code == 64 and "NOT gitignored" in res.output


def test_privacy_guard_needs_the_whole_run_dir_ignored_and_untracked(tmp_path: Path) -> None:
    """Consult 105 #2: a ``*.json`` rule ignores summary.json but not REVIEW.md, final.jsonl or
    export/items/*.md; and an ignore rule does not protect files git already tracks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("bench/results/*.json\ndocs/private/\ntracked/\n")
    problem = privacy_problem(repo / "bench" / "results") or ""
    assert "NOT gitignored" in problem and "the directory itself" in problem
    (repo / "tracked" / "run").mkdir(parents=True)
    (repo / "tracked" / "run" / "REVIEW.md").write_text("owner data\n")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "tracked/run/REVIEW.md"], check=True)
    assert "TRACKED" in (privacy_problem(repo / "tracked" / "run") or "")
    assert "TRACKED" in (privacy_problem(repo) or "")  # the work tree itself
    assert privacy_problem(repo / "docs" / "private" / "curate" / "demo-1") is None  # accepted
    res = curate("--project", "demo", "--map", "--run-dir", str(repo / "tracked" / "run"))
    assert res.exit_code == 64 and "TRACKED" in res.output


HOSTILE = "/tmp/run\nprintf CURATE_INJECTION >&2\n#/apply"


def test_apply_script_refuses_control_characters_and_quotes_every_byte(tmp_path: Path) -> None:
    """Consult 105 #1: a newline in a path ended a comment line of apply.sh and ran as shell code."""
    final = tmp_path / "final.jsonl"
    final.write_text('{"x": 1}\n')
    sshc = tmp_path / "ssh_config"
    sshc.write_text("")
    kw: dict[str, Any] = {
        "run_name": "run",
        "project": "demo",
        "final": final,
        "out": tmp_path / "apply",
        "ssh_config": sshc,
        "ssh_host": "hlm-deploy",
        "remote_app": "/opt/hlmemo/app",
        "remote_env": "/etc/hlmemo/prod.env",
    }
    for key, bad in (
        ("out", Path(HOSTILE)),
        ("final", Path(HOSTILE)),
        ("ssh_config", Path("/a\rb")),
        ("run_name", "r\0n"),
    ):
        with pytest.raises(ValueError, match="control character"):
            bundle.apply_script(**{**kw, key: bad})
    # odd but legal bytes: every value is $'...'-quoted, no input byte is raw in the script
    odd = tmp_path / 'it\'s $(echo PWNED) `id` ünï \\ "q" ;x'
    odd.mkdir()
    text = bundle.apply_script(**{**kw, "out": odd})
    assert str(odd) not in text and "$(echo PWNED)" not in text and "ünï" not in text
    assert all("demo" not in ln and "@@" not in ln for ln in text.splitlines() if ln.startswith("#"))
    # the reviewer's reproducer form: the script on stdin, an invalid mode
    out = subprocess.run(
        ["bash", "-s", "--", "invalid"], input=text, capture_output=True, text=True, check=False
    )
    assert out.returncode == 64 and "PWNED" not in out.stdout + out.stderr
    for value in (str(odd), HOSTILE):
        quoted = bundle.bash_quote(value)
        assert "\n" not in quoted and "'" not in quoted[2:-1]
        echo = subprocess.run(["bash", "-c", f'X={quoted}; printf %s "$X"'], capture_output=True, check=False)
        assert echo.stdout == value.encode() and b"CURATE_INJECTION" not in echo.stderr
    # the CLI refuses such a path at parse time, before anything is written
    res = curate("--project", "demo", "--map", "--run-dir", str(tmp_path / "run\nprintf X >&2"))
    assert res.exit_code == 64 and "control character" in res.output
    assert not (tmp_path / "run\nprintf X >&2").exists()


def test_relative_run_dir(inp: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # workers run in their own cwd, so every path they get is absolute
    res = new_run(inp, Path("rel") / "run")
    assert res.exit_code == 0, res.output
    assert json.loads((tmp_path / "rel" / "run" / "summary.json").read_text())["final"] == 2
    prompt = (tmp_path / "rel" / "run" / "pass1" / "w01" / "prompt-1.md").read_text()
    assert str(tmp_path.resolve() / "rel" / "run" / "export" / "items") in prompt


FAKE_SSH = Path(__file__).resolve().parents[1] / "fixtures" / "curate" / "fake_ssh.py"


def _run_script(script: Path, mode: str, tmp: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    ssh = bindir / "ssh"
    ssh.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_SSH))} "$@"\n')
    ssh.chmod(0o755)
    full = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_SSH_DIR": str(tmp), **env}
    return subprocess.run(["bash", str(script), mode], capture_output=True, text=True, env=full, check=False)


def test_apply_script_against_a_fake_prod(inp: dict[str, Path], tmp_path: Path) -> None:
    run = tmp_path / "run"
    assert new_run(inp, run).exit_code == 0
    state = tmp_path / "state"
    state.mkdir()
    (state / "ssh_config").write_text("")
    assert curate("--run-dir", str(run), "--apply", "--state", str(state)).exit_code == 0
    script = run / "apply" / "apply.sh"
    prod = tmp_path / "prod"
    prod.mkdir()

    out = _run_script(script, "preview", prod)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "preview PASS 2 of 2" in out.stdout and "counts unchanged: 100|50|7" in out.stdout
    assert "remote temp file removed" in out.stdout and not (prod / "remote.jsonl").exists()
    assert "--dry-run" in (prod / "log.txt").read_text()
    assert all("--dry-run" in ln for ln in (prod / "log.txt").read_text().splitlines() if "backfill" in ln)

    broken = tmp_path / "prod2"
    broken.mkdir()
    out = _run_script(script, "apply", broken, FAKE_SSH_PREVIEW_DELTA="1")
    assert out.returncode == 6 and "ABORT: counts changed by the preview" in out.stdout
    assert sum("backfill" in ln for ln in (broken / "log.txt").read_text().splitlines()) == 1  # no apply

    out = _run_script(script, "apply", prod)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "apply rc=0" in out.stdout and "counts after: 101|52|9" in out.stdout
    assert json.loads((run / "apply" / "apply.json").read_text())["event_id"] == 4242
