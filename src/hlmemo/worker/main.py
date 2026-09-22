"""Outbox worker (PHASE0-SPEC §1 ``jobs``, §1.1 (5), §6 ``worker/main.py``).

Loop: lease ready ``embed``/``reembed`` jobs (``FOR UPDATE SKIP LOCKED``, lease 120 s, expired
leases are re-leased), embed the chunks of each version with the pinned E5 model in batches of
``BATCH_CHUNKS``, insert into ``embeddings`` with ``model/model_revision/preproc_version/dims``, and
mark the job ``done`` — every write is fenced by the job's ``lease_token`` so a worker whose
lease expired cannot clobber a re-leased job. Failures back off 1/2/4/8/16 s and fail
permanently after ``MAX_ATTEMPTS``.

§1.1 (5): a chunk whose ``text_norm`` already has a vector under the same ``model@revision/preproc``
on the version it supersedes / was split from gets that vector copied instead of re-inferred.

``drain(conn_factory, embedder)`` runs the same loop until the queue is empty (tests / one-shot).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

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


async def _copy_from_predecessor(conn: AsyncConnection, job: Job, chunk_ids: list[int]) -> int:
    """§1.1 (5): copy vectors of identical ``text_norm`` from the superseded / split-from version."""
    if not chunk_ids:
        return 0
    p = job.payload
    cur = await conn.execute(
        """
        INSERT INTO embeddings (chunk_id, model, model_revision, preproc_version, dims, vec)
        SELECT DISTINCT ON (c.chunk_id)
               c.chunk_id, e.model, e.model_revision, e.preproc_version, e.dims, e.vec
          FROM chunks c
          JOIN memory_versions mv ON mv.version_id = c.version_id
          JOIN chunks prev ON prev.version_id = mv.supersedes_version_id AND prev.text_norm = c.text_norm
          JOIN embeddings e ON e.chunk_id = prev.chunk_id AND e.model = %(model)s
               AND e.model_revision = %(rev)s AND e.preproc_version = %(preproc)s
         WHERE c.chunk_id = ANY(%(ids)s)
         ORDER BY c.chunk_id, prev.chunk_id
        ON CONFLICT DO NOTHING
        """,
        {"ids": chunk_ids, "model": p["model"], "rev": p["model_revision"], "preproc": p["preproc_version"]},
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


# --------------------------------------------------------------------------- batch processing
def _check_job_model(job: Job) -> None:
    p = job.payload
    if p.get("model") != MODEL_ID or p.get("model_revision") != MODEL_REVISION:
        raise RuntimeError(
            f"job {job.job_id} wants {p.get('model')}@{p.get('model_revision')};"
            f" worker has {MODEL_ID}@{MODEL_REVISION}"
        )


async def process_jobs(conn: AsyncConnection, embedder: Embedder, jobs: list[Job], stats: DrainStats) -> None:
    """Embed the pending chunks of every leased job (one model call per ≤ ``BATCH_CHUNKS`` chunks)
    and commit each job's vectors + ``done`` under its lease fence."""
    pending: dict[int, list[tuple[int, str]]] = {}
    for job in jobs:
        try:
            _check_job_model(job)
            async with conn.transaction():
                chunks = await _pending_chunks(conn, job)
                copied = await _copy_from_predecessor(conn, job, [cid for cid, _ in chunks])
            await conn.commit()
            if copied:
                stats.chunks_copied += copied
                async with conn.transaction():
                    chunks = await _pending_chunks(conn, job)
                await conn.commit()
            pending[job.job_id] = chunks
        except Exception as exc:  # noqa: BLE001 - a job failure must not stop the loop
            await _handle_failure(conn, job, exc, stats)

    todo = [(job, cid, text) for job in jobs if job.job_id in pending for cid, text in pending[job.job_id]]
    vectors: dict[int, Any] = {}
    for start in range(0, len(todo), BATCH_CHUNKS):
        batch = todo[start : start + BATCH_CHUNKS]
        try:
            embedded = await asyncio.to_thread(embedder.embed_passages, [t for _, _, t in batch])
        except Exception as exc:  # noqa: BLE001
            failed_jobs = {job.job_id: job for job, _, _ in batch}
            for job in failed_jobs.values():
                pending.pop(job.job_id, None)
                await _handle_failure(conn, job, exc, stats)
            continue
        for (_, cid, _), vec in zip(batch, embedded, strict=True):
            vectors[cid] = vec

    for job in jobs:
        if job.job_id not in pending:
            continue
        rows = [(cid, vectors[cid]) for cid, _ in pending[job.job_id] if cid in vectors]
        try:
            async with conn.transaction():
                await _insert_embeddings(conn, job, rows)
                if not await _mark_done(conn, job):
                    raise _LeaseLost(job.job_id)
            await conn.commit()
            stats.jobs_done += 1
            stats.chunks_embedded += len(rows)
        except _LeaseLost:
            await conn.rollback()
            log.warning("job %s: lease lost before commit; leaving it to the new owner", job.job_id)
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
    "BATCH_CHUNKS",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "DrainStats",
    "Job",
    "drain",
    "lease_jobs",
    "main",
    "process_jobs",
    "run_forever",
    "run_once",
]
