"""The "HLMemo mode" protocol digest in the SessionStart hook, and the hook's global-install safety.

* The packaged digest is byte-identical to the ``text`` block of ``docs/protocol/HLMEMO-PROTOCOL.md`` §6.
* Mapped cwd: digest (slug filled) + brief; failed/timed-out brief: digest + one "unavailable" line, also
  when the watchdog has to end a hung process. Unmapped or excluded (worktree) cwd: nothing at all.
* Kill switches: ``HLM_BRIEF=off`` (everything), ``HLM_BRIEF_DIGEST=off`` (the digest only).
* Size: digest + brief stay under Claude Code's 10,000-character additionalContext cap.
* Global install: the unmapped path imports nothing heavy (no asyncio/socket/fetch/assemble), and the
  documented command (``-P``) never imports a module from the session's cwd.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hlmemo.brief import hook as H
from hlmemo.brief.fetch import Item, Snapshot

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "docs" / "protocol" / "HLMEMO-PROTOCOL.md"
BRIEF_README = ROOT / "docs" / "brief" / "README.md"
SRC = Path(H.__file__).resolve().parents[2]  # the src/ dir this test imports hlmemo from
RESOURCE = Path(H.__file__).with_name(H.DIGEST_RESOURCE)


def protocol_digest_block(md: str) -> str:
    """The content of the first ```text fence after the ``## 6.`` heading (newline-terminated lines)."""
    sec = md[md.index("\n## 6. ") :]
    start = sec.index("```text\n") + len("```text\n")
    return sec[start : sec.index("\n```", start) + 1]


def good_snapshot() -> Snapshot:
    it = Item(5, "lesson", "A lesson", None, [], body="The rule.", logical_id=5, verified=True, current=True)
    return Snapshot(
        project="proj",
        card={"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False},
        lessons=[it],
        as_of=datetime(2026, 9, 30, tzinfo=UTC),
    )


async def ok_fetcher(slug: str, cfg: Any) -> Snapshot:
    return good_snapshot()


async def boom(slug: str, cfg: Any) -> Snapshot:
    raise ConnectionError("refused")


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "capture.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n\n[capture]\nexclude = [".claude/worktrees"]\n')
    return p


def payload(tmp_path: Path, sub: str = "proj", **kw: Any) -> str:
    d = {
        "hook_event_name": "SessionStart",
        "cwd": str(tmp_path / sub),
        "session_id": "s1",
        "source": "startup",
    }
    return json.dumps({**d, **kw})


def go(raw: str, cfg: Path, fetcher=ok_fetcher, env: dict[str, str] | None = None) -> H.Outcome:
    return H.handle(raw, fetcher=fetcher, env=env if env is not None else {}, config_path=cfg)


def ctx(o: H.Outcome) -> str:
    assert o.output is not None
    doc = json.loads(o.output)
    assert doc["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return doc["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------- the resource
def test_resource_equals_protocol_section_6_byte_for_byte() -> None:
    block = protocol_digest_block(PROTOCOL.read_text(encoding="utf-8"))
    assert RESOURCE.read_bytes() == block.encode("utf-8")
    assert H.digest_template() == block


def test_resource_shape() -> None:
    t = H.digest_template()
    assert t.startswith("HLMemo mode: project <slug> (MCP server `hlm`). Later sessions act on this memory")
    assert t.count("<slug>") == 2 and t.endswith("\n") and "```" not in t
    assert 24 <= t.count("\n") <= 34  # "about 30 lines"
    assert set(re.findall(r"<[a-z]+>", t)) == {"<slug>"}  # no other placeholder to fill


def test_digest_fills_every_slug() -> None:
    d = H.digest("my-proj")
    assert "<slug>" not in d and not d.endswith("\n")
    assert d.startswith("HLMemo mode: project my-proj (MCP server `hlm`).")
    assert "Write into project my-proj only (" in d


# --------------------------------------------------------------------------- mapped / unmapped
def test_mapped_gets_digest_then_brief(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path), cfg_file)
    assert o.status == "injected" and o.digest and o.sections[:2] == ("Digest", "Now")
    text = ctx(o)
    assert text.startswith(H.digest("proj") + "\n\n# Memory brief: project proj (")
    assert "## Lessons\n- [v5] A lesson" in text and o.chars == len(text)


def test_subdirectory_gets_the_project_slug(tmp_path: Path, cfg_file: Path) -> None:
    assert ctx(go(payload(tmp_path, "proj/src/deep"), cfg_file)).startswith("HLMemo mode: project proj ")


@pytest.mark.parametrize("status_fetcher", ["error", "timeout"])
def test_mapped_with_failed_fetch_gets_digest_and_unavailable_line(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch, status_fetcher: str
) -> None:
    import asyncio

    from hlmemo.brief import config as BC

    async def slow(slug: str, cfg: Any) -> Snapshot:
        await asyncio.sleep(5)
        return good_snapshot()

    monkeypatch.setattr(BC, "FETCH_S", 0.2)
    o = go(payload(tmp_path), cfg_file, fetcher=boom if status_fetcher == "error" else slow)
    text = ctx(o)
    head, _, tail = text.rpartition("\n\n")
    assert head == H.digest("proj")
    assert tail == H.unavailable("proj", o.status) and "\n" not in tail
    assert tail.startswith("# Memory brief: project proj: unavailable this session (")
    assert o.status == ("error:ConnectionError" if status_fetcher == "error" else "timeout")


def test_unmapped_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path, "elsewhere"), cfg_file)
    assert o.status == "unmapped" and o.output is None and not o.digest


