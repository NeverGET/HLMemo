"""Sol 35 #1 — default-deny privacy gate: every exclusion path, checked against what actually
reaches the provider (the stub records every request body).

Paths: subject in a policy-off project; cross-project candidate whose OTHER project is policy-off;
cross-project candidate after the device lost its read grant on the other project; device-scoped
candidate; class scope the device cannot see; project added to the device after enqueue (not in the
enqueue-time capability set); device revoked between enqueue and the first call; device revoked
between two calls of the same job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import psycopg
import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db import auth_queries as aq
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
from tests.integration._write_fixtures import MAIN, OTHER, World, item, seed_world, write_req

pytestmark = pytest.mark.integration
D1 = datetime(2026, 3, 1, tzinfo=UTC).isoformat()


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _write(connect, world: World, deps, specs, project=MAIN) -> list[int]:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(project, specs), deps=deps)
        await conn.commit()
    return [v.version_id for v in res.versions]


async def _sql(db_dsn: str, sql: str, params: tuple = ()) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def _revoke(db_dsn: str, device_id: int) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await aq.select_device_for_update(conn, device_id)
        await aq.set_device_revoked(conn, device_id)
        await aq.revoke_all_grants(conn, device_id)
        await conn.commit()


async def _run(db_dsn, connect, world, subject, candidates, llm, key, *, before_run=None):  # noqa: ANN001, ANN202
    async with await connect() as conn:
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=subject,
            candidate_vids=candidates,
            key=key,
        )
        await conn.commit()
    if before_run is not None:
        await before_run()
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()


def _sent(llm: ScriptedLLM) -> str:
    return json.dumps(llm.requests, ensure_ascii=False)


async def test_subject_in_policy_off_project(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    (a, b) = await _write(
        connect, world, deps, [item("A", "alpha text", valid_from=D1), item("B", "beta text")]
    )
    await _sql(
        db_dsn, 'UPDATE projects SET policy = \'{"librarian":"off"}\' WHERE project_id = %s', (world.main_id,)
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, b, [a], llm, "p1")
    assert llm.calls == 0
    async with await connect() as conn:
        assert await outcomes(conn) == ["policy_off"]


async def test_cross_project_candidate_policy_off(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    (subj,) = await _write(connect, world, deps, [item("S", "subject text")])
    (cross, plain) = await _write(
        connect,
        world,
        deps,
        [
            item("X", "cross project secret", project_ids=[MAIN, OTHER], valid_from=D1),
            item("P", "plain candidate text", valid_from=D1),
        ],
    )
    await _sql(
        db_dsn,
        'UPDATE projects SET policy = \'{"librarian":"off"}\' WHERE project_id = %s',
        (world.other_id,),
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, subj, [cross, plain], llm, "p2")
    assert (
        llm.calls == 1 and "cross project secret" not in _sent(llm) and "plain candidate text" in _sent(llm)
    )


async def test_cross_project_candidate_grant_removed_after_enqueue(
    db_dsn, connect, world: World, deps
) -> None:  # noqa: ANN001
    (subj,) = await _write(connect, world, deps, [item("S", "subject text")])
    (cross,) = await _write(
        connect, world, deps, [item("X", "cross project secret", project_ids=[MAIN, OTHER])]
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)

    async def drop_grant() -> None:  # enqueue-time capabilities still list OTHER
        await _sql(
            db_dsn,
            "UPDATE device_project_grants SET revoked_at = now() WHERE device_id = %s AND project_id = %s",
            (world.dev_a, world.other_id),
        )

    await _run(db_dsn, connect, world, subj, [cross], llm, "p3", before_run=drop_grant)
    assert llm.calls == 0 and "cross project secret" not in _sent(llm)
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->'dropped_candidates' FROM events WHERE kind='librarian'"
            " AND payload->'request'->>'audit' = 'llm/1'"
        )
        assert (await cur.fetchone())[0] == {"no_read_grant": 1}


async def test_device_scoped_and_invisible_class_candidates(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    (subj,) = await _write(connect, world, deps, [item("S", "subject text")])
    (scoped, work, ok) = await _write(
        connect,
        world,
        deps,
        [
            item("D", "device local note", device_scope=f"device:{world.dev_a}"),
            item("W", "work class note", device_scope="class:work"),
            item("OK", "visible note", valid_from=D1),
        ],
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, subj, [scoped, work, ok], llm, "p4")
    sent = _sent(llm)
    assert llm.calls == 1 and "device local note" not in sent and "work class note" not in sent
    assert "visible note" in sent


async def test_project_granted_after_enqueue_not_in_capabilities(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    """dev-b enqueues with write on OTHER only; a MAIN grant added later does not widen the job."""
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                OTHER, [item("O", "other subject"), item("X", "main+other secret", project_ids=[OTHER, MAIN])]
            ),
            deps=deps,
        )
        await conn.commit()
        subj, cross = (v.version_id for v in res.versions)
        await enqueue_pair(
            conn,
            project_id=world.other_id,
            trigger_device_id=world.dev_b,
            subject_vid=subj,
            candidate_vids=[cross],
            key="p5",
        )
        await conn.commit()
    await _sql(
        db_dsn,
        "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
        " VALUES (%s, %s, 'read', 1)",
        (world.dev_b, world.main_id),
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 0 and "main+other secret" not in _sent(llm)


async def test_device_revoked_between_enqueue_and_call(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    (a, b) = await _write(
        connect, world, deps, [item("A", "alpha text", valid_from=D1), item("B", "beta text")]
    )
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, b, [a], llm, "p6", before_run=lambda: _revoke(db_dsn, world.dev_a))
    assert llm.calls == 0
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]


async def test_device_revoked_between_two_calls(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    (subj,) = await _write(connect, world, deps, [item("S", "subject text")])
    (c1, c2) = await _write(
        connect,
        world,
        deps,
        [item("C1", "first candidate", valid_from=D1), item("C2", "second candidate secret")],
    )
    fired: list[int] = []

    async def revoke_during_first(_body: dict) -> None:
        if not fired:
            fired.append(1)
            await _revoke(db_dsn, world.dev_a)

    llm = ScriptedLLM(default=CONTRADICTS_B, on_request=revoke_during_first)
    await _run(db_dsn, connect, world, subj, [c1, c2], llm, "p7")
    assert llm.calls == 1 and "second candidate secret" not in _sent(llm)
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]
        cur = await conn.execute("SELECT count(*) FROM links")
        assert (await cur.fetchone())[0] == 0
