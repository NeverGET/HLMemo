"""Sol 35 #2 — record mode never persists a secret the model echoes, and job errors/logs carry
content-free codes only. The provider stub echoes a seeded secret (built at run time)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.cassette import CassetteStore
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    chat,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._write_fixtures import MAIN, World, count, item, seed_world, write_req

pytestmark = pytest.mark.integration
SECRET = "sk-" + "or-v1-" + "0123456789abcdef" * 4  # assembled at run time


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _job(connect, world: World, key: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("A", "old value", valid_from=datetime(2026, 1, 1, tzinfo=UTC).isoformat()),
                    item("B", "new value"),
                ],
            ),
            deps=default_deps(),
        )
        a, b = res.versions
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=b.version_id,
            candidate_vids=[a.version_id],
            key=key,
        )
        await conn.commit()


async def test_record_mode_redacts_echoed_secret(db_dsn, connect, world: World, tmp_path) -> None:  # noqa: ANN001
    await _job(connect, world, "rec-ok")
    echo = {"contradicts": True, "supersedes": "B", "reason": f"the key is {SECRET}"}
    llm = ScriptedLLM(default=chat(echo))
    store = CassetteStore(tmp_path, record_name="rec")
    provider = make_provider(db_dsn, llm, budget_disabled=True, mode="record", cassettes=store)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    recorded = (tmp_path / "rec.jsonl").read_text()
    assert recorded and SECRET not in recorded and "⟦REDACTED:openrouter_key:" in recorded
    async with await connect() as conn:
        cur = await conn.execute("SELECT payload::text FROM events WHERE kind = 'librarian'")
        assert all(SECRET not in r[0] for r in await cur.fetchall())


async def test_schema_failure_error_is_content_free(db_dsn, connect, world: World, tmp_path, caplog) -> None:  # noqa: ANN001
    await _job(connect, world, "rec-bad")
    bad = {"contradicts": True, "supersedes": SECRET, "reason": SECRET}  # schema-invalid, echoes the secret
    llm = ScriptedLLM(default=chat(bad))
    store = CassetteStore(tmp_path, record_name="bad")
    provider = make_provider(db_dsn, llm, budget_disabled=True, mode="record", cassettes=store)
    worker = make_worker(lib_settings(db_dsn), provider, connect)
    with caplog.at_level(logging.DEBUG):
        assert await worker.drain() == 1
    await provider.aclose()
    assert SECRET not in caplog.text
    assert SECRET not in (tmp_path / "bad.jsonl").read_text()
    async with await connect() as conn:
        cur = await conn.execute("SELECT last_error FROM jobs WHERE dedupe_key = 'librarian_write:rec-bad'")
        (err,) = await cur.fetchone()
        assert err == "E_SCHEMA_FAIL"
        assert (
            await count(conn, "events", "kind = 'librarian' AND payload->'request'->>'audit' = 'llm/1'") == 0
        )
        cur = await conn.execute("SELECT row_to_json(c)::text FROM llm_calls c")
        assert all(SECRET not in r[0] for r in await cur.fetchall())
    assert json.dumps(llm.requests).count("sk-") == 0  # prompts never carried it either
