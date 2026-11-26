"""The brief hook serves agy (Antigravity CLI) through the same code path as Claude Code.

agy sends camelCase input (``hookEventName``, ``conversationId``, ``cwd``, ``workspacePaths``; no
``source``) and reads ``{"injectSteps": [{"systemMessage": {"systemMessage": <text>}}]}``. The hook
process runs in the folder of hooks.json, so the agent's folder comes only from the input: ``cwd``, or
else ``workspacePaths[0]``. Every printing path (brief, last good brief, "unavailable", the watchdog's
armed line) must speak the dialect of the input."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hlmemo.brief import hook as H
from hlmemo.brief.fetch import Item, Snapshot

SRC = Path(__file__).resolve().parents[2] / "src"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def good_snapshot() -> Snapshot:
    it = Item(5, "lesson", "A lesson", None, [], body="The rule.", logical_id=5, verified=True, current=True)
    return Snapshot(
        project="proj",
        card={"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False},
        lessons=[it],
        as_of=datetime(2026, 11, 19, tzinfo=UTC),
    )


async def ok(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
    assert slug == "proj"
    return good_snapshot()


async def down(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
    raise ConnectionError("no route")


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "capture.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n\n[capture]\nexclude = [".claude/worktrees"]\n')
    return p


def agy_payload(tmp_path: Path, sub: str | None = "proj", **kw: Any) -> str:
    d: dict[str, Any] = {
        "conversationId": "ec33ebf9-0cba-4100-8142-c61503f6c587",
        "workspacePaths": [str(tmp_path / "elsewhere")],
        "transcriptPath": str(tmp_path / "t.jsonl"),
        "artifactDirectoryPath": str(tmp_path / "artifacts"),
        "modelName": "auto",
        "hookEventName": "SessionStart",
    }
    if sub is not None:
        d["cwd"] = str(tmp_path / sub)
    d.update(kw)
    return json.dumps(d)


def go(raw: str, cfg: Path, fetcher: Any = ok, env: dict[str, str] | None = None, **kw: Any) -> H.Outcome:
    return H.handle(raw, fetcher=fetcher, env=env if env is not None else {}, config_path=cfg, **kw)


def agy_text(line: str | None) -> str:
    """The injected text of an agy output line; fails on any other shape."""
    assert line is not None
    doc = json.loads(line)
    assert list(doc) == ["injectSteps"]
    (step,) = doc["injectSteps"]
    assert list(step) == ["systemMessage"] and list(step["systemMessage"]) == ["systemMessage"]
    text = step["systemMessage"]["systemMessage"]
    assert isinstance(text, str)
    return text


# --------------------------------------------------------------------------- input dialects
def test_normalize_maps_agy_input_and_leaves_claude_input_alone(tmp_path: Path) -> None:
    agent, data = H.normalize(json.loads(agy_payload(tmp_path)))
    assert agent == H.AGY
    assert data == {
        "hook_event_name": "SessionStart",
        "cwd": str(tmp_path / "proj"),
        "session_id": "ec33ebf9-0cba-4100-8142-c61503f6c587",
        "source": "startup",
    }
    claude = {"hook_event_name": "SessionStart", "cwd": "/x", "session_id": "s", "source": "resume"}
    assert H.normalize(claude) == (H.CLAUDE, claude)
    both = {**claude, "hookEventName": "SessionStart"}  # Claude's own field wins
    assert H.normalize(both) == (H.CLAUDE, both)
    for other in (None, [], "x", {}):
        assert H.normalize(other) == (H.CLAUDE, other)


# --------------------------------------------------------------------------- agy input -> agy output
def test_agy_mapped_cwd_injects_digest_and_brief_as_a_system_message(tmp_path: Path, cfg_file: Path) -> None:
    o = go(agy_payload(tmp_path), cfg_file)
    assert (o.status, o.slug, o.agent) == ("injected", "proj", H.AGY)
    text = agy_text(o.output)
    assert text.startswith("HLMemo mode: project proj ")
    assert "Real card." in text and "## Lessons" in text
    assert o.chars == len(text) <= H.CONTEXT_CHARS
    assert "hookSpecificOutput" not in (o.output or "")


def test_agy_unmapped_cwd_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
    o = go(agy_payload(tmp_path, "elsewhere"), cfg_file)
    assert (o.status, o.output, o.agent) == ("unmapped", None, H.AGY)


def test_agy_cwd_wins_over_the_workspace_path(tmp_path: Path, cfg_file: Path) -> None:
    raw = agy_payload(tmp_path, "elsewhere", workspacePaths=[str(tmp_path / "proj")])
    assert go(raw, cfg_file).status == "unmapped"


@pytest.mark.parametrize("cwd", [None, ""])
def test_agy_without_cwd_uses_the_first_workspace_path(
    tmp_path: Path, cfg_file: Path, cwd: str | None
) -> None:
    extra = {} if cwd is None else {"cwd": cwd}
    raw = agy_payload(
        tmp_path, None, workspacePaths=[str(tmp_path / "proj" / "sub"), str(tmp_path / "x")], **extra
    )
    o = go(raw, cfg_file)
    assert (o.status, o.slug) == ("injected", "proj")
    assert "Real card." in agy_text(o.output)
    second_only = agy_payload(tmp_path, None, workspacePaths=[str(tmp_path / "x"), str(tmp_path / "proj")])
    assert go(second_only, cfg_file).status == "unmapped"


@pytest.mark.parametrize("paths", [None, [], "not-a-list", [None]])
def test_agy_without_any_folder_is_silent_and_never_uses_the_process_cwd(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, paths: Any
) -> None:
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")  # a mapped folder as the process cwd must not count
    monkeypatch.setenv("PWD", str(tmp_path / "proj"))
    d = json.loads(agy_payload(tmp_path, None))
    if paths is None:
        del d["workspacePaths"]
    else:
        d["workspacePaths"] = paths
    o = go(json.dumps(d), cfg_file)
    assert o.status == "unmapped" and o.output is None


@pytest.mark.parametrize("event", ["PreToolUse", "Stop", ""])
def test_agy_other_events_are_ignored(tmp_path: Path, cfg_file: Path, event: str) -> None:
    o = go(agy_payload(tmp_path, hookEventName=event), cfg_file)
    assert o.status == "ignored" and o.output is None


def test_agy_failed_read_injects_the_unavailable_line(tmp_path: Path, cfg_file: Path) -> None:
    o = go(agy_payload(tmp_path), cfg_file, fetcher=down)
    assert o.status == "error:network"
    text = agy_text(o.output)
    assert text.startswith("HLMemo mode: project proj ")
    assert text.split("\n\n")[-1] == H.unavailable("proj", "error:network", "ConnectionError")


def test_agy_failed_read_shows_the_last_good_brief(tmp_path: Path, cfg_file: Path) -> None:
    assert go(agy_payload(tmp_path), cfg_file, now=NOW).status == "injected"  # saves the brief
    o = go(agy_payload(tmp_path), cfg_file, fetcher=down, now=NOW + timedelta(hours=3))
    text = agy_text(o.output)
    assert o.sections[-1] == "Cached" and "the live read failed this session" in text
    assert "Real card." in text


def test_agy_armed_watchdog_lines_are_agy(tmp_path: Path, cfg_file: Path) -> None:
    H.save_last_good("proj", "# Memory brief: project proj (read-only)\n\n## Now\nSaved card.", NOW)
    armed: list[str] = []
    go(agy_payload(tmp_path), cfg_file, arm=armed.append, now=NOW + timedelta(hours=1))
    assert len(armed) == 2
    assert agy_text(armed[0]).endswith(H.unavailable("proj", "timeout"))
    assert "Saved card." in agy_text(armed[1])


def test_agy_digest_off_and_empty_brief_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
    async def nothing(slug: str, cfg: Any, partial: Any = None) -> None:
        return None

    o = go(agy_payload(tmp_path), cfg_file, fetcher=nothing, env={"HLM_BRIEF_DIGEST": "off"})
    assert o.status == "empty" and o.output is None


def test_agy_dryrun_names_the_conversation(tmp_path: Path, cfg_file: Path) -> None:
    out = tmp_path / "dry"
    o = go(agy_payload(tmp_path), cfg_file, env={"HLM_BRIEF_DRYRUN": str(out)})
    assert o.status == "dryrun" and o.output is None
    (f,) = out.glob("*.brief.txt")
    assert f.name.startswith("ec33ebf9-startup-")


# --------------------------------------------------------------------------- Claude input is unchanged
def test_claude_input_still_gets_the_claude_shape(tmp_path: Path, cfg_file: Path) -> None:
    raw = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "cwd": str(tmp_path / "proj"),
            "session_id": "s1",
            "source": "startup",
        }
    )
    o = go(raw, cfg_file)
    assert o.agent == H.CLAUDE and o.output is not None
    doc = json.loads(o.output)
    assert list(doc) == ["hookSpecificOutput"]
    assert doc["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Real card." in doc["hookSpecificOutput"]["additionalContext"]
    agy = go(agy_payload(tmp_path), cfg_file)
    assert agy_text(agy.output) == doc["hookSpecificOutput"]["additionalContext"]  # same text, other shape


def test_claude_watchdog_line_keeps_the_claude_shape(tmp_path: Path, cfg_file: Path) -> None:
    raw = json.dumps({"hook_event_name": "SessionStart", "cwd": str(tmp_path / "proj"), "source": "startup"})
    armed: list[str] = []
    go(raw, cfg_file, arm=armed.append)
    (line,) = armed
    assert json.loads(line)["hookSpecificOutput"]["additionalContext"].endswith(
        H.unavailable("proj", "timeout")
    )


# --------------------------------------------------------------------------- main(): stdout, watchdog, log
def test_main_prints_agy_json_and_logs_the_agent(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HLM_CAPTURE_CONFIG", str(cfg_file))
    monkeypatch.setenv("HLM_CAPTURE_STATE_DIR", str(tmp_path / "state"))
    for k in ("HLM_BRIEF", "HLM_BRIEF_DRYRUN", "HLM_BRIEF_DIGEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(H, "default_fetcher", ok)
    monkeypatch.setattr(sys, "stdin", io.StringIO(agy_payload(tmp_path)))
    assert H.main() == 0
    assert "Real card." in agy_text(capsys.readouterr().out.strip())
    line = (tmp_path / "state" / "brief.log").read_text().strip().splitlines()[-1]
    assert " injected " in line and " slug=proj " in line and line.endswith(" agent=agy")


HUNG = (
    "import sys, time\n"
    "from hlmemo.brief import hook as H\n"
    "async def hung(slug, cfg, partial=None):\n"
    "    time.sleep(30)\n"
    "H.default_fetcher = hung\n"
    "sys.exit(H.main())\n"
)


def test_the_watchdog_prints_the_agy_line_for_a_hung_read(tmp_path: Path, cfg_file: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HLM_BRIEF")}
    env.update(
        PYTHONPATH=str(SRC),
        HLM_CAPTURE_CONFIG=str(cfg_file),
        HLM_CAPTURE_STATE_DIR=str(tmp_path / "state"),
        HLM_BRIEF_WALL_S="2.0",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    p = subprocess.run(
        [sys.executable, "-P", "-c", HUNG],
        input=agy_payload(tmp_path),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=elsewhere,  # like agy: the hook runs in the folder of hooks.json, not the agent's
        env=env,
    )
    assert p.returncode == 0
    assert agy_text(p.stdout.splitlines()[0]).endswith(H.unavailable("proj", "timeout", "", 2.0))
    line = (tmp_path / "state" / "brief.log").read_text().strip().splitlines()[-1]
    assert " watchdog " in line and "slug=proj" in line and line.endswith(" agent=agy")
