"""D-280: a background share of the spend caps (threat model: docs/consults/133-d280-threat-model.md).

The librarian worker's reservations stop at ``cap × share`` of every window; a guard built without a
share (the API's interactive calls) uses the full cap on the same counters, so the two can never add up
to more than the cap, and with share 1 nothing differs from before.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import uuid
from decimal import Decimal
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from hlmemo.config import get_settings
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget, q8
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
    lib_settings,
    make_worker,
    seed_reserved,
    stub_chain,
)
from tests.integration._write_fixtures import count, seed_world

pytestmark = pytest.mark.integration

BIG = Decimal(1000)
D = Decimal


@contextlib.asynccontextmanager
async def _pool(db_dsn: str):  # noqa: ANN202
    pool = AsyncConnectionPool(db_dsn, min_size=1, max_size=8, kwargs={"autocommit": True}, open=False)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _row(connect, period: str) -> tuple[Decimal, Decimal, Decimal]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT cap_usd, spent_usd, reserved_usd FROM llm_budget WHERE period_kind = %s", (period,)
        )
        cap, spent, reserved = await cur.fetchone()
    return cap, spent, reserved


@pytest.mark.parametrize("period", ["hour", "day", "month"])
async def test_background_stops_at_its_share_and_interactive_uses_the_rest(db_dsn, connect, period) -> None:  # noqa: ANN001
    caps = Caps(**{p: (D("1.00") if p == period else BIG) for p in ("hour", "day", "month")})
    async with _pool(db_dsn) as pool:
        background = DbBudget(pool.connection, caps, share=D("0.75"))
        interactive = DbBudget(pool.connection, caps)
        a, b, c, d, e = (uuid.uuid4() for _ in range(5))
        assert await background.reserve(a, D("0.50"), None)
        assert await background.reserve(b, D("0.25"), None)  # exactly the share: allowed (<=)
        assert not await background.reserve(c, D("0.00000001"), None)  # T1/T4: one step past it is refused
        assert await interactive.reserve(d, D("0.25"), None)  # the last quarter is the interactive calls'
        assert not await interactive.reserve(e, D("0.00000001"), None)  # T1: never above the cap
        cap, spent, reserved = await _row(connect, period)
        assert (cap, spent, reserved) == (D("1.00"), D(0), D("1.00"))  # T6: the row keeps the full cap

        await background.settle(a, D("0.10"))  # T7: settling frees room for both, by the same counters
        cap, spent, reserved = await _row(connect, period)
        assert (cap, spent, reserved) == (D("1.00"), D("0.10"), D("0.50"))
        assert await background.reserve(c, D("0.15"), None)  # 0.10 + 0.50 + 0.15 = 0.75
        assert not await background.reserve(uuid.uuid4(), D("0.01"), None)
        assert await interactive.reserve(e, D("0.25"), None)  # 0.10 + 0.65 + 0.25 = 1.00
        assert not await interactive.reserve(uuid.uuid4(), D("0.00000001"), None)


async def test_interactive_spend_counts_against_the_background_share(db_dsn, connect) -> None:  # noqa: ANN001
    """Accepted by design: there is one set of counters. Interactive use lowers what background may take."""
    caps = Caps(BIG, D("1.00"), BIG)
    async with _pool(db_dsn) as pool:
        background = DbBudget(pool.connection, caps, share=D("0.75"))
        interactive = DbBudget(pool.connection, caps)
        assert await interactive.reserve(uuid.uuid4(), D("0.70"), None)
        assert not await background.reserve(uuid.uuid4(), D("0.10"), None)  # 0.80 > 0.75
        assert await background.reserve(uuid.uuid4(), D("0.05"), None)
        assert await interactive.reserve(uuid.uuid4(), D("0.25"), None)
        assert (await _row(connect, "day"))[2] == D("1.00")


async def test_share_one_is_the_old_guard_and_concurrent_background_never_passes_its_share(
    db_dsn, connect
) -> None:  # noqa: ANN001
    caps = Caps(D("1.00"), BIG, BIG)
    async with _pool(db_dsn) as pool:
        plain, one = DbBudget(pool.connection, caps), DbBudget(pool.connection, caps, share=1)
        assert plain._bound == one._bound == "cap_usd"  # noqa: SLF001 - T2: the statement of every earlier release
        background = DbBudget(pool.connection, caps, share=D("0.75"))
        got = await asyncio.gather(*(background.reserve(uuid.uuid4(), D("0.10"), None) for _ in range(30)))
        assert got.count(True) == 7  # 7 × 0.10 <= 0.75 < 8 × 0.10
        got = await asyncio.gather(*(one.reserve(uuid.uuid4(), D("0.10"), None) for _ in range(30)))
        assert got.count(True) == 3  # the full cap for a guard without a share: 1.00 in all
        assert (await _row(connect, "hour"))[2] == D("1.00")


def test_share_bounds_and_the_two_kinds_of_provider(db_dsn) -> None:  # noqa: ANN001
    for bad in (0, -0.1, 1.01, 2):
        with pytest.raises(ValueError, match="share"):
            DbBudget(lambda: None, Caps(BIG, BIG, BIG), share=bad)  # type: ignore[arg-type,return-value]
    with pytest.raises(ValueError):
        get_settings(db_dsn=db_dsn, llm_budget_background_share=0)
    with pytest.raises(ValueError):
        get_settings(db_dsn=db_dsn, llm_budget_background_share=1.2)
    assert get_settings(db_dsn=db_dsn).llm_budget_background_share == 1.0  # unset: no separate bound
    assert get_settings(db_dsn=db_dsn, llm_budget_background_share="").llm_budget_background_share == 1.0

    settings = get_settings(db_dsn=db_dsn, llm_budget_background_share=0.75, llm_mode="off")
    worker_side = Provider.from_settings(settings, conn=lambda: None, background=True)  # type: ignore[arg-type,return-value]
    api_side = Provider.from_settings(settings, conn=lambda: None)  # type: ignore[arg-type,return-value]
    assert isinstance(worker_side.budget, DbBudget) and worker_side.budget.share == D("0.75")  # T3
    assert isinstance(api_side.budget, DbBudget) and api_side.budget.share == D(1)
    off = get_settings(
        db_dsn=db_dsn, llm_budget_disabled=True, llm_budget_background_share=0.5, llm_mode="off"
    )
    assert isinstance(Provider.from_settings(off, conn=lambda: None, background=True).budget, NoBudget)  # type: ignore[arg-type,return-value]


USER = 'JOB: contradiction\nINPUT: {"A": {"text": "a"}, "B": {"text": "b"}}'


class _TwoStepHandler:
    """A job whose first call fits the bound and whose second, dearer call does not (a write review
    whose verifier's worst case is above the room left under the share)."""

    op = "twostep"

    async def plan(self, w: Any, job: Any) -> Plan:
        small = load_task("contradiction")
        big = dataclasses.replace(small, max_tokens=small.max_tokens * 40)
        await w.provider.complete(small, USER, job_id=job.job_id)
        await w.provider.complete(big, USER, job_id=job.job_id)
        return Plan(self.op, "no_change", job.payload["capabilities"])


async def test_a_job_waiting_at_the_share_is_never_failed_by_the_call_ceiling(db_dsn, connect) -> None:  # noqa: ANN001
    """Review 133 C1: every hand-back redoes the run from its first call. Before the refund each round
    kept its ceiling slot, so after 20 rounds the job was failed with E_CALL_CAP although it never
    looped. Now the slots of a deferred run go back and the job only waits."""
    async with await connect() as conn:
        world = await seed_world(conn)
        await seed_reserved(conn)
        spec = job_spec(
            kind="librarian_write", dedupe_key="librarian_write:twostep:0", payload={"op": "twostep"}
        )
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()
    async with _pool(db_dsn) as pool:
        probe = Provider(stub_chain(fallback=False), ledger=DbLedger(pool.connection), redactor=Redactor())
        small = load_task("contradiction")
        messages = [{"role": "system", "content": small.system}, {"role": "user", "content": USER}]
        tokens = probe.estimate_input_tokens(messages)
        worst_small = q8(probe.chain[0].worst_usd(tokens, small.max_tokens))
        worst_big = q8(probe.chain[0].worst_usd(tokens, small.max_tokens * 40))
        assert worst_big > worst_small * 4
        # the bound (cap x share) leaves room for the small call of every round, never for the big one
        caps = Caps((worst_big - worst_small) / D("0.75"), BIG, BIG)
        llm = ScriptedLLM(default=chat(CONTRADICTS_B, cost=0.0))
        budget = DbBudget(pool.connection, caps, share=D("0.75"))
        provider = Provider(
            stub_chain(fallback=False),
            budget=budget,
            ledger=DbLedger(pool.connection),
            transport=llm.transport,
            clock=FakeClock(),
            redactor=Redactor(),
        )
        worker = make_worker(
            lib_settings(db_dsn), provider, connect, handlers={"twostep": _TwoStepHandler()}, budget=budget
        )
        rounds = 0
        for _ in range(30):  # well past the ceiling of 20
            worker.pause_reason = None  # the 60-second budget pause is over
            async with await connect() as conn:  # the hand-back's 60 s wait is over
                await conn.execute(
                    "UPDATE jobs SET run_after = now() - interval '1 second' WHERE status = 'queued'"
                )
                await conn.commit()
            rounds += await worker.run_once()
        await provider.aclose()
    assert rounds == 30 and llm.calls == 30  # one small call went out in every round
    async with await connect() as conn:
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'queued'") == 1
        assert await count(conn, "jobs", "status = 'failed'") == 0
        cur = await conn.execute("SELECT COALESCE(max(calls), 0) FROM llm_lineage_calls")
        assert (await cur.fetchone())[0] == 0  # every round gave its slot back
        cur = await conn.execute("SELECT outcome, count(*) FROM llm_calls GROUP BY 1 ORDER BY 1")
        assert dict(await cur.fetchall()) == {"budget_deferred": 30, "ok": 30}


def test_the_default_share_keeps_the_old_statement_through_the_factory(db_dsn) -> None:  # noqa: ANN001
    """The setting's production type is float 1.0: the librarian's guard must still be the old one."""
    settings = get_settings(db_dsn=db_dsn, llm_mode="off")
    guard = Provider.from_settings(settings, conn=lambda: None, background=True).budget  # type: ignore[arg-type,return-value]
    assert isinstance(guard, DbBudget) and guard.share == 1 and guard._bound == "cap_usd"  # noqa: SLF001
    for bad in ("abc", float("nan"), None):
        with pytest.raises(ValueError, match="share"):
            DbBudget(lambda: None, Caps(BIG, BIG, BIG), share=bad)  # type: ignore[arg-type,return-value]
