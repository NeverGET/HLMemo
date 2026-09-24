"""G-R2 latency on the loaded G3 world (opt-in ``HLM_GR2_G3=1``; run it with ``make gate-release``'s
disposable clone of ``hlm_retr``: 2,400 items, 761 lessons/experiences, ~11.6k chunks).

The deterministic stage of ``memory.risk_check`` for the 100 G3 fixture queries used as tasks,
×3, by 3 concurrent callers each on its own connection (the G4 shape). Gate: p95 ≤ 300 ms.
Read-only: nothing is written.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
import os
import random
import statistics
import time

import pytest

from hlmemo.core import risk_service as rs
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    RetrWorld,
    _clean_tables,
    embedder,
    load_queries,
    read_deps,
    retr_world,
    world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_GR2_G3") != "1", reason="G-R2 on the G3 world: HLM_GR2_G3=1"),
]

CALLERS = 3
WARMUP = 10
P95_LIMIT_MS = 300.0


async def test_g_r2_latency_on_g3_world(connect, world: RetrWorld, read_deps) -> None:  # noqa: ANN001
    tasks = [q["query"] for q in load_queries()] * 3
    random.Random(7).shuffle(tasks)
    args = [{"project": MAIN, "task": t, "token_budget": 2000, "mode": "deterministic"} for t in tasks]
    times: list[float] = []
    warned = 0

    async def caller(chunk: list[dict]) -> None:
        nonlocal warned
        async with await connect() as conn:
            for i, a in enumerate(chunk):
                t0 = time.perf_counter()
                out = await rs.risk_check(conn, world.ctx_reader, a, deps=read_deps)
                ms = (time.perf_counter() - t0) * 1000
                await conn.commit()
                assert out["judged"] is False and out["candidates_considered"] <= rs.TOP_K
                warned += out["verdict"] == rs.VERDICT_WARN
                if i >= WARMUP:
                    times.append(ms)

    await asyncio.gather(*(caller(args[i::CALLERS]) for i in range(CALLERS)))
    p50 = statistics.median(times)
    p95 = statistics.quantiles(times, n=20, method="inclusive")[18]
    print(
        f"\nG-R2 on the G3 world: n={len(times)} p50={p50:.1f} ms p95={p95:.1f} ms max={max(times):.1f} ms "
        f"({warned}/{len(args)} deterministic warnings)"
    )
    assert p95 <= P95_LIMIT_MS, p95
