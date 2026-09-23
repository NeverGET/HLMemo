"""G-L4 — concurrent spend guard (W2a, Sol #7; D-058 runaway guard, test caps not dev defaults).

* 50 concurrent calls with the hour window set to exactly 3 × worst → exactly 3 HTTP calls reach
  the stub, 47 are ``budget_deferred``, and the ledger's spend never exceeds the cap.
* A crash between reserve and settle is swept as worst-case spend (never free, never twice).
* A synthetic loop (a job that re-enqueues itself) trips the hour window within one minute and
  pauses the librarian (heartbeat ``breaker_state=budget``).
* A job that keeps calling hits the per-job ceiling (20 provider calls) and fails permanently.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from hlmemo.librarian.budget import Caps, DbBudget, q8
from hlmemo.librarian.errors import BudgetDeferred
from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.tasks import Plan
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FakeClock,
    ScriptedLLM,
    chat,
    conn_ctx,
    lib_settings,
    make_worker,
    seed_reserved,
    stub_chain,
)
from tests.integration._write_fixtures import World, count, seed_world

pytestmark = pytest.mark.integration

USER = 'JOB: contradiction\nINPUT: {"A": {"text": "a"}, "B": {"text": "b"}}'
BIG = Decimal(1000)


def _worst(provider: Provider) -> Decimal:
    task = load_task("contradiction")
    profile = provider.chain[0]
    messages = [{"role": "system", "content": task.system}, {"role": "user", "content": USER}]
    return q8(profile.worst_usd(provider.estimate_input_tokens(messages), task.max_tokens))


@contextlib.asynccontextmanager
async def _pool(db_dsn: str):  # noqa: ANN202
    pool = AsyncConnectionPool(db_dsn, min_size=1, max_size=8, kwargs={"autocommit": True}, open=False)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _provider(llm: ScriptedLLM, ctx: Any, caps: Caps) -> Provider:
    return Provider(
        stub_chain(fallback=False),
        budget=DbBudget(ctx, caps),
        ledger=DbLedger(ctx),
        transport=llm.transport,
        clock=FakeClock(),
        redactor=Redactor(),
    )


async def test_gl4_concurrent_guard_exactly_three_calls(db_dsn, connect) -> None:  # noqa: ANN001
    async with _pool(db_dsn) as pool:
        probe = _provider(ScriptedLLM(), pool.connection, Caps(BIG, BIG, BIG))
        worst = _worst(probe)
        cap = worst * 3
        llm = ScriptedLLM(default=("stall", 0.3, chat(CONTRADICTS_B, cost=float(worst))))
        p = _provider(llm, pool.connection, Caps(cap, BIG, BIG))
        task = load_task("contradiction")
        results = await asyncio.gather(*(p.complete(task, USER) for _ in range(50)), return_exceptions=True)
        await p.aclose()
    ok = [r for r in results if not isinstance(r, BaseException)]
    deferred = [r for r in results if isinstance(r, BudgetDeferred)]
    assert (len(ok), len(deferred), llm.calls) == (3, 47, 3)
    async with await connect() as conn:
        cur = await conn.execute("SELECT outcome, count(*) FROM llm_calls GROUP BY 1 ORDER BY 1")
        assert dict(await cur.fetchall()) == {"budget_deferred": 47, "ok": 3}
        cur = await conn.execute(
            "SELECT cap_usd, spent_usd, reserved_usd FROM llm_budget WHERE period_kind = 'hour'"
        )
        cap_row, spent, reserved = await cur.fetchone()
        cur = await conn.execute("SELECT sum(cost_usd) FROM llm_calls")
        (ledger_spend,) = await cur.fetchone()
        assert cap_row == cap and reserved == 0
        assert spent == ledger_spend <= cap


async def test_gl4_crash_between_reserve_and_settle_is_swept_as_worst(db_dsn, connect) -> None:  # noqa: ANN001
    budget = DbBudget(conn_ctx(db_dsn), Caps(BIG, BIG, BIG), ttl_s=600)
    call_id = uuid.uuid4()
    worst = Decimal("0.00123456")
    assert await budget.reserve(call_id, worst, job_id=None)
    async with await connect() as conn:  # the process "dies" here: never settles
        await conn.execute("UPDATE llm_reservations SET expires_at = now() - interval '1 second'")
        await conn.commit()
    assert await budget.sweep() == 1
    await budget.settle(call_id, Decimal("0.00000001"))  # a late settle must not charge twice
    async with await connect() as conn:
        cur = await conn.execute("SELECT period_kind, spent_usd, reserved_usd FROM llm_budget ORDER BY 1")
        rows = await cur.fetchall()
        assert rows == [("day", worst, 0), ("hour", worst, 0), ("month", worst, 0)]
        assert await count(conn, "llm_reservations") == 0


class _LoopHandler:
    """A job that calls the model once and re-enqueues itself (the runaway the guard must stop)."""

    op = "loop"

    async def plan(self, w: Any, job: Any) -> Plan:
        res = await w.provider.complete(load_task("contradiction"), USER, job_id=job.job_id)
        n = int(job.payload.get("n", 0)) + 1
        payload = {
            "op": "loop",
            "n": n,
            "project_id": job.payload["project_id"],
            "capabilities": job.payload["capabilities"],
        }
        nxt = job_spec(kind="librarian_write", dedupe_key=f"librarian_write:loop:{n}", payload=payload)
        return Plan(
            self.op,
            "no_change",
            job.payload["capabilities"],
            calls=[res.audit(w.provider.redactor)],
            jobs=[nxt],
        )


class _ChattyHandler:
    op = "chatty"

    async def plan(self, w: Any, job: Any) -> Plan:
        for _ in range(25):
            await w.provider.complete(load_task("contradiction"), USER, job_id=job.job_id)
        return Plan(self.op, "no_change", job.payload["capabilities"])


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _enqueue(connect, world: World, op: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        spec = job_spec(
            kind="librarian_write", dedupe_key=f"librarian_write:{op}:0", payload={"op": op, "n": 0}
        )
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()


async def test_gl4_synthetic_loop_trips_hour_window_within_a_minute(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    await _enqueue(connect, world, "loop")
    async with _pool(db_dsn) as pool:
        worst = _worst(_provider(ScriptedLLM(), pool.connection, Caps(BIG, BIG, BIG)))
        llm = ScriptedLLM(default=chat(CONTRADICTS_B, cost=float(worst)))
        caps = Caps(worst * 5, BIG, BIG)
        p = _provider(llm, pool.connection, caps)
        worker = make_worker(
            lib_settings(db_dsn),
            p,
            connect,
            handlers={"loop": _LoopHandler()},
            budget=DbBudget(pool.connection, caps),
        )
        stop = asyncio.Event()
        runner = asyncio.create_task(worker.run_forever(stop))
        t0 = time.monotonic()
        while worker.breaker_state != "budget" and time.monotonic() - t0 < 60:  # noqa: ASYNC110
            await asyncio.sleep(0.05)  # polling another task's state
        tripped_after = time.monotonic() - t0
        hb = await worker.heartbeat(force=True)
        stop.set()
        await runner
        await p.aclose()
    assert worker.breaker_state == "budget" and tripped_after < 60
    assert hb is not None and hb["breaker_state"] == "budget" and hb["spend_hour_usd"] <= float(worst * 5)
    assert llm.calls == 5  # the sixth call's reservation was refused before any network
    async with await connect() as conn:
        # the loop's next job is handed back, not lost and not failed
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'queued'") == 1
        assert await count(conn, "jobs", "status = 'failed'") == 0


async def test_gl4_new_id_loop_hits_lineage_ceiling(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    """Sol 35 #7: each loop iteration is a NEW job id; the lineage it inherits carries the 20-call
    ceiling across them, so the loop stops at 20 calls even with generous money caps."""
    await _enqueue(connect, world, "loop")
    llm = ScriptedLLM(default=chat(CONTRADICTS_B))
    async with _pool(db_dsn) as pool:
        p = _provider(llm, pool.connection, Caps(BIG, BIG, BIG))
        worker = make_worker(lib_settings(db_dsn), p, connect, handlers={"loop": _LoopHandler()})
        for _ in range(40):
            if await worker.run_once() == 0:
                break
        await p.aclose()
    assert llm.calls == 20 and worker.breaker_state == "budget"
    async with await connect() as conn:
        cur = await conn.execute("SELECT count(DISTINCT lineage), count(*) FROM llm_calls")
        assert await cur.fetchone() == (1, 20)
        cur = await conn.execute("SELECT count(*), min(last_error) FROM jobs WHERE status = 'failed'")
        assert await cur.fetchone() == (1, "E_CALL_CAP")
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'done'") == 20


async def test_gl4_per_job_call_ceiling(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    await _enqueue(connect, world, "chatty")
    llm = ScriptedLLM(default=chat(CONTRADICTS_B))
    async with _pool(db_dsn) as pool:
        p = _provider(llm, pool.connection, Caps(BIG, BIG, BIG))
        worker = make_worker(lib_settings(db_dsn), p, connect, handlers={"chatty": _ChattyHandler()})
        assert await worker.run_once() == 1
        await p.aclose()
    assert llm.calls == 20 and worker.breaker_state == "budget"
    async with await connect() as conn:
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'failed'") == 1
        assert (
            await count(conn, "events", "kind = 'librarian' AND payload->'request'->>'audit' = 'llm/1'") == 0
        )
