"""e2e #7 (D-080 report §6): the librarian processes up to ``HLM_LIBRARIAN_CONCURRENCY`` jobs at once.

* Throughput: the same review workload (one write_review job per written fact, each with a
  placement call and, from the second on, a relation call) against a stub provider with a fixed
  1.5 s latency per call, run one job at a time (``concurrency=1``, the pre-change behaviour) and
  three at a time (the default). Jobs/min are printed; three slots must be ≥ 2x faster. Every job
  reaches the same outcome with the same number of calls in both runs, every reservation is settled
  and the projections rebuild identically (per-job lease, fencing and replay determinism).
* Spend guard: with an hour cap that fits only a few worst-case reservations, three concurrent jobs
  never push ``spent + reserved`` over the cap (the reservation is one atomic statement); the
  refused job is handed back (``E_BUDGET_DEFERRED``, no attempt consumed) and the librarian pauses.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.budget import Caps
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, dump_w2b, embed, write_items

pytestmark = pytest.mark.integration

LATENCY_S = 1.5
FACTS = [
    ("Service alpha", "The alpha service listens on port 8010 behind nginx on the deploy host."),
    ("Service beta", "The beta service listens on port 8020 behind nginx on the deploy host."),
    ("Service gamma", "The gamma service listens on port 8030 behind nginx on the deploy host."),
    ("Service delta", "The delta service listens on port 8040 behind nginx on the deploy host."),
    ("Service epsilon", "The epsilon service listens on port 8050 behind nginx on the deploy host."),
    ("Service zeta", "The zeta service listens on port 8060 behind nginx on the deploy host."),
]


async def _project(connect, slug: str) -> AuthContext:  # noqa: ANN001
    """A project and a device that can write (and read) ONLY there: no cross-project candidates."""
    async with await connect() as conn:
        await seed_reserved(conn)
        cur = await conn.execute(
            "INSERT INTO projects (slug, name) VALUES (%s, %s) RETURNING project_id", (slug, slug)
        )
        (pid,) = await cur.fetchone()
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id) VALUES (%s, 'personal', %s, %s, 'trusted', now(), 1)"
            " RETURNING device_id",
            (f"dev-{slug}", f"fp-{slug}", f"h-{slug}"),
        )
        (did,) = await cur.fetchone()
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'write', 1)",
            (did, pid),
        )
        await conn.commit()
    return AuthContext(did, "personal", False, 1, {pid: Role.WRITE}, "pytest/0")


def _slow(oracle: Oracle) -> Any:
    return lambda body: ("stall", LATENCY_S, oracle(body))


async def _run(db_dsn, connect, embedder, slug: str, concurrency: int) -> dict[str, Any]:  # noqa: ANN001
    ctx = await _project(connect, slug)
    for title, body in FACTS:  # one write (= one review job) per fact
        await write_items(connect, ctx, slug, [{"kind": "fact", "title": title, "body": body}])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=_slow(Oracle()))
    provider = make_provider(db_dsn, llm)  # the DB spend guard: atomic reservations per call
    worker = make_worker(lib_settings(db_dsn, librarian_concurrency=concurrency), provider, connect)
    t0 = time.monotonic()
    n = await worker.drain()
    seconds = time.monotonic() - t0
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            """
            SELECT e.payload->'resolved'->>'outcome',
                   COALESCE(jsonb_array_length(e.payload->'request'->'calls'),
                            CASE WHEN e.payload->'request' ? 'model_id' THEN 1 ELSE 0 END)
              FROM events e JOIN projects p ON p.project_id = e.project_id
             WHERE e.kind = 'librarian' AND e.payload->'request'->>'op' = 'write_review' AND p.slug = %s
            """,
            (slug,),
        )
        per_job = sorted((o, int(c)) for o, c in await cur.fetchall())
    return {"jobs": n, "seconds": seconds, "per_job": per_job, "calls": llm.calls}


async def test_concurrency_throughput_and_determinism(db_dsn, connect, embedder) -> None:  # noqa: ANN001
    one = await _run(db_dsn, connect, embedder, "conc-one", 1)
    three = await _run(db_dsn, connect, embedder, "conc-three", 3)
    rate1 = one["jobs"] / one["seconds"] * 60
    rate3 = three["jobs"] / three["seconds"] * 60
    print(
        f"\nlibrarian throughput (stub {LATENCY_S}s/call, {one['jobs']} jobs, {one['calls']} calls):"
        f" concurrency 1: {one['seconds']:.1f}s = {rate1:.1f} jobs/min;"
        f" concurrency 3: {three['seconds']:.1f}s = {rate3:.1f} jobs/min; x{rate3 / rate1:.2f}"
    )
    assert one["jobs"] == three["jobs"] == len(FACTS)
    assert one["per_job"] == three["per_job"]  # same outcome and calls per job, one or three at a time
    assert one["calls"] == three["calls"]
    assert rate3 >= 2.0 * rate1
    async with await connect() as conn:
        cur = await conn.execute("SELECT count(*) FROM llm_reservations")
        assert (await cur.fetchone())[0] == 0  # every reservation settled
        cur = await conn.execute("SELECT count(*), count(*) FILTER (WHERE outcome = 'ok') FROM llm_calls")
        total, ok = await cur.fetchone()
        assert total == ok == one["calls"] + three["calls"]
        cur = await conn.execute(
            "SELECT count(*) FROM jobs WHERE kind = 'librarian_write' AND status <> 'done'"
        )
        assert (await cur.fetchone())[0] == 0
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
    for table in before:
        assert sorted(set(before[table]) ^ set(after[table])) == [], table
    assert after == before


async def test_concurrent_jobs_never_exceed_the_spend_cap(db_dsn, connect, embedder) -> None:  # noqa: ANN001
    ctx = await _project(connect, "conc-cap")
    for title, body in FACTS:
        await write_items(connect, ctx, "conc-cap", [{"kind": "fact", "title": title, "body": body}])
    await embed(connect, embedder)
    cap = Decimal("0.006")  # a few worst-case reservations (placement ~$0.002, relation ~$0.005)
    llm = ScriptedLLM(default=lambda body: ("stall", 0.3, Oracle()(body)))
    provider = make_provider(db_dsn, llm, caps=Caps(cap, Decimal(100), Decimal(100)))
    worker = make_worker(lib_settings(db_dsn, librarian_concurrency=3), provider, connect)
    await worker.drain()
    await provider.aclose()
    assert worker.paused and worker.pause_reason == "budget"
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT spent_usd, reserved_usd, cap_usd FROM llm_budget WHERE period_kind = 'hour'"
        )
        rows = await cur.fetchall()
        assert rows and all(spent + reserved <= capped for spent, reserved, capped in rows)
        cur = await conn.execute("SELECT count(*) FROM llm_reservations")
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT count(*) FROM jobs WHERE kind = 'librarian_write' AND status = 'queued'"
            " AND last_error = 'E_BUDGET_DEFERRED' AND attempts = 0"
        )
        assert (await cur.fetchone())[0] >= 1  # handed back, no attempt consumed
