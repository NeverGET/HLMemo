"""G-L8 — role enforcement (PHASE2-4-ROADMAP §4b ladder, D-058).

``observer``: 500 fixture jobs whose model output always proposes a contradiction + supersession
produce proposals (questions + audit rows) only: 0 mutations of user items or links.
``assistant``: mutations apply only for batches with a recorded owner approval (``answer``)
event, and only the approved proposals. ``autonomous`` refuses to start without a matching
librarian role decision event. An observer never applies an approved batch.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.errors import RoleNotAuthorized
from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.librarian.roles import check_role_at_start, record_batch_decision, record_role_decision
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    ScriptedLLM,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    outcomes,
    seed_reserved,
)
from tests.integration._write_fixtures import MAIN, World, count, item, seed_world, write_req

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _pair(connect, world: World, deps) -> tuple[int, int]:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("Cache TTL", "The API cache TTL is 60 seconds.", valid_from=D_OLD),
                    item(
                        "Cache TTL changed", "Since June the API cache TTL is 300 seconds.", valid_from=D_NEW
                    ),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
    old, new = res.versions
    return old.version_id, new.version_id


async def _snapshot(conn) -> tuple[int, int, int]:  # noqa: ANN001
    return (
        await count(conn, "links"),
        await count(conn, "memory_versions"),
        await count(conn, "memory_versions", "superseded_at <> 'infinity'"),
    )


async def test_gl8_observer_500_jobs_zero_mutations(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    old, new = await _pair(connect, world, deps)
    async with await connect() as conn:
        before = await _snapshot(conn)
        specs = [
            job_spec(
                kind="librarian_write",
                dedupe_key=f"librarian_write:obs:{i}",
                payload={"op": "pair_check", "version_id": new, "candidates": [old]},
            )
            for i in range(500)
        ]
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=specs)
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect)
    assert await worker.drain() == 500
    assert llm.calls == 500
    async with await connect() as conn:
        assert await _snapshot(conn) == before  # 0 mutations of user items or links
        assert await outcomes(conn) == ["proposed"] * 500
        cur = await conn.execute(
            "SELECT count(*), sum(jsonb_array_length(payload->'resolved'->'questions')),"
            " sum(jsonb_array_length(payload->'resolved'->'mutations')), min(schema_version)"
            " FROM events WHERE kind = 'librarian' AND payload->'request'->>'audit' = 'llm/1'"
        )
        n, questions, mutations, schema_version = await cur.fetchone()
        assert (n, questions, mutations, schema_version) == (500, 1000, 0, 2)
        # CC-3 question(P): the proposals are rows, all open
        cur = await conn.execute("SELECT status, kind, count(*) FROM librarian_questions GROUP BY 1, 2")
        assert await cur.fetchall() == [("open", "contradiction", 1000)]
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status <> 'done'") == 0
    await provider.aclose()


async def _proposals_job(db_dsn, connect, world, deps, role: str):  # noqa: ANN001, ANN202
    old, new = await _pair(connect, world, deps)
    async with await connect() as conn:
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new,
            candidate_vids=[old],
            key="a1",
        )
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role=role), provider, connect)
    assert await worker.drain() == 1
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT batch_id::text, question_id::text, proposal FROM librarian_questions ORDER BY question_id"
        )
        rows = await cur.fetchall()
    batch_id = rows[0][0]
    proposals = [{"question_id": qid, **proposal} for _, qid, proposal in rows]
    return worker, provider, batch_id, proposals


async def test_gl8_assistant_applies_only_approved_batches(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    worker, provider, batch_id, proposals = await _proposals_job(db_dsn, connect, world, deps, "assistant")
    assert len(proposals) == 2 and {p["actions"][0]["rel"] for p in proposals} == {
        "contradicts",
        "supersedes",
    }
    async with await connect() as conn:
        assert await count(conn, "links") == 0  # assistant: nothing before an approval
        assert await worker.drain() == 0  # no apply job exists without an approval event
        with pytest.raises(ToolError) as ei:  # a device without write on the batch's project
            await record_batch_decision(conn, batch_id=batch_id, approver=world.ctx_b, decision="accept")
        await conn.rollback()
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
        rejected = next(p["question_id"] for p in proposals if p["actions"][0]["rel"] == "supersedes")
        summary = await record_batch_decision(
            conn, batch_id=batch_id, approver=world.ctx_a, decision="accept", except_ids=[rejected]
        )
        await conn.commit()
        assert (summary["accepted"], summary["rejected"]) == (1, 1)
        assert await count(conn, "events", "kind = 'answer'") == 2
    assert await worker.drain() == 1
    async with await connect() as conn:
        cur = await conn.execute("SELECT rel, props->>'by' FROM links ORDER BY link_id")
        assert await cur.fetchall() == [("contradicts", "librarian")]
        assert (await outcomes(conn))[-1] == "applied"
        cur = await conn.execute("SELECT status, decided_by FROM librarian_questions ORDER BY status")
        assert await cur.fetchall() == [("applied", world.dev_a), ("rejected", world.dev_a)]
    await provider.aclose()


async def test_gl8_observer_never_applies_an_approved_batch(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    worker, provider, batch_id, _ = await _proposals_job(db_dsn, connect, world, deps, "observer")
    async with await connect() as conn:
        await record_batch_decision(conn, batch_id=batch_id, approver=world.ctx_a, decision="accept")
        await conn.commit()
    assert await worker.drain() == 1
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        assert (await outcomes(conn))[-1] == "role_denied"
    await provider.aclose()


async def test_gl8_assistant_without_decision_is_demoted(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    """A configured role above observer without its decision event acts as observer per job."""
    worker, provider, batch_id, _ = await _proposals_job(db_dsn, connect, world, deps, "assistant")
    async with await connect() as conn:
        await record_batch_decision(conn, batch_id=batch_id, approver=world.ctx_a, decision="accept")
        await conn.commit()
    assert await worker.drain() == 1
    async with await connect() as conn:
        assert await count(conn, "links") == 0 and (await outcomes(conn))[-1] == "role_denied"
    await provider.aclose()


async def test_gl8_autonomous_refuses_to_start_without_decision(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await check_role_at_start(conn, "observer")  # default role needs no decision
        for configured in ("autonomous", "assistant"):
            with pytest.raises(RoleNotAuthorized):
                await check_role_at_start(conn, configured)
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-x")
        await conn.commit()
        await check_role_at_start(conn, "assistant")
        with pytest.raises(RoleNotAuthorized):  # a decision for another role does not match
            await check_role_at_start(conn, "autonomous")
        with pytest.raises(ToolError):  # the deployment role is the operator's decision only
            await record_role_decision(conn, role="autonomous", decided_by=world.ctx_a, decision="D-y")
        await conn.rollback()
        await record_role_decision(conn, role="autonomous", decided_by=world.ctx_admin, decision="D-z")
        await conn.commit()
        await check_role_at_start(conn, "autonomous")
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="autonomous"), provider, connect)
    async with await connect() as conn:  # a later demotion wins
        await record_role_decision(conn, role="observer", decided_by=world.ctx_admin, decision="D-stop")
        await conn.commit()
    import asyncio

    with pytest.raises(RoleNotAuthorized):
        await asyncio.wait_for(worker.run_forever(asyncio.Event()), timeout=10)
    await provider.aclose()


async def test_gl8_autonomous_applies_auto_class(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await record_role_decision(conn, role="autonomous", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    old, new = await _pair(connect, world, deps)
    async with await connect() as conn:
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new,
            candidate_vids=[old],
            key="z",
        )
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="autonomous"), provider, connect)
    assert await worker.drain() == 1
    async with await connect() as conn:
        cur = await conn.execute("SELECT rel FROM links ORDER BY link_id")
        assert [r[0] for r in await cur.fetchall()] == ["contradicts", "supersedes"]
        assert (await outcomes(conn))[-1] == "applied"
    await provider.aclose()
