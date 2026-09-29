"""R4 (R-10) load gate: memory.ask under concurrent LONG writer calls, through the real app
(middleware, pool, MCP session manager), a mocked provider and TIME SCALING: a writer latency of
60-170 s is simulated as 3.0-8.5 s (x0.05).

While 4 memory.ask calls wait on their writer:
- no ask holds a pool connection while it waits on the provider: at EVERY provider request the
  asking task holds none, and once all 4 writers wait, nothing at all is checked out of the pool
  (the request connection was released by ``detach``);
- concurrent memory.query and memory.write traffic keeps its p95 under 2 s (two waves of 40 queries
  and 20 writes, 8 at a time: the first starts WITH the asks, so it competes with their map phases and
  their parallel internal queries, 4 x 3 against the pool's 8; the second runs during the writer wait);
- a 5th memory.ask answers ``busy`` at once (E_UNAVAILABLE, reason busy): it never waits;
and then the 4 asks answer.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian.tasks import research as rs
from hlmemo.server.app import create_app
from tests.integration._ask_fixtures import MAIN, FakeResearcher, request_job, seed_world
from tests.integration._librarian_fixtures import chat, stub_chain
from tests.integration._mcp_fixtures import ADMIN_TOKEN, call_tool_raw, trusted_device, write_args

pytestmark = pytest.mark.integration

SCALE = 0.05
WRITER_S = (60, 90, 130, 170)  # the real writer latencies the gate stands for
QUESTIONS = [f"What is the current retrieval p95 target? (ask {i})" for i in range(len(WRITER_S))]
P95_LIMIT_S = 2.0


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, int(round(0.95 * (len(values) - 1))))]


class PoolWatch:
    """Which asyncio task holds which pool connection (``getconn``/``putconn`` wrapped)."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.held: dict[int, asyncio.Task | None] = {}
        self.acquired = 0
        self._get, self._put = pool.getconn, pool.putconn

        async def getconn(*a: Any, **kw: Any) -> Any:
            conn = await self._get(*a, **kw)
            self.held[id(conn)] = asyncio.current_task()
            self.acquired += 1
            return conn

        async def putconn(conn: Any) -> None:
            self.held.pop(id(conn), None)
            await self._put(conn)

        pool.getconn, pool.putconn = getconn, putconn

    def by_current_task(self) -> int:
        me = asyncio.current_task()
        return sum(1 for t in self.held.values() if t is me)

    def checked_out(self) -> int:
        return len(self.held)