def test_missing_mapping_file_prints_nothing(tmp_path: Path) -> None:
    o = go(payload(tmp_path), tmp_path / "absent.toml")
    assert o.status == "unmapped" and o.output is None


@pytest.mark.parametrize("sub", ["proj/.claude/worktrees/agent-1", "proj/.claude/worktrees/agent-1/src/x"])
def test_worktree_subpath_is_excluded_digest_too(tmp_path: Path, cfg_file: Path, sub: str) -> None:
    o = go(payload(tmp_path, sub), cfg_file)
    assert o.status == "unmapped" and o.output is None


# --------------------------------------------------------------------------- kill switches
@pytest.mark.parametrize("v", ["off", "0", "false", "no", "disabled"])
def test_hlm_brief_off_kills_digest_and_brief(tmp_path: Path, cfg_file: Path, v: str) -> None:
    o = go(payload(tmp_path), cfg_file, env={"HLM_BRIEF": v})
    assert o.status == "killed" and o.output is None


@pytest.mark.parametrize("v", ["off", "0", "FALSE", " no "])
def test_hlm_brief_digest_off_keeps_the_brief_only(tmp_path: Path, cfg_file: Path, v: str) -> None:
    o = go(payload(tmp_path), cfg_file, env={"HLM_BRIEF_DIGEST": v})
    assert o.status == "injected" and not o.digest and "Digest" not in o.sections
    text = ctx(o)
    assert text.startswith("# Memory brief: project proj (") and "HLMemo mode" not in text


def test_hlm_brief_digest_off_with_failed_fetch_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
    o = go(payload(tmp_path), cfg_file, fetcher=boom, env={"HLM_BRIEF_DIGEST": "off"})
    assert o.output is None and o.status == "error:ConnectionError"


def test_other_digest_values_keep_the_digest(tmp_path: Path, cfg_file: Path) -> None:
    assert go(payload(tmp_path), cfg_file, env={"HLM_BRIEF_DIGEST": "on"}).digest


