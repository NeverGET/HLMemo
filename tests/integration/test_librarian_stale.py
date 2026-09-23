"""Sol 35 #5 — stale proposals: at apply the worker locks both logical heads (the write path's
per-item lock) and compares them with the version ids the model assessed. A revision in between
supersedes the proposal: nothing is applied, the question row says ``superseded``, and the event
records it (replay identical)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.roles import record_batch_decision, record_role_decision
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    ScriptedLLM,
    dump_full_jobs_and_questions,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    outcomes,
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


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _pair(connect, world: World, deps):  # noqa: ANN001, ANN202
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Timeout",
                        "The timeout is 10 s.",
                        valid_from=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                    ),
                    item(
                        "Timeout",
                        "The timeout is 30 s now.",
                        valid_from=datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
                    ),
                ],
            ),
            deps=deps,
        )
        old, new = res.versions
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new.version_id,
            candidate_vids=[old.version_id],
            key="stale",
        )
        await conn.commit()
    return old, new


async def _revise(connect, world: World, deps, v) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Timeout",
                        "The timeout was reworded.",
                        logical_id=v.logical_id,
                        expected_version_id=v.version_id,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before


async def test_approved_batch_superseded_after_revision(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    old, _new = await _pair(connect, world, deps)
    async with await connect() as conn:
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    assert await worker.drain() == 1
    await _revise(connect, world, deps, old)  # the candidate changes after the model assessed it
    async with await connect() as conn:
        cur = await conn.execute("SELECT DISTINCT batch_id::text FROM librarian_questions")
        (batch_id,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch_id, approver=world.ctx_a, decision="accept")
        await conn.commit()
    assert await worker.drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        assert (await outcomes(conn))[-1] == "superseded"
        cur = await conn.execute("SELECT status, count(*) FROM librarian_questions GROUP BY 1")
        assert await cur.fetchall() == [("superseded", 2)]
    await _replay_identical(connect)


async def test_autonomous_revision_during_call_supersedes(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    old, _new = await _pair(connect, world, deps)
    async with await connect() as conn:
        await record_role_decision(conn, role="autonomous", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    fired: list[int] = []

    async def revise_mid_call(_body: dict) -> None:
        if not fired:
            fired.append(1)
            await _revise(connect, world, deps, old)

    llm = ScriptedLLM(default=CONTRADICTS_B, on_request=revise_mid_call)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert (
        await make_worker(lib_settings(db_dsn, librarian_role="autonomous"), provider, connect).drain() == 1
    )
    await provider.aclose()
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        cur = await conn.execute(
            "SELECT payload->'resolved'->>'outcome', payload->'resolved'->>'superseded' FROM events"
            " WHERE kind = 'librarian' AND payload->'request'->>'audit' = 'llm/1'"
        )
        assert await cur.fetchone() == ("superseded", "2")
    await _replay_identical(connect)


async def test_observer_revision_during_call_records_superseded_questions(
    db_dsn, connect, world: World, deps
) -> None:  # noqa: ANN001
    old, _new = await _pair(connect, world, deps)
    fired: list[int] = []

    async def revise_mid_call(_body: dict) -> None:
        if not fired:
            fired.append(1)
            await _revise(connect, world, deps, old)

    llm = ScriptedLLM(default=CONTRADICTS_B, on_request=revise_mid_call)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status, count(*) FROM librarian_questions GROUP BY 1")
        assert await cur.fetchall() == [("superseded", 2)]
        assert (await outcomes(conn))[-1] == "superseded"
