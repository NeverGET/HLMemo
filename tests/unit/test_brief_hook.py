"""AL5 brief hook: mapping, kill switch, fail-open on every error path, output JSON, dry run.

The hook prepends the "HLMemo mode" protocol digest for every mapped project (test_brief_digest.py). The
fail-open tests here run both ways: digest on (the default: digest + one "unavailable" line) and
``HLM_BRIEF_DIGEST=off`` (the pre-digest behaviour: nothing at all)."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hlmemo.brief import config as BC
from hlmemo.brief import hook as H
from hlmemo.brief.fetch import Item, Snapshot

PROJ = "/w/proj"


def good_snapshot() -> Snapshot:
    it = Item(5, "lesson", "A lesson", None, [], body="The rule.", logical_id=5, verified=True, current=True)
    return Snapshot(
        project="proj",
        card={"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False},
        lessons=[it],
        as_of=datetime(2026, 9, 30, tzinfo=UTC),
    )


async def ok_fetcher(slug: str, cfg: Any) -> Snapshot:
    assert slug == "proj"
    return good_snapshot()


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "capture.toml"
    p.write_text(
        f'[projects]\n"{tmp_path}/proj" = "proj"\n"{tmp_path}/other" = "other"\n\n'
        '[capture]\nexclude = [".claude/worktrees"]\n'
    )
    return p


def payload(tmp_path: Path, sub: str = "proj", **kw: Any) -> str:
    d = {
        "hook_event_name": "SessionStart",
        "cwd": str(tmp_path / sub),
        "session_id": "abc123def456",
        "source": "startup",
        **kw,
    }
    return json.dumps(d)


def go(raw: str, cfg: Path, fetcher=ok_fetcher, env: dict[str, str] | None = None) -> H.Outcome:
    return H.handle(raw, fetcher=fetcher, env=env if env is not None else {}, config_path=cfg)


DIGEST_OFF = {"HLM_BRIEF_DIGEST": "off"}


def ctx(o: H.Outcome) -> str:
    assert o.output is not None
    return json.loads(o.output)["hookSpecificOutput"]["additionalContext"]


def assert_fail_open(o: H.Outcome, digest_on: bool) -> None:
    """Digest on: the digest plus ONE "unavailable" line and no brief. Digest off: nothing."""
    if not digest_on:
        assert o.output is None
        return
    text = ctx(o)
    assert text.startswith("HLMemo mode: project proj ") and o.digest
    tail = text.split("\n\n")[-1]
    assert tail.startswith("# Memory brief: project proj: unavailable this session (") and "\n" not in tail
    assert "## Lessons" not in text and "## Now" not in text


# --------------------------------------------------------------------------- mapping + kill switch
def test_mapped_cwd_injects(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path), cfg_file)
    assert o.status == "injected" and o.output


def test_subdirectory_of_a_mapped_project_maps(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path, "proj/src/deep"), cfg_file).status == "injected"


def test_unmapped_cwd_is_silent(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path, "elsewhere"), cfg_file)
    assert o.status == "unmapped" and o.output is None


def test_prefix_lookalike_is_not_mapped(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path, "proj-evil"), cfg_file).status == "unmapped"


def test_worktrees_are_excluded_like_capture(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path, "proj/.claude/worktrees/agent-1"), cfg_file).status == "unmapped"


def test_mapping_is_exactly_the_capture_mapping(tmp_path: Path, cfg_file: Path) -> None:
    from hlmemo.capture import config as CC

    cc = CC.load_config(cfg_file)
    bc = BC.load_config(cfg_file)
    for sub in ("proj", "proj/x", "other/y", "nope", "proj/.claude/worktrees/w"):
        cwd = str(tmp_path / sub)
        assert BC.map_cwd(bc, cwd) == CC.map_cwd(cc, cwd)


@pytest.mark.parametrize("v", ["off", "0", "false", "NO", " Disabled "])
def test_env_kill_switch(tmp_path: Path, cfg_file: Path, v: str) -> None:
    o = go(payload(tmp_path), cfg_file, env={"HLM_BRIEF": v})
    assert o.status == "killed" and o.output is None


def test_env_other_values_do_not_kill(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path), cfg_file, env={"HLM_BRIEF": "on"}).status == "injected"


def test_capture_kill_switch_does_not_kill_the_brief(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path), cfg_file, env={"HLM_CAPTURE": "off"}).status == "injected"


def test_config_brief_disabled(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\nenabled = false\n')
    assert go(payload(tmp_path), p).status == "unmapped"


def test_capture_disabled_does_not_disable_the_brief(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[capture]\nenabled = false\n')
    assert go(payload(tmp_path), p).status == "injected"


def test_sources_filter(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\nsources = ["startup", "clear"]\n')
    assert go(payload(tmp_path, source="resume"), p).status == "ignored"
    assert go(payload(tmp_path, source="clear"), p).status == "injected"


# --------------------------------------------------------------------------- fail open
@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", '{"hook_event_name": "SessionEnd"}', "{}"])
def test_bad_or_foreign_input_is_silent(cfg_file: Path, raw: str) -> None:
    o = go(raw, cfg_file)
    assert o.output is None and o.status in {"ignored"} or o.status.startswith("error")
    assert o.output is None


def test_missing_config_file_is_a_noop(tmp_path: Path) -> None:
    o = go(payload(tmp_path), tmp_path / "nope.toml")
    assert o.status == "unmapped" and o.output is None


def test_invalid_toml_is_a_noop(tmp_path: Path) -> None:
    p = tmp_path / "bad.toml"
    p.write_text("[projects\n===")
    assert go(payload(tmp_path), p).output is None


@pytest.mark.parametrize("digest_on", [True, False])
@pytest.mark.parametrize(
    "exc", [RuntimeError("x"), OSError("net"), ValueError("v"), KeyError("k"), SystemExit(3)]
)
def test_fetcher_errors_fail_open(
    tmp_path: Path, cfg_file: Path, exc: BaseException, digest_on: bool
) -> None:
    async def boom(slug: str, cfg: Any) -> Snapshot:
        raise exc

    o = go(payload(tmp_path), cfg_file, fetcher=boom, env={} if digest_on else DIGEST_OFF)
    assert o.status.startswith("error:")
    assert_fail_open(o, digest_on)
    if digest_on:
        assert "(the memory read failed)" in ctx(o)


@pytest.mark.parametrize("digest_on", [True, False])
def test_timeout_fails_open(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, digest_on: bool
) -> None:
    monkeypatch.setattr(BC, "FETCH_S", 0.2)

    async def slow(slug: str, cfg: Any) -> Snapshot:
        await asyncio.sleep(5)
        return good_snapshot()

    o = go(payload(tmp_path), cfg_file, fetcher=slow, env={} if digest_on else DIGEST_OFF)
    assert o.status == "timeout"
    assert_fail_open(o, digest_on)
    if digest_on:
        assert "(the memory read timed out)" in ctx(o)


@pytest.mark.parametrize("digest_on", [True, False])
def test_no_token_means_no_brief(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, digest_on: bool
) -> None:
    from hlmemo.brief import fetch as F

    monkeypatch.setattr(F, "open_call_factory", lambda *a, **k: None)
    o = H.handle(payload(tmp_path), env={} if digest_on else DIGEST_OFF, config_path=cfg_file)
    assert o.status == "empty"
    assert_fail_open(o, digest_on)


@pytest.mark.parametrize("digest_on", [True, False])
def test_server_unreachable_fails_open(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, digest_on: bool
) -> None:
    from hlmemo.brief import fetch as F

    def refuse(*a: Any, **k: Any):
        raise ConnectionError("refused")

    monkeypatch.setattr(F, "open_call_factory", refuse)
    o = H.handle(payload(tmp_path), env={} if digest_on else DIGEST_OFF, config_path=cfg_file)
    assert o.status.startswith("error:")
    assert_fail_open(o, digest_on)


@pytest.mark.parametrize("digest_on", [True, False])
def test_empty_or_skeleton_snapshot_shows_no_brief(tmp_path: Path, cfg_file: Path, digest_on: bool) -> None:
    async def skeleton(slug: str, cfg: Any) -> Snapshot:
        return Snapshot(
            project="proj", card={"clue": "v1", "text": "Skeleton card (D-015) x", "stale": False}
        )

    async def none(slug: str, cfg: Any) -> None:
        return None

    env = {} if digest_on else DIGEST_OFF
    for fetcher in (skeleton, none):
        o = go(payload(tmp_path), cfg_file, fetcher=fetcher, env=env)
        assert o.status == "empty"
        assert_fail_open(o, digest_on)
        if digest_on:
            assert "Skeleton card" not in ctx(o) and "(nothing to show:" in ctx(o)


# --------------------------------------------------------------------------- output shape
def test_output_is_the_sessionstart_json(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path), cfg_file)
    assert o.output is not None and "\n" not in o.output
    doc = json.loads(o.output)
    assert list(doc) == ["hookSpecificOutput"]
    inner = doc["hookSpecificOutput"]
    assert set(inner) == {"hookEventName", "additionalContext"}
    assert inner["hookEventName"] == "SessionStart"
    assert inner["additionalContext"].startswith("HLMemo mode: project proj ")
    assert "\n\n# Memory brief: project proj (" in inner["additionalContext"]
    assert "## Lessons" in inner["additionalContext"]
    assert o.tokens <= 1500


def test_output_without_digest_is_the_brief_alone(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path), cfg_file, env=DIGEST_OFF)
    assert o.status == "injected" and not o.digest
    assert ctx(o).startswith("# Memory brief: project proj (") and "HLMemo mode" not in ctx(o)


def test_unicode_survives(tmp_path: Path, cfg_file: Path) -> None:
    async def tr(slug: str, cfg: Any) -> Snapshot:
        s = good_snapshot()
        s.lessons[0].title = "Türkçe şğıİ öç"
        return s

    o = go(payload(tmp_path), cfg_file, fetcher=tr)
    assert o.output and "Türkçe şğıİ öç" in json.loads(o.output)["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------- dry run
def test_dryrun_writes_a_file_and_injects_nothing(tmp_path: Path, cfg_file: Path) -> None:
    out = tmp_path / "dry"
    o = go(payload(tmp_path), cfg_file, env={"HLM_BRIEF_DRYRUN": str(out)})
    assert o.status == "dryrun" and o.output is None
    txt = list(out.glob("*.brief.txt"))
    assert len(txt) == 1  # the would-be additionalContext: the digest, then the brief
    body = txt[0].read_text()
    assert body.startswith("HLMemo mode: project proj ") and "\n\n# Memory brief: project proj (" in body
    meta = json.loads(next(out.glob("*.meta.json")).read_text())
    assert meta["tokens"] > 0 and meta["sections"][:1] == ["Digest"] and "Lessons" in meta["sections"]
    assert meta["digest"] is True and meta["status"] == "injected" and meta["chars"] == len(body) - 1


def test_dryrun_of_a_failed_fetch_writes_the_digest_and_the_unavailable_line(
    tmp_path: Path, cfg_file: Path
) -> None:
    async def boom(slug: str, cfg: Any) -> Snapshot:
        raise RuntimeError("x")

    out = tmp_path / "dry"
    o = go(payload(tmp_path), cfg_file, fetcher=boom, env={"HLM_BRIEF_DRYRUN": str(out)})
    assert o.status == "dryrun" and o.output is None
    body = next(out.glob("*.brief.txt")).read_text()
    assert body.startswith("HLMemo mode: project proj ") and "unavailable this session" in body
    assert json.loads(next(out.glob("*.meta.json")).read_text())["status"] == "error:RuntimeError"


# --------------------------------------------------------------------------- main(): stdout, exit code
def test_main_prints_json_and_exits_zero(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HLM_CAPTURE_CONFIG", str(cfg_file))
    monkeypatch.setenv("HLM_CAPTURE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("HLM_BRIEF", raising=False)
    monkeypatch.delenv("HLM_BRIEF_DRYRUN", raising=False)
    monkeypatch.setattr(H, "default_fetcher", ok_fetcher)
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload(tmp_path)))
    monkeypatch.delenv("HLM_BRIEF_DIGEST", raising=False)
    assert H.main() == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    log = (tmp_path / "state" / "brief.log").read_text()
    assert " injected " in log and " digest=1 " in log and "sections=Digest,Now,Lessons" in log


def test_main_unmapped_prints_nothing(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HLM_CAPTURE_CONFIG", str(cfg_file))
    monkeypatch.setenv("HLM_CAPTURE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload(tmp_path, "elsewhere")))
    assert H.main() == 0
    assert capsys.readouterr().out == ""


def test_main_survives_unreadable_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Bad:
        def read(self) -> str:
            raise OSError("closed")

    monkeypatch.setattr(sys, "stdin", Bad())
    assert H.main() == 0
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- the hard wall clock
def test_watchdog_ends_a_hung_process_silently_with_exit_zero() -> None:
    code = (
        "import time\n"
        "from hlmemo.brief.hook import start_watchdog\n"
        "start_watchdog(0.3)\n"
        "time.sleep(30)\n"
        "print('never')\n"
    )
    t = __import__("time").monotonic()
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0 and p.stdout == ""
    assert __import__("time").monotonic() - t < 10


def test_wall_clock_is_four_seconds_and_inside_the_hook_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HLM_BRIEF_WALL_S", raising=False)
    assert BC.wall_seconds() == 4.0 and BC.FETCH_S < BC.wall_seconds() < 5.0
    monkeypatch.setenv("HLM_BRIEF_WALL_S", "bogus")
    assert BC.wall_seconds() == 4.0


def test_config_defaults_and_age_setting(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    assert BC.load_config(p).decisions_max_age_days == 7
    assert BC.load_config(p).sources == ("startup", "clear", "compact")  # no resume: context is present
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ndecisions_max_age_days = 14\n')
    assert BC.load_config(p).decisions_max_age_days == 14
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ndecisions_max_age_days = -3\n')
    assert BC.load_config(p).decisions_max_age_days == 7
    assert go(payload(tmp_path, source="resume"), p).status == "ignored"


def test_include_auto_config_default_false_and_only_true_enables(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    assert BC.load_config(p).include_auto is False
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ninclude_auto = true\n')
    assert BC.load_config(p).include_auto is True
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ninclude_auto = "yes"\n')
    assert BC.load_config(p).include_auto is False


def test_hook_hides_auto_lessons_unless_configured(tmp_path: Path) -> None:
    async def auto(slug: str, cfg: Any) -> Snapshot:
        s = good_snapshot()
        s.lessons = [
            Item(
                9,
                "lesson",
                "Auto L",
                None,
                ["auto-capture"],
                body="b",
                logical_id=9,
                verified=True,
                current=True,
            )
        ]
        return s

    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    o = go(payload(tmp_path), p, fetcher=auto)
    assert o.output and "Auto L" not in o.output and "at least 1 auto-captured item" in o.output
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ninclude_auto = true\n')
    assert "Auto L" in (go(payload(tmp_path), p, fetcher=auto).output or "")


def test_config_history_and_body_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    c = BC.load_config(p)
    assert c.decisions_max_lines == 0 and c.lesson_body_chars == 0
    p.write_text(
        f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ndecisions_max_lines = 5\nlesson_body_chars = 100\n'
    )
    c = BC.load_config(p)
    assert c.decisions_max_lines == 5 and c.lesson_body_chars == 100
    p.write_text(
        f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\n'
        "decisions_max_lines = -1\nlesson_body_chars = 7.5\n"
    )
    c = BC.load_config(p)
    assert c.decisions_max_lines == 0 and c.lesson_body_chars == 0


def test_history_and_body_settings_reach_the_brief(tmp_path: Path) -> None:
    """`[brief] decisions_max_lines` / `lesson_body_chars` are passed to assemble (they were read from the
    config but never passed, so the documented settings had no effect)."""
    from datetime import timedelta

    async def with_note(slug: str, cfg: Any) -> Snapshot:
        s = good_snapshot()
        note = Item(
            7,
            "session_note",
            "Session",
            None,
            [],
            body="## Decisions\n- Use the new parser.\n",
            logical_id=7,
            verified=True,
            current=True,
            recorded_at=s.now - timedelta(days=1),
        )
        s.sessions = [note]
        return s

    p = tmp_path / "c.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    off = go(payload(tmp_path), p, fetcher=with_note).output or ""
    assert "Use the new parser." not in off and "The rule." not in off
    p.write_text(
        f'[projects]\n"{tmp_path}/proj" = "proj"\n[brief]\ndecisions_max_lines = 3\nlesson_body_chars = 50\n'
    )
    on = go(payload(tmp_path), p, fetcher=with_note).output or ""
    assert "Use the new parser." in on and "A lesson — The rule." in on
