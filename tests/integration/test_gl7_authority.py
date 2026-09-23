"""G-L7 — ``authority_lost`` (CC-3): the triggering device is revoked (or loses ``write``) between
enqueue and apply → nothing is applied, a ``librarian`` event records the outcome, the job is done,
and a projection rebuild is identical."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db import auth_queries as aq
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.roles import record_role_decision
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
        await record_role_decision(conn, role="autonomous", decided_by=w.ctx_admin, decision="D-test")
        await conn.commit()
        return w


async def _setup(connect, world: World, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Port",
                        "The api listens on 8080.",
                        valid_from=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                    ),
                    item(
                        "Port",
                        "The api now listens on 8765.",
                        valid_from=datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
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
            key="g-l7",
        )
        await conn.commit()


async def _revoke(db_dsn: str, device_id: int) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await aq.select_device_for_update(conn, device_id)
        await aq.set_device_revoked(conn, device_id)
        await aq.revoke_all_grants(conn, device_id)
        await conn.commit()


async def _downgrade(db_dsn: str, device_id: int, project_id: int) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await conn.execute(
            "UPDATE device_project_grants SET role = 'read' WHERE device_id = %s AND project_id = %s",
            (device_id, project_id),
        )
        await conn.commit()


@pytest.mark.parametrize("change", ["revoke", "downgrade"])
async def test_gl7_authority_lost_between_enqueue_and_apply(
    db_dsn, connect, world: World, deps, change
) -> None:  # noqa: ANN001
    await _setup(connect, world, deps)
    fired = []

    async def mid_flight(_body: dict) -> None:  # the LLM call is in flight: authority changes now
        if not fired:
            fired.append(1)
            if change == "revoke":
                await _revoke(db_dsn, world.dev_a)
            else:
                await _downgrade(db_dsn, world.dev_a, world.main_id)

    llm = ScriptedLLM(default=CONTRADICTS_B, on_request=mid_flight)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    # autonomous would apply the auto-class links directly if authority still held
    worker = make_worker(lib_settings(db_dsn, librarian_role="autonomous"), provider, connect)
    assert await worker.drain() == 1 and llm.calls == 1
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]
        cur = await conn.execute(
            "SELECT payload->'resolved' FROM events WHERE kind = 'librarian'"
            " AND payload->'resolved'->>'outcome' = 'authority_lost'"
        )
        (resolved,) = await cur.fetchone()
        assert resolved["mutations"] == [] and resolved["proposals"] == [] and resolved["batch_id"] is None
        assert await count(conn, "links") == 0
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'done'") == 1
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
    await provider.aclose()


async def test_gl7_authority_held_applies(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    """Control: the same job with authority intact applies (autonomous auto-class)."""
    await _setup(connect, world, deps)
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="autonomous"), provider, connect)
    assert await worker.drain() == 1
    async with await connect() as conn:
        assert await outcomes(conn) == ["applied"] and await count(conn, "links") == 2
    await provider.aclose()