@asynccontextmanager
async def _app(db_dsn: str, handler: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    settings = get_settings(
        db_dsn=db_dsn,
        librarian_enabled=True,
        llm_mode="live",
        llm_budget_disabled=True,
        research_enabled=True,
        map_summary_enabled=False,
        research_max_usd=1.0,
        research_answer_mode="prose",
        research_timeout_s=30.0,  # the scaled 170 s writer + the rest of the loop
        admin_token=ADMIN_TOKEN,
        registration_secret=None,
    )
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        app.state.researcher = rs.Researcher(
            settings, chain=stub_chain(fallback=False), transport=httpx.MockTransport(handler)
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
            yield client, app


async def test_r10_four_long_asks_hold_no_connection_and_traffic_stays_fast(connect, db_dsn) -> None:  # noqa: ANN001
    await seed_world(connect, default_read_deps().embedder)
    fake = FakeResearcher(facts=["1.2 s"])
    latency = {q: s * SCALE for q, s in zip(QUESTIONS, WRITER_S, strict=True)}
    held_at_llm: list[tuple[str, int]] = []
    writers_waiting = 0
    all_waiting = asyncio.Event()
    watch: PoolWatch | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal writers_waiting
        body = json.loads(request.content)
        job, payload = request_job(body)
        assert watch is not None
        held_at_llm.append((job, watch.by_current_task()))  # the asking task, waiting on the LLM
        if job == "prose":
            writers_waiting += 1
            if writers_waiting == len(QUESTIONS):
                all_waiting.set()
            await asyncio.sleep(latency[payload["question"]])
        return httpx.Response(200, json=chat(fake(body)))

    async with _app(db_dsn, handler) as (client, app):
        watch = PoolWatch(app.state.pool)
        _did, reader = await trusted_device(
            client, f"r10-r-{uuid.uuid4().hex[:6]}", grants=[{"project": MAIN, "role": "write"}]
        )

        async def ask(q: str) -> tuple[dict[str, Any], float]:
            t0 = time.perf_counter()
            res = await call_tool_raw(client, reader, "memory.ask", {"question": q, "project": MAIN})
            return res, time.perf_counter() - t0

        timings: dict[str, list[float]] = {"query": [], "write": []}
        sem = asyncio.Semaphore(8)

        async def query(i: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                res = await call_tool_raw(
                    client,
                    reader,
                    "memory.query",
                    {"project": MAIN, "query": f"retrieval p95 target {i}", "token_budget": 2000},
                )
                timings["query"].append(time.perf_counter() - t0)
                assert not res.get("isError"), res

        async def write(i: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                items = [
                    {
                        "kind": "fact",
                        "title": f"load note {i}",
                        "body": f"Load-gate note {i}: the p95 is watched.",
                    }
                ]
                res = await call_tool_raw(client, reader, "memory.write", write_args(MAIN, items))
                timings["write"].append(time.perf_counter() - t0)
                assert not res.get("isError"), res

        def wave(start: int) -> list[Any]:
            return [*(query(start + i) for i in range(40)), *(write(start + i) for i in range(20))]

        # wave 1 starts WITH the asks: it competes with their map phases and parallel queries
        t_traffic = time.perf_counter()
        asks = [asyncio.create_task(ask(q)) for q in QUESTIONS]
        await asyncio.gather(*wave(0))
        await asyncio.wait_for(all_waiting.wait(), timeout=20)
        # every ask now waits on its writer (and wave 1 is done): nothing is checked out of the pool
        assert watch.checked_out() == 0, watch.held

        # the 5th ask: busy at once, never a hang
        fifth, fifth_s = await ask("And the p95 before that? (ask 5)")
        assert fifth["isError"] and json.loads(fifth["content"][0]["text"])["details"]["reason"] == "busy"
        assert fifth_s < P95_LIMIT_S

        # wave 2 while the 4 asks still wait on their writers
        await asyncio.gather(*wave(100))
        traffic_s = time.perf_counter() - t_traffic
        assert not all(t.done() for t in asks)  # the traffic ran while the asks were still waiting

        results = await asyncio.wait_for(asyncio.gather(*asks), timeout=60)

    for res, _s in results:
        assert not res.get("isError"), res
        assert json.loads(res["content"][0]["text"])["abstained"] is False
    ask_s = sorted(s for _r, s in results)
    assert ask_s[-1] >= max(WRITER_S) * SCALE  # the longest ask really waited its scaled 170 s
    assert held_at_llm and all(n == 0 for _job, n in held_at_llm), held_at_llm
    assert watch is not None and watch.acquired > 120  # the watch saw the asks' and the traffic's connections
    q95, w95 = _p95(timings["query"]), _p95(timings["write"])
    assert q95 < P95_LIMIT_S and w95 < P95_LIMIT_S, (q95, w95)
    print(  # the gate's numbers (pytest -s)
        f"\nR-10 load gate: asks {[round(s, 2) for s in ask_s]} s (writer x{SCALE});"
        f" query p95 {q95:.3f} s (n={len(timings['query'])},"
        f" median {statistics.median(timings['query']):.3f});"
        f" write p95 {w95:.3f} s (n={len(timings['write'])}); traffic window {traffic_s:.2f} s;"
        f" 5th ask busy in {fifth_s:.3f} s; provider requests checked {len(held_at_llm)}, connections held 0"
    )
