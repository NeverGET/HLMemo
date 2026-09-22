"""Outbox worker (PHASE0-SPEC §1 ``jobs``, §1.1 (5), §6 ``worker/main.py``).

Loop: lease ready ``embed``/``reembed`` jobs (``FOR UPDATE SKIP LOCKED``, lease 120 s, expired
leases are re-leased), embed the chunks of each version with the pinned E5 model in batches of
``BATCH_CHUNKS``, insert into ``embeddings`` with ``model/model_revision/preproc_version/dims``, and
mark the job ``done`` — every write is fenced by the job's ``lease_token`` so a worker whose
lease expired cannot clobber a re-leased job. Failures back off 1/2/4/8/16 s and fail
permanently after ``MAX_ATTEMPTS``.

§1.1 (5): a chunk whose ``text_norm`` already has a vector under the same ``model@revision/preproc``
on the version it supersedes / was split from gets that vector copied instead of re-inferred.

Fencing (codex review C6): *every* embedding row a job produces — copied from the predecessor or
freshly inferred — is inserted in the one final transaction that also runs the lease-fenced
``UPDATE jobs … WHERE lease_token = :ours``. That UPDATE row-locks the job until commit, so a
takeover either happened before it (rowcount 0 → the whole transaction, copies included, rolls
back) or waits behind it and then finds the job ``done``. Before that transaction the worker only
*reads* (which chunks lack a vector, which of them have a copyable predecessor vector) and infers.

``drain(conn_factory, embedder)`` runs the same loop until the queue is empty (tests / one-shot).

Heartbeat (codex review O2): every ``HEARTBEAT_SECONDS`` at most, the loop logs
``worker heartbeat: …`` with the last committed job id + its timestamp, the number of ready jobs
and the age of the oldest ready job. An idle worker and a stalled worker both log nothing useful
otherwise — ``ready_jobs=0`` is idle, a growing ``oldest_ready_age_s`` is a stall.

Test barrier: ``HLM_WORKER_TEST_BARRIER`` (unset in production) names a row in a
``hlm_test_barrier`` table that the O2 crash test creates. See ``_test_barrier``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import AsyncConnection

from hlmemo.core import MODEL_ID, MODEL_REVISION
from hlmemo.core.embedder import Embedder, default_model_dir
from hlmemo.db.read_queries import vector_literal

log = logging.getLogger("hlmemo.worker")

LEASE_SECONDS = 120
BACKOFF_SECONDS = (1, 2, 4, 8, 16)
MAX_ATTEMPTS = len(BACKOFF_SECONDS)
BATCH_CHUNKS = 32
MAX_JOBS_PER_BATCH = 16
POLL_INTERVAL = 1.0
JOB_KINDS = ("embed", "reembed")
HEARTBEAT_SECONDS = 10.0

#: Test-only crash hook, off unless this env var names an armed ``hlm_test_barrier`` row.
BARRIER_ENV = "HLM_WORKER_TEST_BARRIER"
BARRIER_TIMEOUT_SECONDS = 300.0
BARRIER_POLL_SECONDS = 0.1

ConnFactory = Callable[[], Awaitable[AsyncConnection]]


@dataclass(slots=True)
class Job:
    job_id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    lease_token: str


@dataclass(slots=True)
class DrainStats:
    jobs_done: int = 0
    jobs_failed: int = 0
    jobs_retried: int = 0
    chunks_embedded: int = 0
    chunks_copied: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    #: Heartbeat state (O2): the last job whose fenced transaction *committed*, and when.
    last_done_job_id: int | None = None
    last_done_at: datetime | None = None
    last_heartbeat_monotonic: float = 0.0


# --------------------------------------------------------------------------- SQL
async def lease_jobs(conn: AsyncConnection, limit: int, *, lease_seconds: int = LEASE_SECONDS) -> list[Job]:
    """Lease up to ``limit`` ready jobs: queued with ``run_after <= now()`` or running with an
    expired lease. One statement, ``FOR UPDATE SKIP LOCKED`` — concurrent workers never collide."""
    token = str(uuid.uuid4())
    async with conn.transaction():
        cur = await conn.execute(
            """
            WITH cand AS (
                SELECT job_id FROM jobs
                WHERE kind = ANY(%(kinds)s)
                  AND ((status = 'queued' AND run_after <= now())
                       OR (status = 'running' AND lease_until < now()))
                ORDER BY run_after, job_id
                LIMIT %(limit)s
                FOR UPDATE SKIP LOCKED
            )
            UPDATE jobs j
               SET status = 'running', attempts = j.attempts + 1, lease_token = %(token)s,
                   lease_until = now() + make_interval(secs => %(lease)s), last_error = NULL
              FROM cand
             WHERE j.job_id = cand.job_id
            RETURNING j.job_id, j.kind, j.payload, j.attempts
            """,
            {"kinds": list(JOB_KINDS), "limit": limit, "token": token, "lease": lease_seconds},
        )
        rows = await cur.fetchall()
    await conn.commit()  # the factory may hand over a connection with an implicit transaction open
    return [Job(r[0], r[1], r[2], r[3], token) for r in rows]


async def _pending_chunks(conn: AsyncConnection, job: Job) -> list[tuple[int, str]]:
    """Chunks of the job's version that have no vector for its ``model@revision/preproc`` yet."""
    p = job.payload
    cur = await conn.execute(
        """
        SELECT c.chunk_id, c.text FROM chunks c
        WHERE c.version_id = %(vid)s
          AND NOT EXISTS (SELECT 1 FROM embeddings e
                           WHERE e.chunk_id = c.chunk_id AND e.model = %(model)s
                             AND e.model_revision = %(rev)s AND e.preproc_version = %(preproc)s)
        ORDER BY c.ordinal
        """,
        {
            "vid": p["version_id"],
            "model": p["model"],
            "rev": p["model_revision"],
            "preproc": p["preproc_version"],
        },
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


_PREDECESSOR_MATCH = """
      FROM chunks c
      JOIN memory_versions mv ON mv.version_id = c.version_id
      JOIN chunks prev ON prev.version_id = mv.supersedes_version_id AND prev.text_norm = c.text_norm
      JOIN embeddings e ON e.chunk_id = prev.chunk_id AND e.model = %(model)s
           AND e.model_revision = %(rev)s AND e.preproc_version = %(preproc)s
     WHERE c.chunk_id = ANY(%(ids)s)
"""


def _predecessor_params(job: Job, chunk_ids: list[int]) -> dict[str, Any]:
    p = job.payload
    return {
        "ids": chunk_ids,
        "model": p["model"],
        "rev": p["model_revision"],
        "preproc": p["preproc_version"],
    }


async def _copyable_chunks(conn: AsyncConnection, job: Job, chunk_ids: list[int]) -> set[int]:
    """§1.1 (5), read-only: the pending chunks whose ``text_norm`` already has a vector on the
    superseded / split-from version (they are copied in the final transaction, not inferred)."""
    if not chunk_ids:
        return set()
    cur = await conn.execute(
        "SELECT DISTINCT c.chunk_id" + _PREDECESSOR_MATCH, _predecessor_params(job, chunk_ids)
    )
    return {r[0] for r in await cur.fetchall()}


async def _copy_from_predecessor(conn: AsyncConnection, job: Job, chunk_ids: list[int]) -> int:
    """§1.1 (5): copy the predecessor vectors of ``chunk_ids`` (must run inside the final, fenced
    transaction — see the module docstring). Returns the number of rows inserted."""
    if not chunk_ids:
        return 0
    cur = await conn.execute(
        """
        INSERT INTO embeddings (chunk_id, model, model_revision, preproc_version, dims, vec)
        SELECT DISTINCT ON (c.chunk_id)
               c.chunk_id, e.model, e.model_revision, e.preproc_version, e.dims, e.vec
        """
        + _PREDECESSOR_MATCH
        + """
         ORDER BY c.chunk_id, prev.chunk_id
        ON CONFLICT DO NOTHING
        """,
        _predecessor_params(job, chunk_ids),
    )
    return cur.rowcount


async def _insert_embeddings(conn: AsyncConnection, job: Job, rows: list[tuple[int, Any]]) -> None:
    p = job.payload
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO embeddings (chunk_id, model, model_revision, preproc_version, dims, vec)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT DO NOTHING
            """,
            [
                (
                    chunk_id,
                    p["model"],
                    p["model_revision"],
                    p["preproc_version"],
                    len(vec),
                    vector_literal(vec),
                )
                for chunk_id, vec in rows
            ],
        )


async def _mark_done(conn: AsyncConnection, job: Job) -> bool:
    cur = await conn.execute(
        "UPDATE jobs SET status = 'done', done_at = now(), lease_token = NULL, lease_until = NULL"
        " WHERE job_id = %s AND lease_token = %s AND status = 'running'",
        (job.job_id, job.lease_token),
    )
    return cur.rowcount == 1


async def _mark_failed(conn: AsyncConnection, job: Job, error: str) -> str:
    """Backoff by attempt number (1/2/4/8/16 s); ``failed`` after ``MAX_ATTEMPTS``. Fenced."""
    permanent = job.attempts >= MAX_ATTEMPTS
    backoff = BACKOFF_SECONDS[min(job.attempts, MAX_ATTEMPTS) - 1]
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE jobs
               SET status = %(status)s, run_after = now() + make_interval(secs => %(backoff)s),
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
    await conn.commit()
    return "failed" if permanent else "queued"


# --------------------------------------------------------------------------- heartbeat (O2)
#: ``ready_*`` is exactly what ``lease_jobs`` would pick up next (queued and due, or a dead lease).
#: ``in_flight_*`` counts jobs held under a *live* lease. Those are invisible to the ready counters,
#: which is precisely the window a crashed worker leaves behind: until its lease expires the queue
#: looks idle (``ready_jobs=0``) while nothing is progressing. A non-zero ``in_flight_jobs`` whose
#: age approaches ``LEASE_SECONDS`` while ``last_done_job`` stands still is that stall.
_HEARTBEAT_SQL = """
    SELECT count(*) FILTER (WHERE ready)::bigint,
           COALESCE(EXTRACT(EPOCH FROM (now() - min(run_after) FILTER (WHERE ready))), 0)::float8,
           count(*) FILTER (WHERE in_flight)::bigint,
           COALESCE(EXTRACT(EPOCH FROM (now() - min(leased_at) FILTER (WHERE in_flight))), 0)::float8
      FROM (
        SELECT run_after,
               lease_until - make_interval(secs => %(lease)s) AS leased_at,
               ((status = 'queued' AND run_after <= now())
                OR (status = 'running' AND lease_until < now())) AS ready,
               (status = 'running' AND lease_until >= now()) AS in_flight
          FROM jobs
         WHERE kind = ANY(%(kinds)s) AND status IN ('queued', 'running')
      ) j
"""


async def heartbeat(
    conn: AsyncConnection, stats: DrainStats, *, force: bool = False
) -> dict[str, Any] | None:
    """Emit at most one ``worker heartbeat:`` line per ``HEARTBEAT_SECONDS`` (O2).

    Returns the heartbeat dict when it fired, ``None`` when it was rate-limited. The line carries
    the last *committed* job id and timestamp, the ready-job count and the oldest ready age, plus
    the in-flight (leased) count and age. Idle is ``ready_jobs=0 in_flight_jobs=0``; a stall is a
    backlog that ages while ``last_done_job`` does not move — either ready jobs nobody picks up, or
    a job leased by a worker that is no longer alive.
    """
    now = time.monotonic()
    if not force and (now - stats.last_heartbeat_monotonic) < HEARTBEAT_SECONDS:
        return None
    stats.last_heartbeat_monotonic = now
    cur = await conn.execute(_HEARTBEAT_SQL, {"kinds": list(JOB_KINDS), "lease": LEASE_SECONDS})
    ready_jobs, oldest_ready_age_s, in_flight_jobs, oldest_in_flight_age_s = await cur.fetchone()
    await conn.commit()
    hb: dict[str, Any] = {
        "last_done_job": stats.last_done_job_id,
        "last_done_at": stats.last_done_at.isoformat() if stats.last_done_at else None,
        "ready_jobs": int(ready_jobs),
        "oldest_ready_age_s": round(float(oldest_ready_age_s), 3) if ready_jobs else None,
        "in_flight_jobs": int(in_flight_jobs),
        "oldest_in_flight_age_s": round(float(oldest_in_flight_age_s), 3) if in_flight_jobs else None,
        "jobs_done": stats.jobs_done,
    }
    log.info(
        "worker heartbeat: last_done_job=%s last_done_at=%s ready_jobs=%s oldest_ready_age_s=%s"
        " in_flight_jobs=%s oldest_in_flight_age_s=%s jobs_done_this_process=%s",
        hb["last_done_job"],
        hb["last_done_at"],
        hb["ready_jobs"],
        hb["oldest_ready_age_s"],
        hb["in_flight_jobs"],
        hb["oldest_in_flight_age_s"],
        hb["jobs_done"],
    )
    return hb


# --------------------------------------------------------------------------- test-only crash hook
def _self_destruct() -> None:  # pragma: no cover - the process does not survive this
    """Kill *this* process (PID 1 under compose) as abruptly as the kernel allows.

    ``kill(1, SIGKILL)`` from inside the container is silently dropped: the kernel marks a PID
    namespace's init ``SIGNAL_UNKILLABLE`` and ignores every default-action signal sent to it from
    within its own namespace (``sig_task_ignored``), self-sent ones included — measured, the
    process simply keeps running. A *forced* fault is the one path that is not ignorable:
    ``force_sig_info_to_task`` clears ``SIGNAL_UNKILLABLE`` first, so the process dies immediately
    with SIGSEGV (exit 139) and Docker's ``restart: unless-stopped`` brings it back. No cleanup
    runs, the open transaction is abandoned, and the server rolls it back when the backend dies —
    which is the point of the test.
    """
    import ctypes

    sys.stdout.flush()
    sys.stderr.flush()
    os.kill(1, signal.SIGKILL)  # the real thing first; ignored for a namespace init
    time.sleep(0.2)
    ctypes.string_at(0)  # unignorable: SIGSEGV via force_sig clears SIGNAL_UNKILLABLE
    time.sleep(30)  # unreachable


async def _test_barrier(job: Job) -> None:
    """One-shot, *persisted* pause immediately before ``_mark_done`` — O2 crash test only.

    Inert unless ``HLM_WORKER_TEST_BARRIER`` names a row of a ``hlm_test_barrier`` table (the test
    creates it; a missing table is a no-op). The claim ``UPDATE … WHERE armed`` runs on an
    independent autocommit connection, so it survives the rollback of the fenced transaction *and*
    the process death: the worker Docker restarts finds the barrier already consumed and completes
    the job normally. While a claimed barrier is held, the job is ``running`` and none of its
    embeddings are committed yet — exactly the window O2 needs to observe.
    """
    name = os.environ.get(BARRIER_ENV, "").strip()
    if not name:
        return
    from hlmemo.config import get_settings

    conn = await AsyncConnection.connect(get_settings().db_dsn, autocommit=True)
    try:
        try:
            cur = await conn.execute(
                "UPDATE hlm_test_barrier SET armed = false, hit_at = now(), hit_job_id = %s,"
                " hit_pid = %s WHERE name = %s AND armed RETURNING 1",
                (job.job_id, os.getpid(), name),
            )
        except psycopg.errors.UndefinedTable:
            log.warning("test barrier %r: no hlm_test_barrier table, ignoring", name)
            return
        if cur.rowcount != 1:
            return  # already consumed — this is the restarted worker
        log.warning(
            "TEST BARRIER %r claimed by pid %s: holding job %s before _mark_done",
            name,
            os.getpid(),
            job.job_id,
        )
        deadline = time.monotonic() + BARRIER_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            cur = await conn.execute("SELECT released, action FROM hlm_test_barrier WHERE name = %s", (name,))
            row = await cur.fetchone()
            if row and row[0]:
                action = row[1]
                log.warning("TEST BARRIER %r released, action=%s", name, action)
                if action == "sigkill":
                    _self_destruct()
                return
            await asyncio.sleep(BARRIER_POLL_SECONDS)
        log.error("TEST BARRIER %r timed out after %ss", name, BARRIER_TIMEOUT_SECONDS)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


# --------------------------------------------------------------------------- batch processing
def _check_job_model(job: Job) -> None:
    p = job.payload
    if p.get("model") != MODEL_ID or p.get("model_revision") != MODEL_REVISION:
        raise RuntimeError(
            f"job {job.job_id} wants {p.get('model')}@{p.get('model_revision')};"
            f" worker has {MODEL_ID}@{MODEL_REVISION}"
        )


@dataclass(slots=True)
class _Plan:
    """What one leased job needs: chunks to copy from the predecessor and chunks to infer."""

    copy_ids: list[int]
    infer: list[tuple[int, str]]


async def process_jobs(conn: AsyncConnection, embedder: Embedder, jobs: list[Job], stats: DrainStats) -> None:
    """Embed the pending chunks of every leased job (one model call per ≤ ``BATCH_CHUNKS`` chunks)
    and commit each job's vectors (copied + inferred) and ``done`` in ONE transaction under its
    lease fence. Losing the lease rolls back everything the loser produced."""
    plans: dict[int, _Plan] = {}
    for job in jobs:
        try:
            _check_job_model(job)
            async with conn.transaction():  # read-only
                chunks = await _pending_chunks(conn, job)
                copyable = await _copyable_chunks(conn, job, [cid for cid, _ in chunks])
            await conn.commit()
            plans[job.job_id] = _Plan(
                copy_ids=[cid for cid, _ in chunks if cid in copyable],
                infer=[(cid, text) for cid, text in chunks if cid not in copyable],
            )
        except Exception as exc:  # noqa: BLE001 - a job failure must not stop the loop
            await _handle_failure(conn, job, exc, stats)

    todo = [(job, cid, text) for job in jobs if job.job_id in plans for cid, text in plans[job.job_id].infer]
    vectors: dict[int, Any] = {}
    for start in range(0, len(todo), BATCH_CHUNKS):
        batch = todo[start : start + BATCH_CHUNKS]
        try:
            embedded = await asyncio.to_thread(embedder.embed_passages, [t for _, _, t in batch])
        except Exception as exc:  # noqa: BLE001
            failed_jobs = {job.job_id: job for job, _, _ in batch}
            for job in failed_jobs.values():
                plans.pop(job.job_id, None)
                await _handle_failure(conn, job, exc, stats)
            continue
        for (_, cid, _), vec in zip(batch, embedded, strict=True):
            vectors[cid] = vec

    for job in jobs:
        plan = plans.get(job.job_id)
        if plan is None:
            continue
        rows = [(cid, vectors[cid]) for cid, _ in plan.infer if cid in vectors]
        try:
            async with conn.transaction():
                copied = await _copy_from_predecessor(conn, job, plan.copy_ids)
                await _insert_embeddings(conn, job, rows)
                await _test_barrier(job)  # inert in production; O2 crashes the process here
                if not await _mark_done(conn, job):
                    raise _LeaseLost(job.job_id)
            await conn.commit()
            stats.jobs_done += 1
            stats.chunks_copied += copied
            stats.chunks_embedded += len(rows)
            stats.last_done_job_id = job.job_id
            stats.last_done_at = datetime.now(UTC)
        except _LeaseLost:
            await conn.rollback()
            log.warning(
                "job %s: lease lost before commit; nothing written, left to the new owner", job.job_id
            )
        except Exception as exc:  # noqa: BLE001
            await _handle_failure(conn, job, exc, stats)


class _LeaseLost(RuntimeError):
    pass


async def _handle_failure(conn: AsyncConnection, job: Job, exc: Exception, stats: DrainStats) -> None:
    err = f"{type(exc).__name__}: {exc}"
    stats.errors.append(f"job {job.job_id}: {err}")
    try:
        await conn.rollback()
    except Exception:  # noqa: BLE001
        pass
    try:
        status = await _mark_failed(conn, job, err)
    except Exception as exc2:  # noqa: BLE001
        log.error("job %s: could not record failure: %s", job.job_id, exc2)
        return
    if status == "failed":
        stats.jobs_failed += 1
        log.error("job %s failed permanently after %s attempts: %s", job.job_id, job.attempts, err)
    else:
        stats.jobs_retried += 1
        log.warning("job %s attempt %s failed, retrying: %s", job.job_id, job.attempts, err)


async def run_once(conn: AsyncConnection, embedder: Embedder, stats: DrainStats) -> int:
    """Lease one batch and process it. Returns the number of jobs leased (0 = queue idle)."""
    jobs = await lease_jobs(conn, MAX_JOBS_PER_BATCH)
    if jobs:
        await process_jobs(conn, embedder, jobs, stats)
    return len(jobs)


async def drain(
    conn_factory: ConnFactory, embedder: Embedder, *, max_batches: int | None = None
) -> DrainStats:
    """Process every ready job until the queue is idle (tests: embed synchronously after writes).
    Jobs waiting on a backoff ``run_after`` in the future are not waited for."""
    stats = DrainStats()
    t0 = time.perf_counter()
    conn = await conn_factory()
    try:
        batches = 0
        while max_batches is None or batches < max_batches:
            if await run_once(conn, embedder, stats) == 0:
                break
            batches += 1
            if batches % 25 == 0:
                log.info(
                    "drain: %s jobs done, %s chunks embedded (%.0fs)",
                    stats.jobs_done,
                    stats.chunks_embedded,
                    time.perf_counter() - t0,
                )
    finally:
        await conn.close()
    stats.seconds = time.perf_counter() - t0
    return stats


async def run_forever(
    conn_factory: ConnFactory, embedder: Embedder, *, stop: asyncio.Event | None = None
) -> None:
    stop = stop or asyncio.Event()
    stats = DrainStats()
    conn: AsyncConnection | None = None
    while not stop.is_set():
        try:
            if conn is None or conn.closed:
                conn = await conn_factory()
            leased = await run_once(conn, embedder, stats)
            await heartbeat(conn, stats)
        except Exception as exc:  # noqa: BLE001 - database hiccups: reconnect after a pause
            log.error("worker loop error: %s", exc)
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass
            conn = None
            leased = 0
        if leased == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
            except TimeoutError:
                pass
    if conn is not None:
        await conn.close()


# --------------------------------------------------------------------------- entrypoint
async def _amain() -> int:
    from hlmemo.config import get_settings

    settings = get_settings()
    model_dir = default_model_dir()
    log.info("worker: model %s@%s from %s", MODEL_ID, MODEL_REVISION[:8], model_dir)
    embedder = Embedder(model_dir)

    async def factory() -> AsyncConnection:
        c = await AsyncConnection.connect(settings.db_dsn, autocommit=False)
        await c.execute("SET TIME ZONE 'UTC'")
        await c.commit()
        return c

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-POSIX
            pass
    log.info(
        "worker: polling %s (lease %ss, batch %s chunks)",
        settings.db_dsn.split("@")[-1],
        LEASE_SECONDS,
        BATCH_CHUNKS,
    )
    await run_forever(factory, embedder, stop=stop)
    log.info("worker: stopped")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BACKOFF_SECONDS",
    "BARRIER_ENV",
    "BATCH_CHUNKS",
    "HEARTBEAT_SECONDS",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "DrainStats",
    "Job",
    "drain",
    "heartbeat",
    "lease_jobs",
    "main",
    "process_jobs",
    "run_forever",
    "run_once",
]
