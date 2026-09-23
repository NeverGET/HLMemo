"""Shared outbox lease primitives for the embed worker and the librarian (PHASE2-4-ROADMAP §2).

* ``lease_jobs``: one statement, ``FOR UPDATE SKIP LOCKED``; ready = queued and due, or running
  with an expired lease; ordered by ``(priority, run_after, job_id)`` (fixes BACKLOG consults/30:
  a flood of low-priority work no longer starves interactive jobs).
* ``keep_lease``: renews ``lease_until`` every ``every_s`` seconds on its own connection while a
  job runs, fenced by ``lease_token`` (a 300 s job under a 120 s lease is never re-leased, G-L6).
  A crashed process stops renewing, so its lease expires and the job is re-leased.
* ``mark_done`` / ``mark_failed`` / ``release``: every update is fenced by ``lease_token``.
  ``mark_failed`` backs off and fails permanently after ``max_attempts`` (5); ``release`` hands a
  job back without consuming the attempt (systemic causes: provider outage, budget pause).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

log = logging.getLogger("hlmemo.worker.lease")

ConnFactory = Callable[[], Awaitable[AsyncConnection]]
BACKOFF_SECONDS = (1, 2, 4, 8, 16)
MAX_ATTEMPTS = 5


@dataclass(slots=True)
class LeasedJob:
    job_id: int
    kind: str
    dedupe_key: str
    payload: dict[str, Any]
    attempts: int
    lease_token: str
    priority: int = 5


async def lease_jobs(
    conn: AsyncConnection, kinds: list[str] | tuple[str, ...], limit: int, *, lease_seconds: int
) -> list[LeasedJob]:
    token = str(uuid.uuid4())
    async with conn.transaction():
        cur = await conn.execute(
            """
            WITH cand AS (
                SELECT job_id FROM jobs
                WHERE kind = ANY(%(kinds)s)
                  AND ((status = 'queued' AND run_after <= now())
                       OR (status = 'running' AND lease_until < now()))
                ORDER BY priority, run_after, job_id
                LIMIT %(limit)s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE jobs j
               SET status = 'running', attempts = j.attempts + 1, lease_token = %(token)s,
                   lease_until = now() + make_interval(secs => %(lease)s), last_error = NULL
              FROM cand
             WHERE j.job_id = cand.job_id
            RETURNING j.job_id, j.kind, j.dedupe_key, j.payload, j.attempts, j.priority
            """,
            {"kinds": list(kinds), "limit": limit, "token": token, "lease": lease_seconds},
        )
        rows = await cur.fetchall()
    await conn.commit()
    rows.sort(key=lambda r: (r[5], r[0]))
    return [LeasedJob(r[0], r[1], r[2], r[3], r[4], token, r[5]) for r in rows]


async def renew(conn: AsyncConnection, job: LeasedJob, lease_seconds: int) -> bool:
    cur = await conn.execute(
        "UPDATE jobs SET lease_until = now() + make_interval(secs => %s)"
        " WHERE job_id = %s AND lease_token = %s AND status = 'running'",
        (lease_seconds, job.job_id, job.lease_token),
    )
    ok = cur.rowcount == 1
    if not conn.autocommit:
        await conn.commit()
    return ok


@contextlib.asynccontextmanager
async def keep_lease(
    conn_factory: ConnFactory, job: LeasedJob, *, lease_seconds: int, every_s: float
) -> AsyncIterator[asyncio.Event]:
    """Renew the lease in the background; the yielded event is set if the lease was lost."""
    lost = asyncio.Event()

    async def loop() -> None:
        conn: AsyncConnection | None = None
        try:
            while True:
                await asyncio.sleep(every_s)
                try:
                    if conn is None or conn.closed:
                        conn = await conn_factory()
                    if not await renew(conn, job, lease_seconds):
                        lost.set()
                        log.warning("job %s: lease lost (taken over or finished elsewhere)", job.job_id)
                        return
                except Exception as exc:  # noqa: BLE001 - keep trying; the lease has slack
                    log.warning("job %s: lease renewal failed: %s", job.job_id, exc)
                    if conn is not None:
                        with contextlib.suppress(Exception):
                            await conn.close()
                    conn = None
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()

    task = asyncio.create_task(loop(), name=f"lease-{job.job_id}")
    try:
        yield lost
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def mark_done(conn: AsyncConnection, job: LeasedJob) -> bool:
    """Fenced; call inside the job's final transaction."""
    cur = await conn.execute(
        "UPDATE jobs SET status = 'done', done_at = now(), lease_token = NULL, lease_until = NULL"
        " WHERE job_id = %s AND lease_token = %s AND status = 'running'",
        (job.job_id, job.lease_token),
    )
    return cur.rowcount == 1


async def mark_failed(
    conn: AsyncConnection, job: LeasedJob, error: str, *, max_attempts: int = MAX_ATTEMPTS
) -> str:
    permanent = job.attempts >= max_attempts
    backoff = BACKOFF_SECONDS[min(job.attempts, len(BACKOFF_SECONDS)) - 1]
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE jobs SET status = %(status)s, run_after = now() + make_interval(secs => %(backoff)s),
                   last_error = %(err)s, lease_token = NULL, lease_until = NULL
             WHERE job_id = %(id)s AND lease_token = %(token)s
            """,
            {
                "status": "failed" if permanent else "queued",
                "backoff": backoff,
                "err": error[:2000],
                "id": job.job_id,
                "token": job.lease_token,
            },
        )
    if not conn.autocommit:
        await conn.commit()
    return "failed" if permanent else "queued"


async def release(conn: AsyncConnection, job: LeasedJob, *, delay_s: float, reason: str) -> bool:
    """Hand the job back without consuming the attempt (systemic failure, not the job's fault)."""
    async with conn.transaction():
        cur = await conn.execute(
            """
            UPDATE jobs SET status = 'queued', attempts = GREATEST(attempts - 1, 0),
                   run_after = now() + make_interval(secs => %s), last_error = %s,
                   lease_token = NULL, lease_until = NULL
             WHERE job_id = %s AND lease_token = %s AND status = 'running'
            """,
            (delay_s, reason[:2000], job.job_id, job.lease_token),
        )
    if not conn.autocommit:
        await conn.commit()
    return cur.rowcount == 1


__all__ = [
    "BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "ConnFactory",
    "LeasedJob",
    "keep_lease",
    "lease_jobs",
    "mark_done",
    "mark_failed",
    "release",
    "renew",
]
