"""Sol 37 — the privacy/authority gate runs before EVERY provider attempt, working-memory rules
are rule-shaped and redacted, the lineage ceiling is atomic, and a retried job replays identically.

* revoke during the backoff after a 503 → the retry is never sent;
* revoke before the fallback profile → the fallback is never sent;
* a rule carrying a seeded secret reaches the prompt redacted; an item body pasted as a "rule" (or
  a rule another device wrote) never reaches it;
* 50 concurrent calls on one lineage → exactly 20 reach the provider;
* a job that failed once (its run_after moved by the backoff) and then succeeded rebuilds to the
  identical FULL jobs projection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db import auth_queries as aq
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.budget import Caps, DbBudget
from hlmemo.librarian.errors import JobCallCapExceeded
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.memory import memory_ctx
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.reserved import MEMORY_PROJECT, reserved_ids
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FakeClock,
    ScriptedLLM,
    chat,
    conn_ctx,
    dump_full_jobs_and_questions,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    outcomes,
    seed_reserved,
    stub_chain,
)
from tests.integration._write_fixtures import MAIN, World, dump_projections, item, seed_world, write_req

pytestmark = pytest.mark.integration
SECRET = "sk-" + "or-v1-" + "fedcba9876543210" * 4  # assembled at run time


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _pair(connect, world: World, deps, key: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("Limit", "old limit 10", valid_from=datetime(2026, 1, 1, tzinfo=UTC).isoformat()),
                    item("Limit", "new limit 20"),
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
            key=key,
        )
        await conn.commit()


async def _revoke(db_dsn: str, device_id: int) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await aq.select_device_for_update(conn, device_id)
        await aq.set_device_revoked(conn, device_id)
        await aq.revoke_all_grants(conn, device_id)
        await conn.commit()


@pytest.mark.parametrize("first_answer", [503, 400], ids=["retry-after-backoff", "fallback-profile"])
async def test_revocation_before_the_next_attempt_stops_it(
    db_dsn, connect, world, deps, first_answer
) -> None:  # noqa: ANN001
    """503 → the primary retries after a backoff; 400 → the fallback profile is tried. Either way
    the device is revoked while the first attempt is in flight: no second request is sent."""
    await _pair(connect, world, deps, f"gate-{first_answer}")
    fired: list[int] = []

    async def revoke_on_first(_body: dict) -> None:
        if not fired:
            fired.append(1)
            await _revoke(db_dsn, world.dev_a)

    llm = ScriptedLLM([first_answer], default=CONTRADICTS_B, on_request=revoke_on_first)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 1
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]


async def test_rules_are_redacted_and_rule_shaped(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    body_text = "PASTED ITEM BODY " + ("lorem ipsum dolor sit amet " * 40)  # > MAX_RULE_CHARS
    async with await connect() as conn:
        ids = await reserved_ids(conn)
        lib = memory_ctx(ids.librarian_device_id, ids.memory_project_id)
        rule = {"kind": "fact", "tags": ["librarian-rule"], "importance": 9}
        # written by the librarian but NOT via write_rule: the secret must still be redacted at load
        await write(
            conn,
            lib,
            write_req(MEMORY_PROJECT, [{**rule, "title": "r1", "body": f"Never log {SECRET}"}]),
            deps=deps,
        )
        await write(
            conn, lib, write_req(MEMORY_PROJECT, [{**rule, "title": "r2", "body": body_text}]), deps=deps
        )
        # a "rule" another device wrote (the admin bypasses grants): never trusted as working memory
        await write(
            conn,
            world.ctx_admin,
            write_req(MEMORY_PROJECT, [{**rule, "title": "r3", "body": "admin rule"}]),
            deps=deps,
        )
        await conn.commit()
    await _pair(connect, world, deps, "rules")
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    prompt = json.dumps(llm.requests, ensure_ascii=False)
    assert "Never log" in prompt and SECRET not in prompt and "REDACTED:openrouter_key" in prompt
    assert "PASTED ITEM BODY" not in prompt and "admin rule" not in prompt


async def test_lineage_ceiling_is_atomic_under_concurrency(db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=("stall", 0.05, chat(CONTRADICTS_B)))
    ctx = conn_ctx(db_dsn)
    big = Decimal(1000)
    p = Provider(
        stub_chain(fallback=False),
        budget=DbBudget(ctx, Caps(big, big, big)),
        ledger=DbLedger(ctx),
        transport=llm.transport,
        clock=FakeClock(),
        redactor=Redactor(),
        job_call_cap=20,
    )
    task = load_task("contradiction")
    user = 'JOB: contradiction\nINPUT: {"A": {"text": "a"}, "B": {"text": "b"}}'
    lineage = "11111111-2222-4333-8444-555555555555"
    results = await asyncio.gather(
        *(p.complete(task, user, lineage=lineage) for _ in range(50)), return_exceptions=True
    )
    await p.aclose()
    assert llm.calls == 20
    assert sum(isinstance(r, JobCallCapExceeded) for r in results) == 30
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        cur = await conn.execute("SELECT calls FROM llm_lineage_calls WHERE lineage = %s", (lineage,))
        assert await cur.fetchone() == (20,)


async def test_retried_job_replays_to_the_full_jobs_projection(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    await _pair(connect, world, deps, "retry")
    bad = {"contradicts": "maybe", "supersedes": "C", "reason": 1}
    llm = ScriptedLLM([bad, bad], default=CONTRADICTS_B)  # attempt 1: schema_fail -> backoff
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn), provider, connect)
    assert await worker.drain() == 1  # failed once, re-queued with run_after = now + 1 s
    await asyncio.sleep(1.2)
    assert await worker.drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts, run_after > created_at FROM jobs"
            " WHERE dedupe_key = 'librarian_write:retry'"
        )
        assert await cur.fetchone() == ("done", 2, True)
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before
