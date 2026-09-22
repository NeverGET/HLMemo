"""D-037: private targets, serialization waits and deterministic temporal projections."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps, drilldown
from hlmemo.core.write_service import call_the_day, default_deps, write
from hlmemo.db import read_queries as rq
from hlmemo.db import write_queries as q
from hlmemo.db.replay import rebuild_projections
from tests.integration._write_fixtures import (
    MAIN,
    OTHER,
    dump_projections,
    event_payload,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration
D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect):
    async with await connect() as conn:
        return await seed_world(conn)


def close_req(project=MAIN, **kwargs):
    return {
        "project": project,
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "session",
        **kwargs,
    }


@pytest.mark.parametrize("lock_kind", ["logical", "request", "session"])
async def test_hidden_and_missing_pins_fail_before_contended_keys(connect, world, deps, lock_kind):
    async with await connect() as conn, await connect() as blocker:
        first = await write(
            conn,
            world.ctx_a,
            write_req(OTHER, [item("private", "body", device_scope=f"device:{world.dev_a}")]),
            deps=deps,
        )
        await conn.commit()
        target = first.versions[0]
        req = close_req(OTHER)
        if lock_kind == "logical":
            await q.lock_logical_ids(blocker, [target.logical_id])
        elif lock_kind == "request":
            await q.lock_request_key(blocker, world.other_id, world.dev_b, req["request_id"])
        else:
            await q.lock_session_key(blocker, world.other_id, req["session_id"])
        for lid in (target.logical_id, target.logical_id + 1_000_000):
            with pytest.raises(ToolError) as exc:
                async with asyncio.timeout(0.5):
                    await call_the_day(
                        conn,
                        world.ctx_b,
                        {**req, "expected_versions": [{"logical_id": lid, "version_id": target.version_id}]},
                        deps=deps,
                    )
            assert exc.value.code == "E_NOT_FOUND"
            assert "current_version_id" not in exc.value.details
        await blocker.rollback()


async def test_hidden_revision_fails_before_contended_logical_lock(connect, world, deps):
    async with await connect() as conn, await connect() as blocker:
        initial = await write(
            conn,
            world.ctx_a,
            write_req(OTHER, [item("private", "body", device_scope=f"device:{world.dev_a}")]),
            deps=deps,
        )
        await conn.commit()
        target = initial.versions[0]
        await q.lock_logical_ids(blocker, [target.logical_id])
        for lid in (target.logical_id, target.logical_id + 1_000_000):
            with pytest.raises(ToolError) as exc:
                async with asyncio.timeout(0.5):
                    await write(
                        conn,
                        world.ctx_b,
                        write_req(
                            OTHER,
                            [
                                item(
                                    "probe",
                                    "body",
                                    logical_id=lid,
                                    expected_version_id=target.version_id,
                                )
                            ],
                        ),
                        deps=deps,
                    )
            assert exc.value.code == "E_NOT_FOUND"
        await blocker.rollback()


@pytest.mark.parametrize("scope", ["class:personal", "class:work", "device"])
async def test_project_card_scope_rejected_before_persistence(connect, world, deps, scope):
    if scope == "device":
        scope = f"device:{world.dev_a}"
    async with await connect() as conn:
        for expected in (None, 123):
            with pytest.raises(ToolError) as exc:
                await write(
                    conn,
                    world.ctx_a,
                    write_req(
                        MAIN,
                        [
                            item(
                                "card",
                                "body",
                                kind="project_card",
                                device_scope=scope,
                                expected_version_id=expected,
                            )
                        ],
                    ),
                    deps=deps,
                )
            assert exc.value.code == "E_INVALID_ARG"
        assert (await (await conn.execute("SELECT count(*) FROM events")).fetchone())[0] == 0


@pytest.mark.parametrize("operation", ["revision", "replay", "session", "scope_change"])
async def test_advisory_contention_converges_and_restores_timeouts(
    connect,
    world,
    deps,
    monkeypatch,
    operation,
):
    async with await connect() as holder, await connect() as contender:
        initial = await write(holder, world.ctx_a, write_req(OTHER, [item("base", "body")]), deps=deps)
        await holder.commit()
        target = initial.versions[0]
        await holder.execute("SELECT 1")  # outer transaction keeps the write locks until commit
        if operation == "session":
            first_req = close_req(OTHER)
            await call_the_day(holder, world.ctx_a, first_req, deps=deps)
            second_req = {**first_req, "request_id": str(uuid.uuid4())}
            run = call_the_day
        else:
            first_req = write_req(
                OTHER,
                [
                    item(
                        "revision",
                        "new body",
                        logical_id=target.logical_id,
                        expected_version_id=target.version_id,
                        device_scope=f"device:{world.dev_a}" if operation == "scope_change" else "all",
                        valid_from=D0.isoformat(),
                    )
                ],
            )
            await write(holder, world.ctx_a, first_req, deps=deps)
            second_req = (
                first_req
                if operation == "replay"
                else {
                    **first_req,
                    "request_id": str(uuid.uuid4()),
                    "items": [{**first_req["items"][0], "device_scope": "all"}],
                }
            )
            run = write
        await contender.execute("SET LOCAL lock_timeout = '25ms'")
        await contender.execute("SET LOCAL statement_timeout = '50ms'")
        await contender.execute("SELECT set_config('hlmemo.request_db_timeout_ms', '2000', true)")
        entered = asyncio.Event()
        original = q._advisory_lock

        async def observe(conn, query, params):
            if conn is contender:
                entered.set()
            return await original(conn, query, params)

        monkeypatch.setattr(q, "_advisory_lock", observe)
        ctx = world.ctx_b if operation == "scope_change" else world.ctx_a
        task = asyncio.create_task(run(contender, ctx, second_req, deps=deps))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0.15)  # exceeds both ordinary statement and lock limits
            assert not task.done(), "serialization must wait within the request deadline"
            await holder.commit()
            if operation == "replay":
                result = await asyncio.wait_for(task, 2)
                assert result.replayed
            else:
                with pytest.raises(ToolError) as exc:
                    await asyncio.wait_for(task, 2)
                assert (
                    exc.value.code
                    == {
                        "revision": "E_VERSION_CONFLICT",
                        "session": "E_SESSION_CLOSED",
                        "scope_change": "E_NOT_FOUND",
                    }[operation]
                )
            settings = await (
                await contender.execute(
                    "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
                )
            ).fetchone()
            assert settings == ("25ms", "50ms")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await holder.rollback()


async def test_survivor_access_snapshot_replays_after_concurrent_read(connect, world, deps, monkeypatch):
    async with await connect() as conn:
        initial = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "base",
                        "body",
                        valid_from=D0.isoformat(),
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        target = initial.versions[0]
        original = q.clock_now
        fired = False

        async def concurrent_read(c):
            nonlocal fired
            if not fired:
                fired = True
                async with await connect() as reader:
                    await drilldown(
                        reader,
                        world.ctx_a,
                        {
                            "project": MAIN,
                            "clue_ids": [f"v{target.version_id}"],
                            "token_budget": 2000,
                        },
                        deps=default_read_deps(),
                    )
                    await reader.commit()
            return await original(c)

        monkeypatch.setattr(q, "clock_now", concurrent_read)
        req = write_req(
            MAIN,
            [
                item(
                    "correction",
                    "body",
                    logical_id=target.logical_id,
                    expected_version_id=target.version_id,
                    valid_from=(D0 + 5 * DAY).isoformat(),
                    valid_to=(D0 + 6 * DAY).isoformat(),
                )
            ],
        )
        await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        assert fired
        payload = await event_payload(conn, req["request_id"])
        survivors = payload["resolved"]["items"][0]["survivors"]
        assert len(survivors) == 2
        assert all("last_access_at" in s and s["last_access_at"] is None for s in survivors)
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_card_revision_replaces_all_sources_and_replays(connect, world, deps):
    async with await connect() as conn:
        initial = await write(conn, world.ctx_a, write_req(MAIN, [item("source", "body")]), deps=deps)
        target = initial.versions[0]
        first = await call_the_day(
            conn,
            world.ctx_a,
            close_req(
                card_update={"body": "card one"},
                expected_versions=[{"logical_id": target.logical_id, "version_id": target.version_id}],
            ),
            deps=deps,
        )
        await conn.commit()
        await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "source",
                        "updated",
                        logical_id=target.logical_id,
                        expected_version_id=target.version_id,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        head = first.versions[-1].version_id
        for i in range(30):
            req = close_req(card_update={"body": f"card {i}", "expected_version_id": head})
            result = await call_the_day(conn, world.ctx_a, req, deps=deps)
            head = result.versions[-1].version_id
            await conn.commit()
        now = await q.clock_now(conn)
        sources = await rq.pinned_sources(
            conn,
            world.main_card_lid,
            world.main_id,
            list(world.ctx_a.scope_values()),
            now,
            now,
        )
        assert sources == [(result.versions[0].version_id, False)]
        raw_count = await (
            await conn.execute(
                "SELECT count(*) FROM links WHERE src_logical_id = %s AND superseded_at = 'infinity'"
                " AND valid_from <= %s AND %s < valid_to",
                (world.main_card_lid, now, now),
            )
        ).fetchone()
        assert raw_count == (1,)
        # memory.write also replaces the source set, even with no new links at all.
        await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "card",
                        "standalone card",
                        kind="project_card",
                        expected_version_id=head,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        now = await q.clock_now(conn)
        assert (
            await rq.pinned_sources(
                conn,
                world.main_card_lid,
                world.main_id,
                list(world.ctx_a.scope_values()),
                now,
                now,
            )
            == []
        )
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
