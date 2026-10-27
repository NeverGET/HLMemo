"""D-267: the brief survives a purged tokenizer cache and a failed live read.

Field cause (2026-10-18 .. 10-25): macOS purged tiktoken's temp cache, the hook downloaded the vocabulary
while assembling the brief, outside the fetch deadline, and the watchdog ended it at 8 s ("brief
unavailable", no log line). Now the vocabulary is read offline only, the fetch is capped by the wall clock,
the last good brief stands in for a failed read, and the watchdog logs its own line."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hlmemo.brief import assemble as A
from hlmemo.brief import config as BC
from hlmemo.brief import hook as H
from hlmemo.brief import vocab as V
from hlmemo.brief.fetch import Item, Snapshot
from hlmemo.core import METER_VERSION

SRC = Path(__file__).resolve().parents[2] / "src"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def good_snapshot() -> Snapshot:
    it = Item(5, "lesson", "A lesson", None, [], body="The rule.", logical_id=5, verified=True, current=True)
    return Snapshot(
        project="proj",
        card={"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False},
        lessons=[it],
        as_of=datetime(2026, 10, 26, tzinfo=UTC),
    )


async def ok(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
    return good_snapshot()


async def down(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
    raise ConnectionError("no route")


async def slow(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
    await asyncio.sleep(30)
    return good_snapshot()


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    p = tmp_path / "capture.toml"
    p.write_text(f'[projects]\n"{tmp_path}/proj" = "proj"\n')
    return p


def payload(tmp_path: Path) -> str:
    return json.dumps(
        {
            "hook_event_name": "SessionStart",
            "cwd": str(tmp_path / "proj"),
            "session_id": "s1",
            "source": "startup",
        }
    )


def run(tmp_path: Path, cfg: Path, fetcher: Any, env: dict[str, str] | None = None, **kw: Any) -> H.Outcome:
    return H.handle(payload(tmp_path), fetcher=fetcher, env=env or {}, config_path=cfg, **kw)


def ctx(o: H.Outcome) -> str:
    assert o.output is not None
    return json.loads(o.output)["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------- the last good brief
def test_a_full_brief_is_saved_and_shown_when_the_next_read_fails(tmp_path: Path, cfg_file: Path) -> None:
    first = run(tmp_path, cfg_file, ok, now=NOW)
    assert first.status == "injected"
    saved = json.loads((H._cache_path("proj")).read_text())
    assert saved["slug"] == "proj" and "Real card." in saved["text"] and "HLMemo mode" not in saved["text"]
    assert oct(H._cache_path("proj").stat().st_mode & 0o777) == "0o600"

    later = NOW + timedelta(hours=30)
    o = run(tmp_path, cfg_file, down, now=later)
    text = ctx(o)
    assert o.status == "error:network" and o.sections[-1] == "Cached"
    assert "the live read failed this session (server unreachable)" in text
    assert "brief saved 2026-10-27 12:00 UTC (30 h ago): it may be stale" in text
    assert "Real card." in text and "unavailable this session" not in text and len(text) <= H.CONTEXT_CHARS


def test_a_timeout_without_a_partial_brief_shows_the_saved_one(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(tmp_path, cfg_file, ok, now=NOW)
    monkeypatch.setattr(BC, "FETCH_S", 0.2)
    o = run(tmp_path, cfg_file, slow, now=NOW + timedelta(days=3))
    assert o.status == "timeout" and "Cached" in o.sections
    assert "(timed out after 0.2 s; memory.query still works)" in ctx(o) and "(3 days ago)" in ctx(o)


@pytest.mark.parametrize(
    ("why", "env", "age"),
    [
        ("kill switch", {"HLM_BRIEF_CACHE": "off"}, timedelta(hours=1)),
        ("too old", {}, timedelta(days=H.CACHE_MAX_DAYS)),
        ("from the future", {}, timedelta(hours=-2)),
    ],
)
def test_no_saved_brief_is_shown(
    tmp_path: Path, cfg_file: Path, why: str, env: dict[str, str], age: timedelta
) -> None:
    run(tmp_path, cfg_file, ok, now=NOW)
    o = run(tmp_path, cfg_file, down, env=env, now=NOW + age)
    assert "Cached" not in o.sections, why
    assert ctx(o).endswith(H.unavailable("proj", "error:network", "ConnectionError"))


def test_an_auth_refusal_never_shows_the_saved_brief(tmp_path: Path, cfg_file: Path) -> None:
    from hlmemo.cli.mcp_client import ToolCallError

    async def refused(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
        raise ToolCallError("E_AUTH", "token refused")

    run(tmp_path, cfg_file, ok, now=NOW)
    o = run(tmp_path, cfg_file, refused, now=NOW + timedelta(hours=1))
    assert o.status == "error:auth" and "Cached" not in o.sections and "Real card." not in ctx(o)


@pytest.mark.parametrize("code", ["E_FORBIDDEN_PROJECT", "E_NOT_FOUND", "E_FORBIDDEN", "E_INVALID_ARG"])
def test_a_server_refusal_never_shows_the_saved_brief(tmp_path: Path, cfg_file: Path, code: str) -> None:
    """Review 125: a revoked read grant or a missing project is a refusal, not an outage."""
    from hlmemo.cli.mcp_client import ToolCallError

    async def refused(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
        raise ToolCallError(code, "refused")

    run(tmp_path, cfg_file, ok, now=NOW)
    o = run(tmp_path, cfg_file, refused, now=NOW + timedelta(hours=1))
    assert o.status == "error:server" and "Cached" not in o.sections and "Real card." not in ctx(o)


@pytest.mark.parametrize("code", ["E_UNAVAILABLE", "E_RATE_LIMITED"])
def test_a_transient_server_error_shows_the_saved_brief(tmp_path: Path, cfg_file: Path, code: str) -> None:
    from hlmemo.cli.mcp_client import ToolCallError

    async def busy(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
        raise ToolCallError(code, "busy")

    run(tmp_path, cfg_file, ok, now=NOW)
    o = run(tmp_path, cfg_file, busy, now=NOW + timedelta(hours=1))
    assert o.status == "error:server" and "Cached" in o.sections and "Real card." in ctx(o)


def test_a_symlinked_or_oversized_saved_brief_is_ignored(tmp_path: Path, cfg_file: Path) -> None:
    run(tmp_path, cfg_file, ok, now=NOW)
    p = H._cache_path("proj")
    real = p.read_text()
    target = tmp_path / "elsewhere.json"
    target.write_text(real)
    p.unlink()
    p.symlink_to(target)
    assert H.load_last_good("proj", NOW) is None
    p.unlink()
    data = json.loads(real)
    data["text"] = "x" * (H.CACHE_MAX_BYTES + 1)
    p.write_text(json.dumps(data))
    assert H.load_last_good("proj", NOW) is None


def test_nothing_is_saved_by_a_partial_or_dry_run_or_with_the_switch_off(
    tmp_path: Path, cfg_file: Path
) -> None:
    run(tmp_path, cfg_file, ok, env={"HLM_BRIEF_CACHE": "off"}, now=NOW)
    run(tmp_path, cfg_file, ok, env={"HLM_BRIEF_DRYRUN": str(tmp_path / "dry")}, now=NOW)
    assert not H._cache_path("proj").exists()


def test_a_saved_brief_of_another_slug_or_a_broken_file_is_ignored(tmp_path: Path, cfg_file: Path) -> None:
    p = H._cache_path("proj")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"slug": "other", "saved_at": NOW.isoformat(), "text": "# x"}))
    assert H.load_last_good("proj", NOW) is None
    p.write_text("{not json")
    assert H.load_last_good("proj", NOW) is None
    p.write_text(json.dumps({"slug": "proj", "saved_at": "2026-10-27T12:00:00", "text": "# x"}))  # naive
    assert H.load_last_good("proj", NOW) is None


# --------------------------------------------------------------------------- the wall-clock cap
def test_the_fetch_is_capped_by_the_watchdog_deadline(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []
    real = H.fetch_snapshot

    async def spy(slug: str, cfg: Any, fetcher: Any, fetch_s: float | None = None) -> Any:
        seen.append(fetch_s or -1)
        return await real(slug, cfg, fetcher, fetch_s)

    monkeypatch.setattr(H, "fetch_snapshot", spy)
    t = time.monotonic()
    o = run(tmp_path, cfg_file, slow, deadline=time.monotonic() + H.ASSEMBLY_RESERVE_S + 0.8)
    assert o.status == "timeout" and time.monotonic() - t < 1.5
    assert seen and 0.6 < seen[0] <= 0.8


def test_no_session_is_opened_when_the_start_used_the_time(tmp_path: Path, cfg_file: Path) -> None:
    called: list[str] = []

    async def never(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
        called.append(slug)
        return good_snapshot()

    o = run(tmp_path, cfg_file, never, deadline=time.monotonic() + H.ASSEMBLY_RESERVE_S + 0.1)
    assert (o.status, o.cause) == ("timeout", "start") and not called
    assert "(the hook started too slowly to read memory in time;" in ctx(o)


# --------------------------------------------------------------------------- the watchdog
def _env(tmp_path: Path, cfg: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HLM_BRIEF")}
    env.update(
        PYTHONPATH=str(SRC), HLM_CAPTURE_CONFIG=str(cfg), HLM_CAPTURE_STATE_DIR=str(tmp_path / "state")
    )
    env.update(extra)
    return env


HUNG = (
    "import sys, time\n"
    "from hlmemo.brief import hook as H\n"
    "async def hung(slug, cfg, partial=None):\n"
    "    time.sleep(30)\n"
    "H.default_fetcher = hung\n"
    "sys.exit(H.main())\n"
)


def _main(tmp_path: Path, cfg: Path, code: str) -> tuple[str, str]:
    p = subprocess.run(
        [sys.executable, "-P", "-c", code],
        input=payload(tmp_path),
        capture_output=True,
        text=True,
        timeout=20,
        env=_env(tmp_path, cfg, HLM_BRIEF_WALL_S="2.0"),
    )
    assert p.returncode == 0
    text = json.loads(p.stdout.splitlines()[0])["hookSpecificOutput"]["additionalContext"]
    log = (tmp_path / "state" / "brief.log").read_text()
    return text, log


def test_the_watchdog_logs_its_line(tmp_path: Path, cfg_file: Path) -> None:
    text, log = _main(tmp_path, cfg_file, HUNG)
    assert text.endswith(H.unavailable("proj", "timeout", "", 2.0))  # names the wall clock (review 125)
    line = log.strip().splitlines()[-1]
    assert " watchdog " in line and "slug=proj" in line and "sections=" in line and "Cached" not in line


def test_the_watchdog_prints_the_saved_brief(
    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HLM_CAPTURE_STATE_DIR", str(tmp_path / "state"))
    H.save_last_good("proj", "# Memory brief: project proj (read-only)\n\n## Now\nSaved card.")
    text, log = _main(tmp_path, cfg_file, HUNG)
    assert "the live read failed this session (timed out after" in text and "Saved card." in text
    assert "sections=Cached" in log.strip().splitlines()[-1]


# --------------------------------------------------------------------------- the vocabulary
def test_the_vocabulary_url_is_the_one_tiktoken_reads() -> None:
    import inspect

    import tiktoken_ext.openai_public as op

    assert f'"{V.VOCAB_URL}"' in inspect.getsource(op.o200k_base)


def test_no_local_vocabulary_means_no_download_and_a_background_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tiktoken.load as tl

    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "vocab"))
    monkeypatch.setattr(V, "_temp_copy", lambda: tmp_path / "nowhere" / V.VOCAB_KEY)
    monkeypatch.setattr(tl, "read_file", lambda *_: pytest.fail("the hook must not download"))
    started: list[bool] = []
    monkeypatch.setattr(V, "warm_in_background", lambda: started.append(True) or True)
    monkeypatch.setattr(A, "_enc", None)
    assert A.count_tokens("abcdef" * 10) == 21  # the pessimistic chars/3 estimate
    assert started


def test_the_temp_copy_seeds_the_persistent_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = V.cache_dir() / V.VOCAB_KEY
    if not real.is_file() and not V._temp_copy().is_file():
        pytest.skip("no local o200k_base vocabulary on this machine")
    src = real if real.is_file() else V._temp_copy()
    fake_temp = tmp_path / "tmpcache" / V.VOCAB_KEY
    fake_temp.parent.mkdir()
    fake_temp.write_bytes(src.read_bytes())
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "persistent"))
    monkeypatch.setattr(V, "_temp_copy", lambda: fake_temp)
    assert V.ensure_local() and (tmp_path / "persistent" / V.VOCAB_KEY).read_bytes() == src.read_bytes()
    enc = V.load_offline(METER_VERSION)
    assert enc.encode("hello world") == [24912, 2375]


def test_a_corrupt_local_copy_is_not_replaced_by_a_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tiktoken.load as tl
    import tiktoken.registry as reg

    d = tmp_path / "vocab"
    d.mkdir()
    (d / V.VOCAB_KEY).write_bytes(b"corrupt")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(d))
    monkeypatch.setattr(reg, "ENCODINGS", {})  # force a fresh load in this process
    fetched: list[str] = []
    monkeypatch.setattr(tl, "read_file", lambda p: fetched.append(p) or b"")
    with pytest.raises(V.VocabUnavailable):
        V.load_offline(METER_VERSION)
    assert not fetched


def test_the_warm_up_starts_at_most_once_per_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "vocab"))
    calls: list[list[str]] = []
    monkeypatch.setattr(V.subprocess, "Popen", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(V, "_warm_started", False)
    assert V.warm_in_background() is True
    assert V.warm_in_background() is False  # once per process
    monkeypatch.setattr(V, "_warm_started", False)
    assert V.warm_in_background() is False  # the marker is younger than WARM_EVERY_S
    assert len(calls) == 1 and calls[0][-2:] == ["-m", "hlmemo.brief.vocab"]


# --------------------------------------------------------------------------- the card shrink terminates
def test_a_card_longer_than_the_room_is_shrunk_not_looped_on() -> None:
    """D-267: ``counter`` reports any text over the room as over budget; shrinking the card by 0.8 x that
    number never cut a 420-token card, and assembly looped forever once the digest grew."""
    import threading

    budget, room = 1500, 2000
    words = " ".join(["documentation"] * 3000)

    def counter(text: str) -> int:
        n = A.count_tokens(text)
        return n if len(text) <= room else max(n, budget + 1)

    snap = Snapshot(
        project="proj",
        card={"clue": "v1", "text": "# Proj\n\n" + words, "stale": False},
        as_of=datetime(2026, 10, 26, tzinfo=UTC),
    )
    out: list[Any] = []
    t = threading.Thread(
        target=lambda: out.append(A.assemble(snap, budget=budget, counter=counter)), daemon=True
    )
    t.start()
    t.join(10)
    assert not t.is_alive(), "assemble did not terminate"
    assert out[0] is not None and len(out[0].text) <= room
