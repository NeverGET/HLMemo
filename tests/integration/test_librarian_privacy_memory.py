"""W2a privacy controls, working memory and heartbeat.

* ``projects.policy.librarian = off`` → no provider call, outcome ``policy_off``.
* ``device:*``-scoped subjects are skipped and ``device:*`` candidates never reach a request
  (hard-pinned, not configurable); candidates the triggering device cannot read are dropped too
  (the full exclusion matrix is in ``test_librarian_privacy_gate.py``).
* Working memory: ``librarian-rule`` facts in ``hlm-librarian`` are loaded (top-N, token-capped)
  into the prompt; the ``librarian_memory`` capability writes only that project.
* Heartbeat fields are present (ready, in_flight, oldest_ready_age_s, failed_24h, spend_*,
  reserved_usd, breaker_state, role).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.memory import load_rules, memory_ctx, write_rule
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


async def _items(connect, world, deps, specs):  # noqa: ANN001, ANN202
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(MAIN, specs), deps=deps)
        await conn.commit()
    return [v.version_id for v in res.versions]


async def _run(db_dsn, connect, world, subject, candidates, llm):  # noqa: ANN001, ANN202
    async with await connect() as conn:
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=subject,
            candidate_vids=candidates,
            key=f"p-{subject}-{len(candidates)}",
        )
        await conn.commit()
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn), provider, connect)
    assert await worker.drain() == 1
    await provider.aclose()
    return worker


async def test_policy_off_sends_nothing(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    a, b = await _items(connect, world, deps, [item("A", "alpha", valid_from=D1), item("B", "beta")])
    async with await connect() as conn:
        await conn.execute(
            'UPDATE projects SET policy = \'{"librarian": "off"}\' WHERE project_id = %s', (world.main_id,)
        )
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, b, [a], llm)
    assert llm.calls == 0
    async with await connect() as conn:
        assert await outcomes(conn) == ["policy_off"]


async def test_device_scoped_items_never_sent(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    secret_body = "device-local note: laptop-only path /Users/x/private"
    vids = await _items(
        connect,
        world,
        deps,
        [
            item("Scoped", secret_body, device_scope=f"device:{world.dev_a}"),
            item("Public", "public statement one", valid_from=D1),
            item("Public 2", "public statement two"),
            item("Other project", "belongs to other too", project_ids=[MAIN, OTHER]),
        ],
    )
    scoped, pub1, pub2, both = vids
    llm = ScriptedLLM(default=CONTRADICTS_B)
    await _run(db_dsn, connect, world, scoped, [pub1], llm)  # device-scoped subject: skipped
    assert llm.calls == 0
    await _run(db_dsn, connect, world, pub2, [scoped, pub1, both], llm)  # scoped candidate dropped
    assert llm.calls == 2  # pub1 and the two-project item (dev-a can read both projects)
    sent = json.dumps(llm.requests)
    assert "laptop-only" not in sent and "public statement one" in sent
    async with await connect() as conn:
        assert await outcomes(conn) == ["skipped_device_scope", "proposed"]
        cur = await conn.execute(
            "SELECT payload->'request'->>'dropped_candidates' FROM events"
            " WHERE payload->'resolved'->>'outcome' = 'proposed'"
        )
        assert json.loads((await cur.fetchone())[0]) == {"device_scoped": 1}


async def test_unreadable_candidate_dropped(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    """dev-b (write on OTHER only) triggers: a MAIN-only candidate is never put into a prompt."""
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(OTHER, [item("O1", "other one", valid_from=D1), item("O2", "other two")]),
            deps=deps,
        )
        main_only = await write(
            conn, world.ctx_a, write_req(MAIN, [item("M", "main secret text")]), deps=deps
        )
        await conn.commit()
        o1, o2 = (v.version_id for v in res.versions)
        await enqueue_pair(
            conn,
            project_id=world.other_id,
            trigger_device_id=world.dev_b,
            subject_vid=o2,
            candidate_vids=[main_only.versions[0].version_id, o1],
            key="unreadable",
        )
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 1 and "main secret text" not in json.dumps(llm.requests)


async def test_working_memory_rules_loaded_and_scoped(db_dsn, connect, world: World, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        v1 = await write_rule(
            conn,
            title="rule: TTL",
            text="Cache TTL facts are superseded by later dated ones.",
            clue_refs=["v1"],
            dedupe="t1",
            importance=8,
            deps=deps,
        )
        await write_rule(conn, title="rule: minor", text="Minor rule.", clue_refs=[], dedupe="t2", deps=deps)
        await conn.commit()
        rules = await load_rules(conn, max_rules=8, max_tokens=1500)
        assert [r["clue"] for r in rules][0] == f"v{v1}" and len(rules) == 2
        assert len(await load_rules(conn, max_rules=1, max_tokens=1500)) == 1
        assert await load_rules(conn, max_rules=8, max_tokens=5) == []  # token cap
        with pytest.raises(ToolError):  # body limited to rule text
            await write_rule(conn, title="x", text="y" * 700, clue_refs=[], dedupe="t3", deps=deps)
        await conn.rollback()
        cur = await conn.execute(
            "SELECT device_id, project_id FROM events WHERE kind = 'write' ORDER BY event_id"
        )
        rows = await cur.fetchall()
        cur = await conn.execute("SELECT device_id FROM devices WHERE name = 'librarian' AND is_system")
        (lib_dev,) = await cur.fetchone()
        assert {r[0] for r in rows} == {lib_dev}
        # the internal capability context cannot write anywhere else
        ctx = memory_ctx(lib_dev, rows[0][1])
        with pytest.raises(ToolError) as ei:
            await write(conn, ctx, write_req(MAIN, [item("x", "y")]), deps=deps)
        await conn.rollback()
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
    a, b = await _items(connect, world, deps, [item("A", "TTL 60", valid_from=D1), item("B", "TTL 300")])
    llm = ScriptedLLM(default=CONTRADICTS_B)
    worker = await _run(db_dsn, connect, world, b, [a], llm)
    assert "Cache TTL facts are superseded" in llm.requests[0]["messages"][1]["content"]
    assert llm.requests[0]["messages"][0]["content"].startswith(
        "You are the Librarian"
    )  # static prefix first
    hb = await worker.heartbeat(force=True)
    assert hb is not None
    for key in (
        "ready",
        "in_flight",
        "oldest_ready_age_s",
        "failed_24h",
        "spend_today_usd",
        "spend_hour_usd",
        "reserved_usd",
        "breaker_state",
        "role",
    ):
        assert key in hb, key
    assert hb["role"] == "observer" and hb["breaker_state"] == "closed" and hb["ready"] == 0
