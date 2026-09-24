"""The ``latency`` attempt policy of the provider (BACKLOG, D-084 bake-off finding): for the
deadline-bounded API callers (W2e synthesis, W2d risk judge) ONE bounded attempt per profile and no
backoff, so the fallback profile gets real time inside the caller's cap; the background librarian
keeps the retry/backoff policy. Every attempt still passes the privacy precheck, reserves/settles
and writes its ledger row; breakers count per profile.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from hlmemo.librarian.errors import DeadlineExceeded, LlmConfigError
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import LATENCY_PRIMARY_SHARE, ChainBreakers
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FALLBACK,
    PRIMARY,
    FakeClock,
    ScriptedLLM,
    chat,
    ledger_rows,
    make_provider,
    timeout_honouring,
)

pytestmark = pytest.mark.integration

USER = 'JOB: contradiction\nINPUT: {"A": {"text": "x"}, "B": {"text": "y"}}'
P_HOST, F_HOST = f"{PRIMARY}.invalid", f"{FALLBACK}.invalid"


def _deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


async def test_503_goes_straight_to_the_fallback(db_dsn, connect) -> None:  # noqa: ANN001
    llm = ScriptedLLM([503, chat(CONTRADICTS_B)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    prechecks: list[int] = []

    async def precheck() -> None:
        prechecks.append(1)

    try:
        res = await p.complete(
            load_task("contradiction"),
            USER,
            job_id=31,
            precheck=precheck,
            deadline=_deadline(6.0),
            attempt_policy="latency",
        )
    finally:
        await p.aclose()
    assert res.profile == FALLBACK and clock.sleeps == []  # no backoff, no second primary attempt
    assert llm.hosts == [P_HOST, F_HOST] and len(prechecks) == 2  # the gate before EVERY attempt
    assert p.breaker(PRIMARY).failures == 1 and p.breaker(FALLBACK).failures == 0
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "http_error"), (FALLBACK, "ok")]
        cur = await conn.execute("SELECT count(*) FROM llm_reservations")
        assert (await cur.fetchone())[0] == 0  # both reservations settled


async def test_background_policy_is_unchanged(db_dsn, connect) -> None:  # noqa: ANN001
    """The librarian worker (default policy) still retries the primary with backoff."""
    llm = ScriptedLLM([503, chat(CONTRADICTS_B)])
    clock = FakeClock()
    p = make_provider(db_dsn, llm, clock=clock)
    try:
        res = await p.complete(load_task("contradiction"), USER, job_id=32)
    finally:
        await p.aclose()
    assert res.profile == PRIMARY and clock.sleeps == [1] and llm.hosts == [P_HOST, P_HOST]
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "http_error"), (PRIMARY, "ok")]


async def test_stalled_primary_leaves_the_fallback_the_rest_of_the_deadline(db_dsn, connect) -> None:  # noqa: ANN001
    seen: list[tuple[str, float]] = []
    route = {P_HOST: "stall", F_HOST: CONTRADICTS_B}
    p = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True, timeout_s=5.5)
    p.transport = timeout_honouring(lambda host, _body: route[host], seen)
    t0 = time.monotonic()
    try:
        res = await p.complete(
            load_task("contradiction"), USER, job_id=33, deadline=_deadline(3.0), attempt_policy="latency"
        )
    finally:
        await p.aclose()
    elapsed = time.monotonic() - t0
    assert res.profile == FALLBACK and elapsed < 3.0, elapsed
    (p_host, p_read), (f_host, f_read) = seen
    assert (p_host, f_host) == (P_HOST, F_HOST)
    assert p_read <= 3.0 * LATENCY_PRIMARY_SHARE + 0.01  # one bounded primary attempt
    assert f_read > 0.9  # the fallback got the rest (minus the margin), not a sliver
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "timeout"), (FALLBACK, "ok")]


async def test_every_profile_timing_out_is_the_callers_timeout(db_dsn, connect) -> None:  # noqa: ANN001
    p = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True, timeout_s=5.5, breaker_threshold=2)
    p.transport = timeout_honouring(lambda _host, _body: "stall")
    views = ChainBreakers(lambda: p)
    try:
        for _ in range(2):
            with pytest.raises(DeadlineExceeded):
                await p.complete(
                    load_task("contradiction"),
                    USER,
                    job_id=34,
                    deadline=_deadline(1.5),
                    attempt_policy="latency",
                )
    finally:
        await p.aclose()
    # counted per PROFILE: both reached the threshold, so the task's view is now closed off
    assert p.breaker(PRIMARY).state == p.breaker(FALLBACK).state == "open"
    assert not views.allow() and views.remaining_s() > 0
    views.success()
    assert views.allow() and p.breaker_state() == "closed"
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "timeout"), (FALLBACK, "timeout")] * 2


async def test_an_open_primary_never_suppresses_the_fallback(db_dsn, connect) -> None:  # noqa: ANN001
    seen: list[tuple[str, float]] = []
    route = {P_HOST: CONTRADICTS_B, F_HOST: CONTRADICTS_B}
    p = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True, timeout_s=5.5, breaker_threshold=1)
    p.transport = timeout_honouring(lambda host, _body: route[host], seen)
    p.breaker(PRIMARY).failure()  # the primary's breaker is open
    views = ChainBreakers(lambda: p)
    assert views.allow() and views.remaining_s() == 0  # the fallback is still available
    try:
        res = await p.complete(
            load_task("contradiction"), USER, job_id=35, deadline=_deadline(3.0), attempt_policy="latency"
        )
    finally:
        await p.aclose()
    assert res.profile == FALLBACK and [h for h, _ in seen] == [F_HOST]
    assert seen[0][1] > 3.0 * LATENCY_PRIMARY_SHARE  # the last available profile: the full rest
    async with await connect() as conn:
        assert await ledger_rows(conn) == [(PRIMARY, "breaker_open"), (FALLBACK, "ok")]


async def test_unknown_policy_is_refused(db_dsn) -> None:  # noqa: ANN001
    p = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    try:
        with pytest.raises(LlmConfigError):
            await p.complete(load_task("contradiction"), USER, attempt_policy="fast")
    finally:
        await p.aclose()
