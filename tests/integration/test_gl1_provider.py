"""G-L1 — provider contract against a scripted stub (PHASE2-4-ROADMAP W2a).

Invalid JSON → one schema retry; 429×3 → backoff 1/2/4 s then success; 5xx×6 → the primary
exhausts its 5 attempts (backoff 1/2/4/8 s) and the fallback answers after one more failure;
schema-invalid×2 → ``schema_fail``; five exhausted calls open the breaker (no network, a
``breaker_open`` row) which half-opens after its window. A fake clock asserts every backoff; the
``llm_calls`` rows are asserted exactly (profile, outcome, reservation, cost) and the reservation
windows settle to exactly the ledger's spend.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hlmemo.librarian.errors import BreakerOpen, ProviderUnavailable, SchemaFail
from hlmemo.librarian.prompts import load_task
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FALLBACK,
    PRIMARY,
    FakeClock,
    ScriptedLLM,
    chat,
    ledger_rows,
    make_provider,
)

pytestmark = pytest.mark.integration

USER = 'JOB: contradiction\nINPUT: {"A": {"text": "x"}, "B": {"text": "y"}}'


async def _spend(conn) -> tuple[Decimal, Decimal]:  # noqa: ANN001
    cur = await conn.execute(
        "SELECT (SELECT sum(cost_usd) FROM llm_calls),"
        " (SELECT spent_usd FROM llm_budget WHERE period_kind = 'day'),"
        " (SELECT reserved_usd FROM llm_budget WHERE period_kind = 'day')"
    )
    ledger, spent, reserved = await cur.fetchone()
    assert reserved == 0
    return ledger, spent


async def test_gl1_invalid_json_retried_once(db_dsn, connect) -> None:  # noqa: ANN001
    llm = ScriptedLLM(["this is not json {", chat(CONTRADICTS_B, cost=0.0001)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    res = await p.complete(load_task("contradiction"), USER, job_id=7)
    assert res.output == CONTRADICTS_B and res.profile == PRIMARY
    assert clock.sleeps == []
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "schema_fail"), (PRIMARY, "schema_retry_ok")]
        ledger, spent = await _spend(conn)
        assert ledger == spent
    await p.aclose()


async def test_gl1_429_backoff_then_ok(db_dsn, connect) -> None:  # noqa: ANN001
    llm = ScriptedLLM([429, 429, 429, chat(CONTRADICTS_B)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    res = await p.complete(load_task("contradiction"), USER, job_id=8)
    assert res.profile == PRIMARY
    assert clock.sleeps == [1, 2, 4]
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "http_error")] * 3 + [(PRIMARY, "ok")]
        cur = await conn.execute(
            "SELECT outcome, reserved_usd > 0, cost_usd FROM llm_calls ORDER BY created_at, call_id"
        )
        rows = await cur.fetchall()
        assert all(reserved for _, reserved, _ in rows)  # every network attempt reserved first
        assert [c for o, _, c in rows if o == "http_error"] == [0, 0, 0]  # no bill for a 429
        ledger, spent = await _spend(conn)
        assert ledger == spent and ledger > 0  # usage without `cost`: tokens × profile prices
    await p.aclose()


async def test_gl1_5xx_six_times_falls_back(db_dsn, connect) -> None:  # noqa: ANN001
    llm = ScriptedLLM([503, 502, 500, 504, 503, 503, chat(CONTRADICTS_B, cost=0.00002)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    res = await p.complete(load_task("contradiction"), USER, job_id=9)
    assert res.profile == FALLBACK and res.output["supersedes"] == "B"
    assert clock.sleeps == [1, 2, 4, 8, 1]
    assert llm.hosts == [f"{PRIMARY}.invalid"] * 5 + [f"{FALLBACK}.invalid"] * 2
    assert all(r["max_tokens"] == 600 for r in llm.requests)  # task bound on every request
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "http_error")] * 5 + [
            (FALLBACK, "http_error"),
            (FALLBACK, "ok"),
        ]
        ledger, spent = await _spend(conn)
        cur = await conn.execute(
            "SELECT count(*) FROM llm_calls WHERE outcome = 'http_error' AND cost_usd = reserved_usd"
            " AND reserved_usd > 0"
        )
        assert (await cur.fetchone())[0] == 6  # a 5xx may have been billed: charged at worst case
        assert ledger == spent > Decimal("0.00002")
    await p.aclose()


async def test_gl1_schema_invalid_twice_is_schema_fail(db_dsn, connect) -> None:  # noqa: ANN001
    bad = {"contradicts": "yes", "supersedes": "C", "reason": 3}
    llm = ScriptedLLM([bad, bad, chat(CONTRADICTS_B)])
    p = make_provider(db_dsn, llm)
    with pytest.raises(SchemaFail):
        await p.complete(load_task("contradiction"), USER, job_id=10)
    assert llm.calls == 2  # no fallback on a model-output failure
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "schema_fail"), (PRIMARY, "schema_fail")]
    await p.aclose()


async def test_gl1_breaker_opens_and_half_opens(db_dsn, connect) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=503)
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock, breaker_open_s=60.0)
    task = load_task("contradiction")
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            await p.complete(task, USER)
    assert llm.calls == 50  # 5 calls × (5 primary + 5 fallback attempts)
    assert p.breaker_state() == "open"
    with pytest.raises(BreakerOpen) as ei:
        await p.complete(task, USER)
    assert llm.calls == 50 and 0 < ei.value.retry_after_s <= 60
    async with await connect() as conn:
        rows = await ledger_rows(conn)
        assert rows[-2:] == [(PRIMARY, "breaker_open"), (FALLBACK, "breaker_open")]
        assert sum(1 for _, o in rows if o == "http_error") == 50
    clock.t += 61  # window over: one half-open trial per profile, success closes
    llm.default = chat(CONTRADICTS_B)
    res = await p.complete(task, USER)
    assert res.profile == PRIMARY and p.breaker(PRIMARY).state == "closed"
    # the fallback was not needed, so its breaker stays open until its own trial
    assert p.breaker(FALLBACK).state == "open" and p.breaker_state() == "degraded"
    await p.aclose()


async def test_gl1_breaker_window_doubles_on_failed_trial(db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=503)
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock, breaker_open_s=60.0, breaker_max_open_s=100.0)
    task = load_task("contradiction")
    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            await p.complete(task, USER)
    b = p.breaker(PRIMARY)
    assert b.state == "open" and b.window_s == 60.0
    clock.t += 61
    with pytest.raises(ProviderUnavailable):
        await p.complete(task, USER)  # half-open trial fails → reopened, window doubled (capped)
    assert b.state == "open" and b.window_s == 100.0
    await p.aclose()


async def test_gl1_unpriced_profile_refuses_live(db_dsn) -> None:  # noqa: ANN001
    from hlmemo.librarian.errors import LlmConfigError
    from tests.integration._librarian_fixtures import stub_profile

    unpriced = stub_profile(PRIMARY)
    object.__setattr__(unpriced, "price_in_per_m", None)
    llm = ScriptedLLM(default=chat(CONTRADICTS_B))
    p = make_provider(db_dsn, llm, chain=[unpriced])
    with pytest.raises(LlmConfigError):
        await p.complete(load_task("contradiction"), USER)
    assert llm.calls == 0
    ok = make_provider(db_dsn, llm, chain=[unpriced], budget_disabled=True)
    assert (await ok.complete(load_task("contradiction"), USER)).output == CONTRADICTS_B
    await p.aclose()
    await ok.aclose()


async def test_gl1_transport_error_charged_worst_case(db_dsn, connect) -> None:  # noqa: ANN001
    """Sol 35 #7: a transport failure may have reached the provider: settle at the worst case;
    only a 4xx (rejected before generation) settles at zero."""
    llm = ScriptedLLM(["connect_error", 400, chat(CONTRADICTS_B, cost=0.00001)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    res = await p.complete(load_task("contradiction"), USER, job_id=11)
    assert res.profile == FALLBACK  # 400 is non-retryable on the primary: fallback answers
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT profile, outcome, cost_usd = reserved_usd AND reserved_usd > 0, cost_usd = 0"
            " FROM llm_calls ORDER BY created_at, call_id"
        )
        assert await cur.fetchall() == [
            (PRIMARY, "http_error", True, False),  # transport: worst case
            (PRIMARY, "http_error", False, True),  # 4xx: definitive no-charge
            (FALLBACK, "ok", False, False),
        ]
        ledger, spent = await _spend(conn)
        assert ledger == spent
    await p.aclose()
