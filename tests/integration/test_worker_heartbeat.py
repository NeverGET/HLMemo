"""O2 heartbeat (codex review 09 §4): the worker must make progress observable.

``worker/main.heartbeat`` emits, at most every ``HEARTBEAT_SECONDS``, the last *committed* job id
and timestamp, the ready-job count and the age of the oldest ready job, plus the in-flight (leased)
count and age. Together they separate "idle" (``ready_jobs=0 in_flight_jobs=0``) from "stalled":
a ready backlog nobody picks up, or — the shape a crashed worker leaves behind — a job held under a
live lease that will never be committed, which the ready counters alone report as idle.

The live-container half of this (a restarted worker really logs the line) is asserted by
``test_worker_restart.py``.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.worker.main import (
    HEARTBEAT_SECONDS,
    DrainStats,
    heartbeat,
    lease_jobs,
    process_jobs,
)
from tests.integration._write_fixtures import MAIN, item, seed_world, write_req

pytestmark = pytest.mark.integration


async def _queue_stale_job(conn, *, age_seconds: int) -> int:
    """A ready ``embed`` job that nothing will ever finish — the shape of a stalled queue."""
    cur = await conn.execute(
        "INSERT INTO jobs (kind, dedupe_key, payload, run_after)"
        " VALUES ('embed', %s, %s::jsonb, now() - make_interval(secs => %s)) RETURNING job_id",
        (f"embed:stall:{uuid.uuid4()}", json.dumps({"version_id": -1}), age_seconds),
    )
    return (await cur.fetchone())[0]


async def test_heartbeat_reports_stalled_queue_then_last_committed_job(connect, caplog):
    deps = default_deps()
    async with await connect() as conn:
        stats = DrainStats()

        # --- idle: nothing ready, nothing ever committed ------------------------------------
        idle = await heartbeat(conn, stats, force=True)
        assert idle == {
            "last_done_job": None,
            "last_done_at": None,
            "ready_jobs": 0,
            "oldest_ready_age_s": None,
            "in_flight_jobs": 0,
            "oldest_in_flight_age_s": None,
            "jobs_done": 0,
        }

        # --- rate limit: at most one line per HEARTBEAT_SECONDS ------------------------------
        assert HEARTBEAT_SECONDS >= 10.0
        assert await heartbeat(conn, stats) is None

        # --- stalled: ready jobs pile up and age, last_done_job never moves -------------------
        stale_job = await _queue_stale_job(conn, age_seconds=45)
        await conn.commit()
        stalled = await heartbeat(conn, stats, force=True)
        assert stalled["ready_jobs"] == 1
        assert stalled["oldest_ready_age_s"] >= 45.0
        assert stalled["last_done_job"] is None

        await _queue_stale_job(conn, age_seconds=90)
        await conn.commit()
        worse = await heartbeat(conn, stats, force=True)
        assert worse["ready_jobs"] == 2
        # The queue is stalled, not idle: the backlog and its age grow, nothing gets committed.
        assert worse["oldest_ready_age_s"] > stalled["oldest_ready_age_s"]
        assert worse["last_done_job"] is None and worse["jobs_done"] == 0

        # --- crashed-worker stall: a live lease nobody will ever commit is NOT "idle" -----------
        await conn.execute(
            "UPDATE jobs SET status = 'running', lease_token = gen_random_uuid(),"
            " lease_until = now() + interval '30 seconds' WHERE job_id = %s",
            (stale_job,),
        )
        await conn.commit()
        crashed = await heartbeat(conn, stats, force=True)
        assert crashed["ready_jobs"] == 1  # the other one
        assert crashed["in_flight_jobs"] == 1
        # leased_at = lease_until - LEASE_SECONDS, so the in-flight job has already been held ~90 s
        assert crashed["oldest_in_flight_age_s"] > 0
        assert crashed["last_done_job"] is None

        # --- progress: a committed job moves last_done_job / last_done_at --------------------
        world = await seed_world(conn)
        await write(conn, world.ctx_a, write_req(MAIN, [item("fact", "heartbeat body")]), deps=deps)
        await conn.commit()
        await conn.execute("DELETE FROM jobs WHERE payload->>'version_id' = '-1'")
        await conn.commit()
        jobs = await lease_jobs(conn, 16)
        assert len(jobs) == 1

        class Stub:
            def embed_passages(self, passages):
                return [[0.0] * 384 for _ in passages]

        with caplog.at_level(logging.INFO, logger="hlmemo.worker"):
            await process_jobs(conn, Stub(), jobs, stats)
            done = await heartbeat(conn, stats, force=True)

        assert stats.jobs_done == 1
        assert done["last_done_job"] == jobs[0].job_id
        assert done["last_done_at"] is not None
        assert done["ready_jobs"] == 0 and done["oldest_ready_age_s"] is None
        assert done["in_flight_jobs"] == 0 and done["oldest_in_flight_age_s"] is None

        line = next(r.getMessage() for r in caplog.records if "worker heartbeat:" in r.getMessage())
        assert f"last_done_job={jobs[0].job_id}" in line
        assert "ready_jobs=0" in line
        assert "in_flight_jobs=0" in line
        assert "oldest_ready_age_s=" in line
        assert "last_done_at=" in line
