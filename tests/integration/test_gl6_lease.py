"""G-L6 — lease renewal (PHASE2-4-ROADMAP §2): a job that outlives its lease many times over is
never re-leased while its worker renews (here: lease 2 s, renewal every 0.5 s, job 5 s — the
production 300 s job under a 120 s lease, scaled), and a worker killed with SIGKILL stops renewing,
so its job is re-leased after the lease expires and applied exactly once."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.worker.lease import lease_jobs
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    SlowHandler,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._write_fixtures import World, count, seed_world

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        spec = job_spec(kind="librarian_write", dedupe_key="librarian_write:slow:1", payload={"op": "slow"})
        await enqueue(conn, project_id=w.main_id, trigger_device_id=w.dev_a, specs=[spec])
        await conn.commit()
        return w


async def _job_state(connect) -> tuple[str, int]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts FROM jobs WHERE dedupe_key = 'librarian_write:slow:1'"
        )
        row = await cur.fetchone()
        await conn.commit()
    return row[0], row[1]


async def _applied_events(connect) -> int:  # noqa: ANN001
    async with await connect() as conn:
        return await count(
            conn,
            "events",
            "kind = 'librarian' AND payload->'resolved'->'done'->>'dedupe_key' = %s",
            ("librarian_write:slow:1",),
        )


async def test_gl6_long_job_is_not_released_while_renewed(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    settings = lib_settings(db_dsn, librarian_lease_s=2, librarian_lease_renew_s=0.5)
    provider = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    worker = make_worker(settings, provider, connect, handlers={"slow": SlowHandler(5.0)})
    run = asyncio.create_task(worker.run_once())
    stolen = []
    await asyncio.sleep(0.3)
    while not run.done():  # a competing worker keeps trying to lease the same job
        async with await connect() as conn:
            stolen += await lease_jobs(conn, ["librarian_write"], 5, lease_seconds=2)
        await asyncio.sleep(0.2)
    assert await run == 1
    assert stolen == []
    assert await _job_state(connect) == ("done", 1)
    assert await _applied_events(connect) == 1
    await provider.aclose()


async def test_gl6_sigkill_releases_job_exactly_once(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    code = (
        "import asyncio, sys; from tests.integration._librarian_fixtures import run_slow_worker;"
        " asyncio.run(run_slow_worker(sys.argv[1], '2', '0.5'))"
    )
    proc = subprocess.Popen(  # noqa: ASYNC220 - a real process must be SIGKILLed
        [sys.executable, "-c", code, db_dsn], cwd=ROOT, env=os.environ.copy()
    )
    try:
        deadline = time.monotonic() + 60
        while (await _job_state(connect))[0] != "running":
            assert time.monotonic() < deadline, "subprocess worker never leased the job"
            assert proc.poll() is None, "subprocess worker exited early"
            await asyncio.sleep(0.1)
        await asyncio.sleep(2.5)  # it keeps renewing: still running, still its lease
        assert (await _job_state(connect)) == ("running", 1)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        cur = await conn.execute(
            "SELECT lease_until > now() FROM jobs WHERE dedupe_key = 'librarian_write:slow:1'"
        )
        (still_leased,) = await cur.fetchone()
    assert still_leased  # nobody may take it before the dead worker's lease expires
    settings = lib_settings(db_dsn, librarian_lease_s=2, librarian_lease_renew_s=0.5)
    provider = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    worker = make_worker(settings, provider, connect, handlers={"slow": SlowHandler(0.0)})
    deadline = time.monotonic() + 10
    while await worker.run_once() == 0:
        assert time.monotonic() < deadline, "the expired lease was never re-leased"
        await asyncio.sleep(0.2)
    assert await _job_state(connect) == ("done", 2)
    assert await _applied_events(connect) == 1
    assert await worker.run_once() == 0
    await provider.aclose()