def test_unreadable_resource_still_injects_the_brief(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone() -> str:
        raise FileNotFoundError(H.DIGEST_RESOURCE)

    monkeypatch.setattr(H, "digest_template", gone)
    o = go(payload(tmp_path), cfg_file)
    assert o.status == "injected" and not o.digest
    assert ctx(o).startswith("# Memory brief: project proj (")


# --------------------------------------------------------------------------- size
def test_digest_plus_a_maximal_brief_stays_under_the_context_cap(tmp_path: Path, cfg_file: Path) -> None:
    """Long one-token words: a 1500-token brief alone would be ~2x the characters the digest leaves."""
    words = " ".join(["documentation"] * 3000)
    lessons = [
        Item(
            10 + i,
            "lesson",
            f"Lesson {i} " + words[:300],
            None,
            [],
            body="b",
            logical_id=10 + i,
            verified=True,
            current=True,
        )
        for i in range(5)
    ]

    async def big(slug: str, cfg: Any) -> Snapshot:
        return Snapshot(
            project="proj",
            card={"clue": "v1", "text": "# Proj\n\n" + words, "stale": False},
            lessons=lessons,
            pending=3,
            as_of=datetime(2026, 9, 30, tzinfo=UTC),
        )

    o = go(payload(tmp_path), cfg_file, fetcher=big)
    text = ctx(o)
    assert o.status == "injected" and text.startswith(H.digest("proj") + "\n\n# Memory brief: project proj (")
    assert len(text) <= H.CONTEXT_CHARS < 10_000
    assert o.tokens <= 1500
    assert text.rstrip().endswith("Drill down with the [vN] handles.")  # shrunk by dropping, not cut


def test_digest_size_budget() -> None:
    d = H.digest("a" * 64)  # the longest slug
    assert len(d) < 4000 and H.CONTEXT_CHARS - len(d) > 5000  # the brief keeps most of the room


# --------------------------------------------------------------------------- processes: watchdog, imports, -P
def _env(tmp_path: Path, cfg: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HLM_BRIEF")}
    env.update(
        PYTHONPATH=str(SRC),
        HLM_CAPTURE_CONFIG=str(cfg),
        HLM_CAPTURE_STATE_DIR=str(tmp_path / "state"),
        **extra,
    )
    return env


def test_watchdog_prints_digest_when_the_fetch_hangs(tmp_path: Path, cfg_file: Path) -> None:
    """A fetch that blocks the event loop (keychain, a hung import) cannot be cancelled by wait_for: the
    watchdog ends the process, but first injects the digest with the "unavailable" line."""
    code = (
        "import sys, time\n"
        "from hlmemo.brief import hook as H\n"
        "async def hung(slug, cfg):\n"
        "    time.sleep(30)\n"
        "H.default_fetcher = hung\n"
        "sys.exit(H.main())\n"
    )
    t = __import__("time").monotonic()
    p = subprocess.run(
        [sys.executable, "-P", "-c", code],
        input=payload(tmp_path),
        capture_output=True,
        text=True,
        timeout=20,
        env=_env(tmp_path, cfg_file, HLM_BRIEF_WALL_S="0.8"),
    )
    assert p.returncode == 0 and __import__("time").monotonic() - t < 10
    lines = p.stdout.splitlines()
    assert len(lines) == 1
    text = json.loads(lines[0])["hookSpecificOutput"]["additionalContext"]
    assert text == H.digest("proj") + "\n\n" + H.unavailable("proj", "timeout")


def test_watchdog_prints_nothing_before_the_cwd_is_mapped() -> None:
    code = (
        "import time\n"
        "from hlmemo.brief.hook import Stdout, start_watchdog\n"
        "start_watchdog(0.3, Stdout().last_words)\n"
        "time.sleep(30)\n"
    )
    p = subprocess.run(
        [sys.executable, "-P", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert p.returncode == 0 and p.stdout == ""


def test_stdout_writes_once() -> None:
    out = H.Stdout()
    out.arm("fallback")
    lines: list[str] = []
    real = sys.stdout

    class Cap:
        def write(self, s: str) -> None:
            lines.append(s)

        def flush(self) -> None:
            pass

    sys.stdout = Cap()  # type: ignore[assignment]
    try:
        out.write("main")
        out.write("again")
        out.last_words()  # the main thread already printed: the fallback is not written
    finally:
        sys.stdout = real
    assert lines == ["main\n"]


def test_unmapped_path_imports_nothing_heavy_and_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
    p = subprocess.run(
        [sys.executable, "-P", "-X", "importtime", "-m", "hlmemo.brief.hook"],
        input=payload(tmp_path, "elsewhere"),
        capture_output=True,
        text=True,
        timeout=20,
        env=_env(tmp_path, cfg_file),
    )
    assert p.returncode == 0 and p.stdout == ""
    loaded = {ln.rsplit("|", 1)[-1].strip() for ln in p.stderr.splitlines() if ln.startswith("import time:")}
    assert {"hlmemo.brief.config", "hlmemo.capture.config"} <= loaded  # (-m runs the hook as __main__)
    heavy = {
        "asyncio",
        "socket",
        "ssl",
        "selectors",
        "tiktoken",
        "httpx",
        "mcp",
        "pydantic",
        "keyring",
        "hlmemo.brief.fetch",
        "hlmemo.brief.assemble",
        "hlmemo.cli",
        "hlmemo.core",
        "importlib.resources",
    }
    assert not loaded & heavy
    assert " unmapped " in (tmp_path / "state" / "brief.log").read_text()


def _documented_entry() -> dict[str, Any]:
    """The SessionStart entry of the global-install snippet in docs/brief/README.md."""
    md = BRIEF_README.read_text(encoding="utf-8")
    sec = md[md.index("## Install") :]
    block = sec[sec.index("```json\n") + len("```json\n") : sec.index("\n```", sec.index("```json\n"))]
    entries = json.loads(block)["hooks"]["SessionStart"]
    assert len(entries) == 1
    return entries[0]


def test_documented_global_entry() -> None:
    e = _documented_entry()
    assert e["matcher"] == "startup|clear|compact"
    (h,) = e["hooks"]
    assert h["type"] == "command" and h["timeout"] == 10 and H.BC.WALL_S < h["timeout"]
    py, *args = h["command"].split()
    assert py == "/Users/cemalkurt/Projects/HLMemo/.venv/bin/python"  # absolute: works from any cwd
    assert args == ["-P", "-m", "hlmemo.brief.hook"]


def test_documented_command_never_imports_from_the_session_cwd(tmp_path: Path, cfg_file: Path) -> None:
    """A project with its own ``json.py`` (a stdlib name the hook imports) at its root: ``-P`` keeps the
    session cwd off sys.path, so that file never runs inside the hook. (The shadowing would happen at
    import time, before the mapping check, so an unmapped payload is enough and no fetch can start.)"""
    proj = tmp_path / "unrelated"
    proj.mkdir()
    marker = tmp_path / "SHADOWED"
    (proj / "json.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    _py, *args = _documented_entry()["hooks"][0]["command"].split()
    p = subprocess.run(
        [sys.executable, *args],
        input=payload(tmp_path, "unrelated"),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=proj,
        env=_env(tmp_path, cfg_file),
    )
    assert p.returncode == 0 and p.stdout == "" and not marker.exists()
    # control: without -P the same run imports the project's json.py
    subprocess.run(
        [sys.executable, "-m", "hlmemo.brief.hook"],
        input=payload(tmp_path, "unrelated"),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=proj,
        env=_env(tmp_path, cfg_file),
    )
    assert marker.exists()
