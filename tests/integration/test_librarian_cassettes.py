"""CC-5 cassettes: strict replay of recorded real-model output through the production path.

* A ``pair_check`` job over two real items replays the recorded default-profile answer, is applied
  as proposals, is idempotent (the same audit output on a second run), and rebuilds identically.
* The live-gate runner replays every recorded fixture call (both profiles) and passes the rubric
  with no network at all.

Recording is a manual, keyed step: ``HLM_LLM_MODE=record OPENROUTER_API_KEY=… pytest <this file>``
appends to ``tests/cassettes/w2a/``. Replay is strict: a changed prompt, model id, schema version
or parameter is a miss and fails. These tests prove parsing and application, never model quality.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.budget import NoBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from tests.integration._librarian_fixtures import (
    conn_ctx,
    enqueue_pair,
    lib_settings,
    make_worker,
    seed_reserved,
)
from tests.integration._write_fixtures import (
    MAIN,
    World,
    count,
    dump_projections,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "tests" / "cassettes" / "w2a"


def _mode() -> str:
    mode = os.environ.get("HLM_LLM_MODE", "replay")
    if mode == "record" and os.environ.get("HLM_RECORD_ENV_FILE"):  # key file, never echoed
        for line in Path(os.environ["HLM_RECORD_ENV_FILE"]).read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    return mode if mode in ("record", "replay") else "replay"


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _run(db_dsn, connect, world: World) -> list[dict]:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Deploy target",
                        "Production runs on a Hetzner CX33 in Falkenstein.",
                        valid_from=datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
                    ),
                    item(
                        "Deploy target moved",
                        "Production moved to a Hostinger KVM 2; the Hetzner server is gone.",
                        valid_from=datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
                    ),
                ],
            ),
            deps=default_deps(),
        )
        old, new = res.versions
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new.version_id,
            candidate_vids=[old.version_id],
            key="cassette-deploy",
        )
        await conn.commit()
    mode = _mode()  # loads the recording key (if any) before the profile expands env:…
    provider = Provider(
        [named_profile("openrouter")],
        mode=mode,
        budget=NoBudget(),
        ledger=DbLedger(conn_ctx(db_dsn)),
        cassettes=CassetteStore(CASSETTES, record_name="pair_check"),
        redactor=Redactor(),
        budget_disabled=True,
    )
    worker = make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect)
    assert await worker.drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload FROM events WHERE kind = 'librarian' AND payload->'request'->>'audit' = 'llm/1'"
        )
        return [r[0] for r in await cur.fetchall()]


async def test_pair_check_replays_recorded_model_output(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    (payload,) = await _run(db_dsn, connect, world)
    call = payload["request"]["calls"][0]
    assert call["model_id"] == "deepseek/deepseek-v4.1-flash" and call["prompt_version"] == "v1"
    assert call["output"]["contradicts"] is True and call["output"]["supersedes"] == "B"
    resolved = payload["resolved"]
    assert resolved["outcome"] == "proposed" and resolved["mutations"] == []
    assert {p["mutation"]["rel"] for p in resolved["proposals"]} == {"contradicts", "supersedes"}
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        cur = await conn.execute("SELECT mode, outcome FROM llm_calls")
        assert await cur.fetchall() == [(_mode(), "ok")]
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_pair_check_replay_is_deterministic(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    if _mode() == "record":
        pytest.skip("recording run")
    (first,) = await _run(db_dsn, connect, world)
    async with await connect() as conn:  # a fresh world: same inputs, same key, same output
        from tests.conftest import TRUNCATE_SQL

        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
        w2 = await seed_world(conn)
        await seed_reserved(conn)
    (second,) = await _run(db_dsn, connect, w2)
    assert first["request"]["calls"] == second["request"]["calls"]


def test_live_runner_replays_all_recorded_fixtures() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_run", ROOT / "eval" / "live" / "run.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main(
        [
            "--mode",
            "replay",
            "--reps",
            "1",
            "--max-usd",
            "0.01",
            "--out",
            "",
            "--cassette-dir",
            str(CASSETTES),
        ]
    )
    assert rc == 0
