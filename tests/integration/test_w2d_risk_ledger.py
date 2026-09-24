"""R2 rehearsal bug: the risk judge's 4 s cap also covers the two privacy gates, so slow gates made
the cap cancel the HTTP call mid-flight: no ``llm_calls`` row, and the reservation stayed open until
the sweeper charged it at worst case (21 risk checks → 20 ledger rows + 1 orphan).

Fix (provider ``deadline=``): every HTTP timeout is budgeted to end before the caller's deadline,
no attempt/reservation/backoff starts without room, and any cancellation still settles the
reservation and writes the ledger row (shielded). Both paths are tested with 0.8 s privacy gates:

* a provider that honours the request timeout (real httpx behaviour): the request's read timeout
  is the REMAINING budget, the call ends before the cap, one ``timeout`` row, no orphan;
* a stub that ignores timeouts (the cap's backstop fires mid-HTTP): still one ``timeout`` row and
  no orphan reservation.

In both, ``risk_check`` answers retrieval-only with ``reason: timeout``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import risk_service as rs
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian import privacy
from hlmemo.librarian import risk_judge as rj
from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
from tests.integration._risk_fixtures import MAIN, load_cases, seed_world

pytestmark = pytest.mark.integration

GATE_DELAY_S = 0.8


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_world(connect, deps.embedder)


@pytest.fixture
def slow_gates(monkeypatch):  # noqa: ANN001
    original = privacy.gate

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(GATE_DELAY_S)
        return await original(*args, **kwargs)

    monkeypatch.setattr(privacy, "gate", slow)


async def _counts(connect) -> tuple[int, int]:  # noqa: ANN001
    async with await connect() as c:
        cur = await c.execute(
            "SELECT (SELECT count(*) FROM llm_calls WHERE task = 'risk_judge' AND outcome = 'timeout'),"
            " (SELECT count(*) FROM llm_reservations)"
        )
        rows, orphans = await cur.fetchone()
        await c.commit()
    return int(rows), int(orphans)


async def _run(connect, world, deps, judge: rj.RiskJudge) -> tuple[dict[str, Any], float]:  # noqa: ANN001
    task = load_cases()[0]["task"]
    async with await connect() as conn:
        await conn.commit()
        t0 = time.perf_counter()
        out = await rs.risk_check(
            conn,
            world.ctx_reader,
            {"project": MAIN, "task": task, "token_budget": 2000},
            deps=deps,
            judge=judge,
        )
        elapsed = time.perf_counter() - t0
        await conn.commit()
    return out, elapsed


def _judge(db_dsn: str, transport: httpx.AsyncBaseTransport) -> rj.RiskJudge:
    settings = get_settings(db_dsn=db_dsn, librarian_enabled=True, llm_mode="live", llm_budget_disabled=False)
    return rj.RiskJudge(settings, chain=stub_chain(fallback=False), transport=transport)


async def test_http_timeout_is_budgeted_to_the_remaining_cap(
    connect, world, deps, db_dsn, slow_gates
) -> None:  # noqa: ANN001
    seen: list[float] = []

    async def honours_timeout(request: httpx.Request) -> httpx.Response:
        read = float(request.extensions["timeout"]["read"])
        seen.append(read)
        await asyncio.sleep(read)
        raise httpx.ReadTimeout("stalled provider", request=request)

    judge = _judge(db_dsn, httpx.MockTransport(honours_timeout))
    rows0, _ = await _counts(connect)
    try:
        out, elapsed = await _run(connect, world, deps, judge)
    finally:
        await judge.aclose()
    rows1, orphans = await _counts(connect)
    assert out["judged"] is False and out["judge"] == rj.RETRIEVAL_ONLY and out["reason"] == rj.TIMEOUT, out
    # two 0.8 s gates ran first: the request got what was left, not the static 3.5 s
    assert len(seen) == 1 and seen[0] <= rj.JUDGE_TIMEOUT_S - 2 * GATE_DELAY_S, seen
    assert elapsed < rj.JUDGE_TIMEOUT_S + 0.5, elapsed
    assert rows1 - rows0 == 1 and orphans == 0, (rows0, rows1, orphans)


async def test_cancelled_mid_http_still_settles_and_records(connect, world, deps, db_dsn, slow_gates) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=("stall", 30.0, {"verdict": "none", "matches": []}))  # ignores timeouts
    judge = _judge(db_dsn, llm.transport)
    rows0, _ = await _counts(connect)
    try:
        for _ in range(3):
            judge.breaker.success()
            out, elapsed = await _run(connect, world, deps, judge)
            assert out["reason"] == rj.TIMEOUT and out["judge"] == rj.RETRIEVAL_ONLY, out
            assert elapsed < rj.JUDGE_TIMEOUT_S + 1.0, elapsed
    finally:
        await judge.aclose()
    rows1, orphans = await _counts(connect)
    assert llm.calls == 3 and rows1 - rows0 == 3 and orphans == 0, (llm.calls, rows0, rows1, orphans)
