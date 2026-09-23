"""G-L3 — the LLM is down; the core does not notice (PHASE2-4-ROADMAP W2a).

Runs on the loaded G3 retrieval world (a CLONE of ``hlm_retr``: this test writes into it) with the
librarian worker running in the same event loop against a stub provider:

1. the stub STALLS 30 s per request: 100 writes are acked (each enqueues a librarian job in its
   own transaction), then the G4 query load (3 callers, fixture queries) runs while it stalls;
2. the stub returns 503: the breaker opens, jobs are handed back without consuming attempts,
   and the same query load runs again;
3. recovery: the stub answers; every job completes — 0 lost (none failed, none left queued) and
   0 duplicate ``librarian`` events (one per job).

Gate: query p95 ≤ 500 ms across both loaded phases (the same bound as G4), 100/100 writes acked.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
import os
import random
import statistics
import time
from decimal import Decimal

import pytest

from hlmemo.core.read_service import query
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.budget import Caps
from hlmemo.librarian.provider import Clock
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    ScriptedLLM,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    stub_chain,
)
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    RetrWorld,
    _clean_tables,
    embedder,
    load_queries,
    read_deps,
    retr_world,
)

pytestmark = [
    pytest.mark.integration,
    # Opt-in like O2: it needs the loaded G3 world (10-15 min to build) on a disposable clone.
    # HLM_GL3=1 HLM_TEST_DSN=<clone of hlm_retr> pytest tests/integration/test_g3_recall.py
    #   tests/integration/test_g4_latency.py tests/integration/test_gl3_llm_down.py
    pytest.mark.skipif(os.environ.get("HLM_GL3") != "1", reason="G-L3 runs on a hlm_retr clone (HLM_GL3=1)"),
]

CALLERS = 3
QUERIES_PER_PHASE = 150
N_WRITES = 100
P95_LIMIT_MS = 500.0
STALL_S = 30.0


class FastBackoff(Clock):
    """Real monotonic time (the breaker window is real), backoff sleeps compressed 100×."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / 100)


async def _query_load(connect, world: RetrWorld, deps, n: int) -> list[float]:  # noqa: ANN001
    qs = [q["query"] for q in load_queries()]
    rng = random.Random(n)
    picks = [rng.choice(qs) for _ in range(n)]
    lat: list[float] = []

    async def caller(i: int) -> None:
        async with await connect() as conn:
            for text in picks[i::CALLERS]:
                t0 = time.perf_counter()
                await query(
                    conn, world.ctx_reader, {"project": MAIN, "query": text, "token_budget": 2000}, deps=deps
                )
                await conn.commit()
                lat.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(caller(i) for i in range(CALLERS)))
    return lat


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=100)[94]


async def test_gl3_llm_down_core_unaffected(db_dsn, connect, retr_world: RetrWorld, read_deps) -> None:  # noqa: ANN001
    world = retr_world
    from tests.integration._librarian_fixtures import seed_reserved

    async with await connect() as conn:
        await seed_reserved(conn)
    await _query_load(connect, world, read_deps, 20)  # warm-up (not measured)

    llm = ScriptedLLM(default=("stall", STALL_S, CONTRADICTS_B))
    provider = make_provider(
        db_dsn,
        llm,
        chain=stub_chain(fallback=False),
        clock=FastBackoff(),
        caps=Caps(Decimal(100), Decimal(100), Decimal(100)),
        timeout_s=60.0,
        breaker_open_s=1.0,
        breaker_max_open_s=2.0,
    )
    worker = make_worker(
        lib_settings(db_dsn, librarian_poll_s=0.05, librarian_lease_s=120), provider, connect
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(worker.run_forever(stop))
    candidates = sorted(world.version_to_logical)[:50]
    try:
        # phase 1: stalled provider; 100 writes, then the timed query load
        deps = default_deps()
        write_ms: list[float] = []

        async def writes() -> int:
            acked = 0
            async with await connect() as conn:
                for i in range(N_WRITES):
                    t0 = time.perf_counter()
                    res = await write(
                        conn,
                        world.ctx_loader,
                        {
                            "project": MAIN,
                            "request_id": f"00000000-0000-4000-8000-{i:012d}",
                            "client": "pytest/gl3",
                            "items": [
                                {
                                    "kind": "fact",
                                    "title": f"G-L3 note {i}",
                                    "body": f"Load note {i}: svc-qx7 ok.",
                                }
                            ],
                        },
                        deps=deps,
                    )
                    await enqueue_pair(
                        conn,
                        project_id=world.main_id,
                        trigger_device_id=world.loader_id,
                        subject_vid=res.versions[0].version_id,
                        candidate_vids=[candidates[i % len(candidates)]],
                        key=f"gl3:{i}",
                    )
                    await conn.commit()
                    write_ms.append((time.perf_counter() - t0) * 1000)
                    acked += 1
            return acked

        # The writes run first: their chunking is CPU work in THIS event loop, which would otherwise
        # be charged to the timed queries (in production writes run in the api process). The
        # stalled librarian keeps running (lease renewal, heartbeat) while the queries are timed.
        acked = await writes()
        lat1 = await _query_load(connect, world, read_deps, QUERIES_PER_PHASE)
        assert acked == N_WRITES
        assert llm.calls >= 1 and worker.stats.jobs_done == 0  # the librarian is stuck in the stall

        # phase 2: hard outage (503) once the stalled call returns; the breaker opens
        llm.default = 503
        lat2 = await _query_load(connect, world, read_deps, QUERIES_PER_PHASE)
        deadline = time.monotonic() + STALL_S + 30
        while provider.breaker_state() != "open" and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.2)
        assert provider.breaker_state() == "open"

        # phase 3: recovery
        llm.default = CONTRADICTS_B
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            async with await connect() as conn:
                cur = await conn.execute(
                    "SELECT count(*) FILTER (WHERE status = 'done'), count(*) FROM jobs"
                    " WHERE kind = 'librarian_write' AND dedupe_key LIKE 'librarian_write:gl3:%'"
                )
                done, total = await cur.fetchone()
                await conn.commit()
            if done == total == N_WRITES:
                break
            await asyncio.sleep(0.5)
    finally:
        stop.set()
        await runner
        await provider.aclose()

    lat = lat1 + lat2
    p95 = _p95(lat)
    print(
        f"\nG-L3: {len(lat)} queries p50 {statistics.median(lat):.1f} ms p95 {p95:.1f} ms;"
        f" writes {N_WRITES} acked, write p95 {_p95(write_ms):.1f} ms; provider requests {llm.calls};"
        f" jobs released {worker.stats.jobs_released}"
    )
    assert p95 <= P95_LIMIT_MS
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, count(*) FROM jobs WHERE dedupe_key LIKE 'librarian_write:gl3:%' GROUP BY 1"
        )
        assert dict(await cur.fetchall()) == {"done": N_WRITES}  # 0 lost
        cur = await conn.execute(
            "SELECT count(*), count(DISTINCT payload->'resolved'->>'done_job') FROM events"
            " WHERE kind = 'librarian' AND payload->'resolved'->>'done_job' LIKE 'librarian_write:gl3:%'"
        )
        assert await cur.fetchone() == (N_WRITES, N_WRITES)  # 0 duplicate librarian events
