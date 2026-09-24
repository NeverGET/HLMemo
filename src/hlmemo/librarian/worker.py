"""The librarian service: ``python -m hlmemo.librarian.worker`` (PHASE2-4-ROADMAP §2, W2a).

Loop: keep up to ``HLM_LIBRARIAN_CONCURRENCY`` (default 3) jobs in flight; a free slot leases one
ready ``librarian_write`` job (``worker/lease.py``: ``(priority, run_after)``, lease renewed every
30 s). Each job, independently of the others, lets its handler plan it (reads committed, provider
calls outside any transaction), then applies the plan in ONE transaction:

1. ``T = clock_timestamp()``; the effective role (§4b ladder) for the job's project;
2. CC-3 recheck of the triggering device (``FOR SHARE``) when the plan has mutations — failure is
   ``authority_lost``: nothing applied, the event still recorded (replay stays identical);
3. role gate: ``observer`` turns every mutation into a proposal; ``assistant`` too, applying only
   ``apply_batch`` plans (owner-approved); ``autonomous`` applies the auto-rule class directly;
4. one ``librarian`` event (schema_version 2, deterministic ``request_id``) whose request is the
   ``llm/1`` audit record and whose resolved part holds only what was applied;
5. the mutations, then the lease-fenced ``done``. A lost lease rolls everything back.

Systemic failures (provider outage, open breaker, ``HLM_LLM_MODE=off``) hand the job back without
consuming an attempt; a budget refusal or the per-job call ceiling pauses the whole librarian
(heartbeat ``breaker_state=budget``). Job-specific failures back off and fail after 5 attempts.
Events per job (D-086 §2, amends D-062 / Sol 38 #6): exactly ONE terminal event (done or failed,
both under ``uuid5("job:<dedupe_key>")``); a back-off adds one compact non-terminal ``defer`` event
(≤ 4 per job); a systemic hand-back writes the job row only (non-authoritative for replay).

Connections (Sol 56 #5): at most ``HLM_LIBRARIAN_DB_CONNECTIONS`` (default 8): one own connection
per job slot at a time (opened lazily for apply/defer, never held through provider calls), the
loop, ONE shared lease renewer, and a ledger/reservation pool of one per slot
(``check_connection_envelope`` refuses a configuration that cannot fit).

Heartbeat fields: ``ready``, ``in_flight``, ``oldest_ready_age_s``, ``failed_24h``,
``spend_today_usd``, ``spend_hour_usd``, ``reserved_usd``, ``breaker_state``, ``role`` (+
``enabled``, ``jobs_done``): logged and written to ``HLM_LIBRARIAN_HEARTBEAT_FILE`` for the
container healthcheck. Librarian health is never part of the api's ``/ready``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.resolve import lock_device_access
from hlmemo.core.budget import Meter
from hlmemo.core.temporal import select_T
from hlmemo.db import write_queries as q
from hlmemo.librarian import actor
from hlmemo.librarian.budget import DbBudget, budget_snapshot
from hlmemo.librarian.errors import (
    AuthorityLost,
    BudgetDeferred,
    JobCallCapExceeded,
    LibrarianError,
    LlmDisabled,
    NotReady,
    ProviderUnavailable,
    RoleNotAuthorized,
)
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event
from hlmemo.librarian.jobs import LIBRARIAN_JOB_KINDS, assign_job_ids, insert_recorded_jobs
from hlmemo.librarian.provider import Provider, lineage_scope
from hlmemo.librarian.redact import REDACTION_VERSION
from hlmemo.librarian.reserved import reserved_ids
from hlmemo.librarian.roles import check_role_at_start, effective_role, lock_role_order, lowest_role
from hlmemo.librarian.tasks import Handler, Plan
from hlmemo.librarian.tasks.apply_batch import ApplyBatch, proposal_actions
from hlmemo.librarian.tasks.pair_check import PairCheck
from hlmemo.librarian.tasks.release_pending import ReleasePending
from hlmemo.librarian.tasks.write_review import WriteReview
from hlmemo.worker.lease import BACKOFF_SECONDS, MAX_ATTEMPTS, LeasedJob, lease_jobs, mark_done

log = logging.getLogger("hlmemo.librarian")

HANDLED_KINDS = ("librarian_write",)
BUDGET_PAUSE_S = 60.0
SWEEP_EVERY_S = 60.0
EXPIRE_EVERY_S = 600.0
#: Sol 56 #1: the safety net that re-queues eligible accepted_pending answers
RELEASE_EVERY_S = 300.0

ConnFactory = Callable[[], Awaitable[AsyncConnection]]


def default_handlers() -> dict[str, Handler]:
    return {
        PairCheck.op: PairCheck(),
        ApplyBatch.op: ApplyBatch(),
        WriteReview.op: WriteReview(),
        ReleasePending.op: ReleasePending(),
    }


AUDIT_CALL_FIELDS = ("profile", "model_id", "prompt_version", "schema_version", "input_digest", "output")


def error_code(exc: BaseException) -> str:
    """A content-free error code for job rows and logs (never exception text, which can quote
    model output or data): the ``E_...`` code a librarian error starts with, else the type."""
    msg = str(exc) if isinstance(exc, LibrarianError) else ""
    head = msg.split(" ", 1)[0]
    if head.startswith("E_") and head.replace("_", "").isalnum():
        return head
    return f"E_{type(exc).__name__}"


#: D-086 §2: the ``last_error`` codes of a SYSTEMIC hand-back (no attempt consumed, no event): the
#: only job-row state that is not event-recorded, hence not compared by the replay tests. A back-off
#: after a job-specific failure records its state in a compact event and is compared on raw fields.
SYSTEMIC_HANDBACK_CODES = frozenset(
    {
        "E_BUDGET_DEFERRED",
        "E_NOT_READY",
        "E_LLM_DISABLED",
        "E_ProviderUnavailable",
        "E_BreakerOpen",
        "E_DB_ENVELOPE",
    }
)

#: question statuses an ``apply_batch`` job applies: batch approvals and owner accepts recorded
#: under observer (D-074)
APPLICABLE = ("approved", "accepted_pending")


@dataclass(slots=True)
class _Approved:
    question_id: str
    proposal: dict[str, Any]
    status: str
    expires_at: datetime | None
    projects: frozenset[int] = frozenset()  # the question's home + project_ids
    home: int | None = None  # the question's home project


_logical_ids = actor.action_logical_ids


def audit_request(plan: Plan, job: LeasedJob) -> dict[str, Any]:
    """CC-5 ``llm/1``: ``task, job_id, capabilities, profile, model_id, prompt_version,
    schema_version, redaction_version, input_digest, output``. Flat for one provider call;
    ``calls[]`` entries of exactly that shape when a job made several; no call fields for none."""
    task = plan.calls[0]["task"] if plan.calls else plan.op
    base: dict[str, Any] = {
        "audit": "llm/1",
        "task": task,
        "job_id": job.job_id,
        "capabilities": plan.capabilities,
        "redaction_version": REDACTION_VERSION,
        "op": plan.op,
        "dedupe_key": job.dedupe_key,
        **plan.request_extra,
    }
    shaped = [
        {
            "task": c["task"],
            "job_id": job.job_id,
            "capabilities": plan.capabilities,
            "redaction_version": REDACTION_VERSION,
            **{k: c[k] for k in AUDIT_CALL_FIELDS},
        }
        for c in plan.calls
    ]
    if len(shaped) == 1:
        return {**base, **shaped[0]}
    if shaped:
        base["calls"] = shaped
    return base


class _LeaseLost(RuntimeError):
    pass


class _Duplicate(RuntimeError):
    pass


@dataclass(slots=True)
class Stats:
    jobs_done: int = 0
    jobs_failed: int = 0
    jobs_released: int = 0
    last_done_job: int | None = None


#: how long a job waits for a free connection slot of the envelope before it is handed back
CONN_WAIT_S = 60.0


class LibrarianConfigError(LibrarianError):
    """The librarian refuses to start with this configuration."""


def check_connection_envelope(settings: Any) -> None:
    """Sol 56 #5: the librarian process's database connections stay within
    ``HLM_LIBRARIAN_DB_CONNECTIONS`` (default 8). Per job slot: at most one own connection at a time
    (plan reads, prechecks and apply are sequential within the job; the lease is renewed by ONE
    shared renewer) and at most one pooled ledger/reservation connection (one provider call in
    flight per job). Plus the service loop (lease, heartbeat, sweeps) and the shared renewer:
    ``2 × concurrency + 2 ≤ envelope``."""
    n = int(settings.librarian_concurrency)
    envelope = int(settings.librarian_db_connections)
    if 2 * n + 2 > envelope:
        raise LibrarianConfigError(
            f"E_CONFIG HLM_LIBRARIAN_CONCURRENCY={n} needs 2*{n}+2={2 * n + 2} database connections,"
            f" HLM_LIBRARIAN_DB_CONNECTIONS={envelope}"
        )


class _BoundedConnect:
    """A connection factory with a hard cap on the connections open at once (the envelope). A slot
    is taken on connect and given back on close (``async with`` or ``close()``); a caller that waits
    longer than ``CONN_WAIT_S`` gets ``ProviderUnavailable`` (systemic: the job is handed back)."""

    def __init__(self, factory: ConnFactory, limit: int) -> None:
        self.factory = factory
        self.limit = limit
        self.sem = asyncio.Semaphore(limit)
        self.open = 0
        self.peak = 0

    async def __call__(self) -> AsyncConnection:
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=CONN_WAIT_S)
        except TimeoutError as exc:
            raise ProviderUnavailable("E_DB_ENVELOPE no connection slot", retry_after_s=5.0) from exc
        try:
            conn = await self.factory()
        except BaseException:
            self.sem.release()
            raise
        self.open += 1
        self.peak = max(self.peak, self.open)
        original = conn.close
        state = {"released": False}

        def release() -> None:  # exactly once: on close, or when a connection that is never
            if not state["released"]:  # closed explicitly (broken: __aexit__ skips close) is dropped
                state["released"] = True
                self.open -= 1
                self.sem.release()

        async def close() -> None:
            try:
                await original()
            finally:
                release()

        weakref.finalize(conn, release)
        conn.close = close  # type: ignore[method-assign]
        return conn


class _JobConn:
    """A job's own connection, opened lazily and at most one at a time."""

    def __init__(self, connect: ConnFactory) -> None:
        self._connect = connect
        self._conn: AsyncConnection | None = None

    async def get(self) -> AsyncConnection:
        if self._conn is None or self._conn.closed:
            self._conn = await self._connect()
            await self._conn.commit()
        return self._conn

    async def fresh(self) -> AsyncConnection:
        """The connection after a failure: rolled back (or reopened when it is broken)."""
        if self._conn is not None and not self._conn.closed:
            try:
                await self._conn.rollback()
                return self._conn
            except Exception:  # noqa: BLE001 - a broken connection: reopen
                await self.close()
        return await self.get()

    async def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None


class _LeaseRenewer:
    """ONE lease keeper for every job in flight (Sol 56 #5): every ``every_s`` a single short
    connection renews all watched leases in one statement, fenced by ``(job_id, lease_token)``; a
    job whose row no longer matches (taken over or finished elsewhere) gets its ``lost`` event, so
    it applies nothing (G-L6). The task runs only while jobs are watched."""

    def __init__(self, connect: ConnFactory, *, lease_s: int, every_s: float) -> None:
        self._connect = connect
        self.lease_s = lease_s
        self.every_s = every_s
        self._jobs: dict[int, tuple[LeasedJob, asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None

    @contextlib.asynccontextmanager
    async def watch(self, job: LeasedJob) -> AsyncIterator[asyncio.Event]:
        lost = asyncio.Event()
        self._jobs[job.job_id] = (job, lost)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="librarian-lease-renewer")
        try:
            yield lost
        finally:
            self._jobs.pop(job.job_id, None)
            if not self._jobs and self._task is not None:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
                self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.every_s)
            watched = list(self._jobs.values())
            if not watched:
                continue
            try:
                conn = await self._connect()
                try:
                    cur = await conn.execute(
                        """
                        UPDATE jobs j SET lease_until = now() + make_interval(secs => %s)
                          FROM unnest(%s::bigint[], %s::uuid[]) AS w(job_id, token)
                         WHERE j.job_id = w.job_id AND j.lease_token = w.token AND j.status = 'running'
                        RETURNING j.job_id
                        """,
                        (
                            self.lease_s,
                            [job.job_id for job, _lost in watched],
                            [job.lease_token for job, _lost in watched],
                        ),
                    )
                    renewed = {int(r[0]) for r in await cur.fetchall()}
                    await conn.commit()
                finally:
                    await conn.close()
            except Exception as exc:  # noqa: BLE001 - keep trying; the lease has slack
                log.warning("librarian: lease renewal failed: %s", type(exc).__name__)
                continue
            for job, lost in watched:
                if job.job_id not in renewed and job.job_id in self._jobs:
                    lost.set()
                    log.warning("job %s: lease lost (taken over or finished elsewhere)", job.job_id)


class LibrarianWorker:
    def __init__(
        self,
        settings: Any,
        *,
        provider: Provider,
        connect: ConnFactory,
        handlers: dict[str, Handler] | None = None,
        budget: DbBudget | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        pool = int(getattr(settings, "librarian_concurrency", 1) or 1)
        envelope = int(getattr(settings, "librarian_db_connections", 0) or 0)
        # Sol 56 #5: every connection this worker opens itself counts against the envelope minus the
        # ledger/reservation pool (``open_pool``: one per job slot); waiting for a slot is bounded
        self.db_slots = max(3, envelope - pool) if envelope else 0
        self.connect: ConnFactory = _BoundedConnect(connect, self.db_slots) if self.db_slots else connect
        self._renewer = _LeaseRenewer(
            self.connect, lease_s=settings.librarian_lease_s, every_s=settings.librarian_lease_renew_s
        )
        self.handlers = handlers or default_handlers()
        self.budget = budget
        self.meter = Meter()
        self.stats = Stats()
        self.paused_until = 0.0
        self.pause_reason: str | None = None
        self._last_heartbeat = 0.0
        self._last_sweep = 0.0
        self._last_expire = 0.0
        self._last_release = 0.0

    # ------------------------------------------------------------------ state
    @property
    def paused(self) -> bool:
        if self.pause_reason and time.monotonic() >= self.paused_until:
            log.info("librarian: resuming after %s pause", self.pause_reason)
            self.pause_reason = None
        return self.pause_reason is not None

    def pause(self, reason: str, seconds: float) -> None:
        self.pause_reason = reason
        self.paused_until = time.monotonic() + seconds
        log.warning("librarian: paused (%s) for %.0fs", reason, seconds)

    @property
    def breaker_state(self) -> str:
        if self.paused and self.pause_reason == "budget":
            return "budget"
        return self.provider.breaker_state()

    # ------------------------------------------------------------------ loop
    @property
    def concurrency(self) -> int:
        return max(1, int(getattr(self.settings, "librarian_concurrency", 1) or 1))

    async def lease_one(self) -> LeasedJob | None:
        """Lease the next ready job (priority order), or None (paused / nothing ready)."""
        if self.paused:
            return None
        async with await self.connect() as conn:
            jobs = await lease_jobs(
                conn,
                HANDLED_KINDS,
                1,
                lease_seconds=self.settings.librarian_lease_s,
                # D-074 (Sol 49 #2): an OBSERVER-configured librarian can never apply, so it does not
                # even lease an apply job: it stays queued (no attempt, no event) for a promoted worker
                exclude_ops=("apply_batch",) if self.settings.librarian_role == "observer" else (),
            )
        return jobs[0] if jobs else None

    async def run_once(self) -> int:
        """Lease and process ONE job, sequentially (tests; the service uses ``run_slots``)."""
        job = await self.lease_one()
        if job is None:
            return 0
        await self.process(job)
        return 1

    async def _guarded(self, job: LeasedJob) -> None:
        """``process`` as a slot task: a connection failure before the job's own handling (which
        catches everything job-specific) must not take the loop down; the lease then expires and
        the job is re-leased (G-L6)."""
        try:
            await self.process(job)
        except Exception as exc:  # noqa: BLE001
            log.error("librarian job %s: %s: %s", job.job_id, type(exc).__name__, exc)

    async def run_slots(
        self,
        *,
        max_jobs: int | None = None,
        stop: asyncio.Event | None = None,
        concurrency: int | None = None,
    ) -> int:
        """Keep up to ``concurrency`` (``HLM_LIBRARIAN_CONCURRENCY``) jobs in flight until nothing
        is ready and nothing is in flight (or ``stop``/``max_jobs``). A free slot leases ONE job,
        so the priority order is kept job by job. Each job is fully independent: its own
        connection, lease keeper, lineage/precheck context (asyncio task-local), plan, apply
        transaction and fenced ``done`` — exactly the sequential path, N at a time. The spend guard
        (atomic reservations) and the per-lineage call ceiling are enforced in the database, so they
        hold across concurrent jobs. A budget pause stops leasing; jobs in flight finish or defer.
        Returns the number of jobs processed."""
        n_max = self.concurrency if concurrency is None else max(1, concurrency)
        in_flight: set[asyncio.Task[None]] = set()
        started = 0
        try:
            while True:
                while (
                    len(in_flight) < n_max
                    and (max_jobs is None or started < max_jobs)
                    and not (stop is not None and stop.is_set())
                ):
                    job = await self.lease_one()
                    if job is None:
                        break
                    in_flight.add(asyncio.create_task(self._guarded(job), name=f"librarian-{job.job_id}"))
                    started += 1
                if not in_flight:
                    return started
                _done, in_flight = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if in_flight:  # stop/cancel: let jobs in flight end their own way (lease-fenced)
                await asyncio.gather(*in_flight, return_exceptions=True)

    async def drain(self, *, max_jobs: int | None = None, concurrency: int | None = None) -> int:
        """Process ready jobs until none is left (tests, eval), ``HLM_LIBRARIAN_CONCURRENCY`` at a
        time (``concurrency=1``: strictly one after the other)."""
        return await self.run_slots(max_jobs=max_jobs, concurrency=concurrency)

    async def process(self, job: LeasedJob) -> None:
        """One job. Its database connection is opened only when needed (apply / defer), never held
        through the plan's provider calls (the plan opens its own short connections), and its lease
        is renewed by the worker's shared renewer: one job = at most ONE open connection of this
        process at a time (Sol 56 #5, the connection envelope)."""
        handler = self.handlers.get(str(job.payload.get("op")))
        holder = _JobConn(self.connect)
        try:
            if handler is None:
                await self.defer(await holder.get(), job, "E_UNKNOWN_OP", max_attempts=1)
                self.stats.jobs_failed += 1
                return
            async with self._renewer.watch(job) as lost:
                try:
                    with lineage_scope(job.lineage):
                        plan = await handler.plan(self, job)
                    if lost.is_set():
                        raise _LeaseLost
                    await self.apply(await holder.get(), job, plan)
                    self.stats.jobs_done += 1
                    self.stats.last_done_job = job.job_id
                except _Duplicate:
                    conn = await holder.fresh()
                    async with conn.transaction():
                        await mark_done(conn, job)
                    await conn.commit()
                except _LeaseLost:
                    await holder.fresh()
                    log.warning("job %s: lease lost before commit; nothing applied", job.job_id)
                except BudgetDeferred:
                    await self.defer(await holder.fresh(), job, "E_BUDGET_DEFERRED", release_s=BUDGET_PAUSE_S)
                    self.stats.jobs_released += 1
                    self.pause("budget", BUDGET_PAUSE_S)
                except JobCallCapExceeded as exc:
                    await self.defer(await holder.fresh(), job, error_code(exc), max_attempts=1)
                    self.stats.jobs_failed += 1
                    self.pause("budget", BUDGET_PAUSE_S)
                except ProviderUnavailable as exc:
                    code = error_code(exc)
                    code = code if code in SYSTEMIC_HANDBACK_CODES else "E_ProviderUnavailable"
                    await self.defer(await holder.fresh(), job, code, release_s=max(1.0, exc.retry_after_s))
                    self.stats.jobs_released += 1
                except NotReady as exc:  # W2b: inputs (embeddings) still in flight; no attempt consumed
                    await self.defer(
                        await holder.fresh(), job, "E_NOT_READY", release_s=max(1.0, exc.retry_after_s)
                    )
                    self.stats.jobs_released += 1
                except LlmDisabled:
                    await self.defer(await holder.fresh(), job, "E_LLM_DISABLED", release_s=30.0)
                    self.stats.jobs_released += 1
                except Exception as exc:  # noqa: BLE001 - one bad job must not stop the queue
                    code = error_code(exc)  # content-free: never the exception text
                    status = await self.defer(await holder.fresh(), job, code)
                    if status == "failed":
                        self.stats.jobs_failed += 1
                    log.warning("job %s attempt %s failed: %s", job.job_id, job.attempts, code)
        finally:
            await holder.close()

    # ------------------------------------------------------------------ defer
    async def defer(
        self,
        conn: AsyncConnection,
        job: LeasedJob,
        reason: str,
        *,
        release_s: float | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> str | None:
        """Hand the job back (``release_s``: systemic, the attempt is not consumed) or back it off /
        fail it (job-specific, ``lease.mark_failed`` semantics). Lease-fenced: a lost lease changes
        and records nothing. Returns the resulting status.

        Events (D-086 §2, amending D-062 "one event per job" and Sol 38 #6): a job has EXACTLY
        ONE terminal ``librarian`` event — its completion (``apply``) or its permanent failure
        (here) — both under the job's own request id ``uuid5("job:<dedupe_key>")``, so a job can
        never record both. Queue state lives in the job row:

        * a systemic hand-back (provider outage, open breaker, budget pause, embeddings not ready,
          LLM off) consumes no attempt and changes only scheduling hints (``run_after``,
          ``last_error``): job row only, NO event. A rebuild restores such a job as queued with its
          last recorded attempts; it may run earlier than live would have (a scheduling hint, not
          authoritative state);
        * a back-off consumes an attempt: one compact non-terminal event (op ``defer``, request id
          per attempt), at most ``MAX_ATTEMPTS - 1`` per job, so a rebuild keeps the attempt count
          that bounds retries (Sol 38 #6);
        * a permanent failure is the job's terminal event (outcome ``failed``)."""
        if release_s is not None:
            status_sql, attempts_sql, delay = "'queued'", "GREATEST(attempts - 1, 0)", float(release_s)
        else:
            permanent = job.attempts >= max_attempts
            status_sql = "'failed'" if permanent else "'queued'"
            attempts_sql = "attempts"
            delay = float(BACKOFF_SECONDS[max(0, min(job.attempts, len(BACKOFF_SECONDS)) - 1)])
        project_id = job.payload.get("project_id")
        async with conn.transaction():
            T = await q.clock_now(conn)
            cur = await conn.execute(
                f"""
                UPDATE jobs SET status = {status_sql}, attempts = {attempts_sql},
                       run_after = %(T)s + make_interval(secs => %(delay)s), last_error = %(err)s,
                       lease_token = NULL, lease_until = NULL
                 WHERE job_id = %(id)s AND lease_token = %(token)s AND status = 'running'
                RETURNING status, attempts, run_after, last_error
                """,  # noqa: S608 - fixed fragments
                {"T": T, "delay": delay, "err": reason[:2000], "id": job.job_id, "token": job.lease_token},
            )
            row = await cur.fetchone()
            if row is None:
                return None
            status, attempts, run_after, last_error = row
            if release_s is None:  # an attempt was consumed (back-off) or the job failed for good
                deferred = {
                    "dedupe_key": job.dedupe_key,
                    "status": status,
                    "attempts": int(attempts),
                    "run_after": actor.ts(run_after),
                    "last_error": last_error,
                }
                terminal = status == "failed"
                ids = await reserved_ids(conn)
                await insert_system_event(
                    conn,
                    kind="librarian",
                    project_id=project_id,
                    device_id=ids.librarian_device_id,
                    client=CLIENT,
                    request_id=uuid.uuid5(
                        NS_LIBRARIAN,
                        f"job:{job.dedupe_key}" if terminal else f"defer:{job.dedupe_key}:a{int(attempts)}",
                    ),
                    request={"op": "defer", "job_key": job.dedupe_key, "reason": last_error},
                    resolved={
                        "recorded_at": actor.ts(T),
                        "outcome": "failed" if terminal else "deferred",
                        "deferred": deferred,
                    },
                    at=T,
                )
        await conn.commit()
        return str(status)

    # ------------------------------------------------------------------ apply
    @staticmethod
    async def _lock_targets(conn: AsyncConnection, plan: Plan) -> None:
        """Review 57 (signal-only replay race): EVERY item this plan writes — the subjects of its
        placement signals (``version_signals`` is last-writer-wins per version) and the assessed
        items of its proposals — is locked with the write path's per-item lock, ONCE, in sorted
        order, BEFORE the event id is allocated. Two jobs writing the same target then commit in
        the order of their event ids, so replay (event-id order) ends in the live state. Re-taking
        a lock later in the same transaction (``_plan_questions``) is a no-op."""
        vids = sorted({int(sig["version_id"]) for sig in plan.signals})
        lids = {int(k) for prop in plan.proposals for k in prop.assessed}
        if vids:
            cur = await conn.execute(
                "SELECT DISTINCT logical_id FROM memory_versions WHERE version_id = ANY(%s)", (vids,)
            )
            lids.update(int(r[0]) for r in await cur.fetchall())
        if lids:
            await q.lock_logical_ids(conn, sorted(lids))

    async def _plan_questions(
        self,
        conn: AsyncConnection,
        job: LeasedJob,
        plan: Plan,
        ctx: Any,
        role: str,
        T: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """Proposal job: (directly applicable actions, question rows, superseded count).

        ``autonomous`` applies the auto-rule class directly; everything else becomes a question
        (``question(P)``: every touched project readable by the triggering device NOW). The actions
        of a question are applied later by an owner decision under the relevant authority
        (``apply_batch``: this job's capabilities; ``memory.answer``: the answering device)."""
        caps = plan.capabilities
        auto = role == "autonomous"
        direct: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        superseded = 0
        redact = self.provider.redactor.value
        expires = actor.ts(T + actor.QUESTION_TTL)
        # every item of every proposal locked ONCE in sorted order (Sol 46: per-proposal locking
        # interleaved across jobs could deadlock). Holding them to commit also makes the duplicate
        # check below atomic: a concurrent job proposing the same pair waits here, then sees the row.
        await q.lock_logical_ids(conn, [int(k) for prop in plan.proposals for k in prop.assessed])
        for i, prop in enumerate(plan.proposals):
            if await actor.policy_blocked(conn, prop.actions, prop.project_ids):
                # the cross-project policy changed since the plan (Sol 54 #2): neither applied nor asked
                excluded = plan.request_extra.setdefault(actor.POLICY_EXCLUDED, [])
                excluded.append(list(prop.subject_clues))
                continue
            stale = await actor.is_stale(conn, {"assessed": prop.assessed})
            superseded += int(stale)
            if (
                auto
                and prop.auto_ok
                and await lowest_role(conn, self.settings.librarian_role, set(prop.project_ids))
                == "autonomous"
            ):  # D-074: every touched project, not only the job's home
                if not stale:
                    direct.extend(prop.actions)
                continue
            if not actor.readable(ctx, caps, prop.project_ids):
                raise AuthorityLost("E_QUESTION_CAPABILITY")
            if plan.op == "write_review" and await actor.pending_question_exists(
                conn, prop.kind, sorted(set(prop.assessed.values()))
            ):
                # the same pair was already proposed (e.g. reviewed from its other side by an
                # earlier job): one question per pending proposal, never a duplicate for the owner
                plan.request_extra["duplicate_proposals"] = (
                    plan.request_extra.get("duplicate_proposals", 0) + 1
                )
                continue
            questions.append(
                {
                    "question_id": str(uuid.uuid5(NS_LIBRARIAN, f"{job.dedupe_key}#{i}")),
                    "job_key": job.dedupe_key,
                    "batch_id": None,
                    "project_id": job.payload.get("project_id"),
                    "project_ids": sorted(set(prop.project_ids)),
                    "kind": prop.kind,
                    "subject_clues": list(prop.subject_clues),
                    "subject_version_ids": sorted(set(prop.assessed.values())),
                    "proposal": {
                        "actions": prop.actions,
                        "capabilities": caps,
                        "auto_class": prop.auto_ok,
                        **redact(prop.meta),
                    },
                    "status": "superseded" if stale else "open",
                    "expires_at": expires,
                }
            )
        return direct, questions, superseded

    async def _assign_batches(
        self, conn: AsyncConnection, job: LeasedJob, questions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """§4b approval batches: fill the project's open batch up to ``BATCH_MAX`` open questions,
        then mark it ``ready`` and open the next one (ids are deterministic per job)."""
        if not questions:
            return []
        project_id = int(job.payload["project_id"])
        changes: list[dict[str, Any]] = []
        # concurrent jobs of one project (HLM_LIBRARIAN_CONCURRENCY): serialize the "one open batch
        # per project" step, else two jobs that both see no open batch both create one (unique
        # index). Taken after the item locks, like the batch row lock it precedes (no cycle); held to
        # commit, so it covers open_batch through the insert. Key: namespace 5 + hashtext of the
        # bigint id (a collision only serializes two projects' batch steps).
        await conn.execute(
            "SELECT pg_advisory_xact_lock(5, hashtext(%s))", (f"librarian_batch:{project_id}",)
        )
        batch, n = await actor.open_batch(conn, project_id)
        k = 0
        for qn in questions:
            if batch is None or (qn["status"] == "open" and n >= actor.BATCH_MAX):
                if batch is not None:
                    changes.append({"batch_id": batch, "status": "ready"})
                batch = str(uuid.uuid5(NS_LIBRARIAN, f"batch:{job.dedupe_key}:{k}"))
                k += 1
                n = 0
                changes.append(
                    {"batch_id": batch, "project_id": project_id, "status": "open", "created": True}
                )
            qn["batch_id"] = batch
            n += qn["status"] == "open"
        return changes

    @staticmethod
    async def _lock_approved(
        conn: AsyncConnection, plan: Plan, T: datetime
    ) -> tuple[list[_Approved], list[dict[str, Any]], datetime]:
        """``apply_batch`` lock order (Sol 46), the same as a request and ``memory.answer``: the
        device-access lock of every proposing device (sorted), then the question rows (``FOR
        UPDATE``, by id), then (``_apply_approved``) every logical item once, sorted. Returns the
        questions STILL ``approved`` or ``accepted_pending`` (D-074; one decided otherwise since the
        plan is left alone) and the status changes: a question past ``expires_at`` is ``expired``,
        never applied (Sol 46 #2: the 30-day TTL bounds every not-yet-applied proposal). The TTL is
        compared with the clock read AFTER each lock wait (here and after the item locks, Sol 47 #2
        and Sol 48), which becomes the job's ``T`` (the event is recorded at or after it)."""
        devices = {
            int(p["capabilities"]["trigger_device_id"])
            for _qid, p in plan.approved
            if (p.get("capabilities") or {}).get("trigger_device_id")
        }
        for device_id in sorted(devices):
            await lock_device_access(conn, device_id)
        cur = await conn.execute(
            "SELECT question_id::text, status, expires_at, project_id, project_ids FROM librarian_questions"
            " WHERE question_id = ANY(%s::uuid[]) ORDER BY question_id FOR UPDATE",
            ([qid for qid, _p in plan.approved],),
        )
        rows = {
            qid: (status, expires, frozenset({int(home), *(int(x) for x in pids)}), int(home))
            for qid, status, expires, home, pids in await cur.fetchall()
        }
        now = max(T, await q.clock_now(conn))
        live: list[_Approved] = []
        changes: list[dict[str, Any]] = []
        for qid, proposal in plan.approved:
            status, expires, projects, home = rows.get(qid, (None, None, frozenset(), None))
            if status not in APPLICABLE:
                continue
            if expires is not None and expires <= now:
                changes.append({"question_id": qid, "status": "expired"})
                continue
            live.append(_Approved(qid, proposal, status, expires, projects, home))
        return live, changes, now

    async def _apply_approved(
        self, conn: AsyncConnection, job: LeasedJob, plan: Plan, approved: list[_Approved], T: datetime
    ) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], int, list[datetime], datetime, list[dict[str, Any]]
    ]:
        """``apply_batch``: each approved question under its own proposing job's capabilities.
        After the item-lock wait the TTL is checked again against a FRESH clock (Sol 48), then the
        cross-project policy of every question (its project rows ``FOR SHARE``), then the TTL once
        more against a FRESH clock after that last policy lock (review 61: a question that expired
        while the policy read waited is ``expired``, nothing of it applied); that clock becomes the
        job's ``T``.

        D-076 approve-all staleness chains: the questions are applied in DEPENDENCY order —
        link-only proposals first (they never change a head), then by their earliest close cut,
        then by id — so a chain A←B←C closes A at B and B at C. A question whose subject an
        EARLIER question of this batch closed is rebased, not dropped: a redundant close (the item
        is already closed at an earlier or equal cut) is skipped and the rest applies
        (``rebased``; ``already_satisfied`` when nothing is left to do). A real conflict (an
        inverse supersession of one planned in this batch, or a close before a planned cut) is
        re-planned: the question is ``superseded`` and a ``write_review`` of its subjects that stay
        current after this transaction is enqueued (own lineage, recorded in ``resolved.jobs``).
        A proposal carrying a whole-item close without the v2 evidence (``legacy_close``) is never
        applied; it is re-planned the same way. An EXTERNAL revision since the proposal keeps the
        G-Q3 rule: ``superseded``, nothing applied. Returns ``(records, status changes, superseded,
        recorded_at of closed rows, T, re-plan jobs)``."""
        from hlmemo.core.temporal import parse_ts
        from hlmemo.librarian.tasks.apply_batch import legacy_close

        records: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        superseded = 0
        recorded: list[datetime] = []
        replans: list[dict[str, Any]] = []
        planned: dict[str, set[Any]] = {"links": set(), "closed": set()}
        cuts: dict[int, datetime] = {}  # logical id -> its close cut planned in this batch
        sup_edges: set[tuple[int, int]] = set()  # planned supersedes links (src, dst)
        audit: dict[str, list[str]] = {
            "rebased": [],
            "already_satisfied": [],
            "replanned": [],
            "replan_refused": [],
        }
        # D-058 propose-only: an approved widen_scope stays approved until memory.answer by a
        # writer on both projects (it never reaches the actor here)
        todo = [
            (a, actions)
            for a in approved
            for actions in [proposal_actions(a.proposal)]
            if a.proposal.get("kind") != "widen_scope"
            and not any(x.get("op") == "widen_scope" for x in actions)
        ]
        epoch = datetime(1970, 1, 1, tzinfo=T.tzinfo)

        def order(item: tuple[_Approved, list[dict[str, Any]]]) -> tuple[int, datetime, str]:
            closes = [
                parse_ts(x["valid_to"], field="valid_to") for x in item[1] if x.get("op") == "version_close"
            ]
            return (1, min(closes), item[0].question_id) if closes else (0, epoch, item[0].question_id)

        todo.sort(key=order)
        await q.lock_logical_ids(conn, [lid for _a, actions in todo for lid in _logical_ids(actions)])
        T = max(T, await q.clock_now(conn))  # the item-lock wait is over: the TTL against NOW
        policed: list[tuple[_Approved, list[dict[str, Any]]]] = []
        for a, actions in todo:
            if a.expires_at is not None and a.expires_at <= T:
                changes.append({"question_id": a.question_id, "status": "expired"})
            elif await actor.policy_blocked(conn, actions, a.projects):  # the policy NOW (Sol 54 #2)
                changes.append(
                    {
                        "question_id": a.question_id,
                        "status": "authority_lost",
                        "reason": actor.POLICY_EXCLUDED,
                    }
                )
            else:
                policed.append((a, actions))
        # review 61: the policy rows are read FOR SHARE above, and that wait can outlast a TTL. The
        # LAST policy lock is held now: the TTL once more against a FRESH clock, before any
        # staleness verdict, rebase or event id (lock order: items → policy → TTL → staleness)
        T = max(T, await q.clock_now(conn))
        for a, actions in policed:
            qid, proposal = a.question_id, a.proposal
            if a.expires_at is not None and a.expires_at <= T:
                changes.append({"question_id": qid, "status": "expired"})
                continue
            assessed: dict[str, int] = {}
            for x in actions:
                assessed.update(x.get("assessed") or {})
            # an EXTERNAL revision since the proposal (G-Q3); a subject closed earlier in THIS batch
            # is rebased below (D-076 staleness chains), not superseded
            if await actor.is_stale(conn, {"assessed": assessed}):
                changes.append({"question_id": qid, "status": "superseded", "reason": "stale"})
                superseded += 1
                continue
            conflict = legacy_close(proposal)
            keep: list[dict[str, Any]] = []
            redundant = 0
            for x in actions:
                if x.get("op") == "link_insert" and x.get("rel") == "supersedes":
                    if (int(x["dst_logical_id"]), int(x["src_logical_id"])) in sup_edges:
                        conflict = True  # the inverse supersession is planned in this batch
                if x.get("op") == "version_close" and int(x["logical_id"]) in cuts:
                    if parse_ts(x["valid_to"], field="valid_to") >= cuts[int(x["logical_id"])]:
                        redundant += 1  # already closed at an earlier (or the same) cut
                        continue
                    conflict = True  # pragma: no cover - the dependency order prevents it
                keep.append(x)
            if conflict:
                why = "legacy_close" if legacy_close(proposal) else "conflict"
                changes.append({"question_id": qid, "status": "superseded", "reason": why})
                superseded += 1
                audit["replanned"].append(qid)
                jobs, refused = await self._replan(conn, job, a, assessed, cuts)
                replans.extend(jobs)
                if refused:
                    audit["replan_refused"].append(f"{qid}: {refused}")
                continue
            chained = bool({int(k) for k in assessed} & set(cuts)) or redundant > 0
            caps = proposal.get("capabilities") or plan.capabilities
            try:
                async with conn.transaction():  # savepoint: one question's failure applies nothing of it
                    ctx = await actor.recheck(conn, caps, CLIENT)
                    trial = {k: set(v) for k, v in planned.items()}
                    recs, rec_at = await actor.materialize(conn, ctx, caps, keep, trial)
                planned = trial
            except AuthorityLost:
                changes.append({"question_id": qid, "status": "authority_lost"})
                continue
            for rec in recs:
                if rec["op"] == "version_close":
                    cuts[int(rec["logical_id"])] = parse_ts(rec["valid_to"], field="valid_to")
                if rec["op"] == "link_insert" and rec["rel"] == "supersedes":
                    sup_edges.add((int(rec["src_logical_id"]), int(rec["dst_logical_id"])))
            for x in keep:  # an idempotent (already live) supersedes link still orders the batch
                if x.get("op") == "link_insert" and x.get("rel") == "supersedes":
                    sup_edges.add((int(x["src_logical_id"]), int(x["dst_logical_id"])))
            if chained:
                audit["rebased"].append(qid)
            if not any(r["op"] != "signal_upsert" for r in recs):
                audit["already_satisfied"].append(qid)
            records.extend(recs)
            recorded.extend(rec_at)
            changes.append({"question_id": qid, "status": "applied"})
        for k, v in audit.items():
            if v:
                plan.request_extra[k] = v
        return records, changes, superseded, recorded, T, replans

    @staticmethod
    async def _replan(
        conn: AsyncConnection,
        job: LeasedJob,
        a: _Approved,
        assessed: dict[str, int],
        cuts: dict[int, datetime],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """The re-plan of a conflicting (or legacy-close) approved question: a ``write_review`` of
        its subject versions that stay current after this batch (not closed by it; not stale, which
        the caller checked), under the question's proposing capabilities, with its OWN lineage.
        Sol 56 #6: those capabilities must cover every project of the re-planned subjects (the
        privacy gate would silently drop a foreign one); otherwise nothing is enqueued and the
        reason is returned (audited as ``replan_refused``)."""
        from hlmemo.librarian.jobs import job_spec
        from hlmemo.librarian.tasks.write_review import MAX_VERSIONS
        from hlmemo.librarian.tasks.write_review import OP as REVIEW

        vids = sorted(int(v) for k, v in assessed.items() if int(k) not in cuts)
        if not vids or a.home is None:
            return [], None
        cur = await conn.execute(
            "SELECT version_id, kind, project_ids FROM memory_versions WHERE version_id = ANY(%s)", (vids,)
        )
        rows = {int(v): (k, [int(p) for p in pids]) for v, k, pids in await cur.fetchall()}
        caps = a.proposal.get("capabilities") or {}
        readable = {int(p) for p in caps.get("question") or []}
        missing = sorted({p for _k, pids in rows.values() for p in pids} - readable)
        if missing:
            return [], f"capabilities do not cover subject project(s) {missing}"
        key = f"librarian_replan:{a.question_id}:{job.dedupe_key}"
        return [
            job_spec(
                kind="librarian_write",
                dedupe_key=key,
                priority=4,
                payload={
                    "op": REVIEW,
                    "trigger": "replan",
                    "replan_of": a.question_id,
                    "versions": [
                        {
                            "version_id": v,
                            "kind": rows.get(v, (None, []))[0],
                            "client_importance": None,
                            "client_stability": True,
                        }
                        for v in vids[:MAX_VERSIONS]
                    ],
                    "project_id": a.home,
                    "capabilities": caps,
                    "lineage": str(uuid.uuid5(NS_LIBRARIAN, "lineage:" + key)),
                },
            )
        ], None

    async def _split_by_role(
        self, conn: AsyncConnection, live: list[_Approved]
    ) -> tuple[list[_Approved], list[_Approved]]:
        """(applicable, deferred): a question applies only if EVERY project it touches (its home,
        its ``project_ids`` and every project of its actions on the CURRENT rows) has an effective
        role above observer, read under the role-order lock like ``memory.answer`` (D-074, Sol 49
        #1). An A→B action while B is observer is deferred WHOLE: nothing on A or B."""
        ready: list[_Approved] = []
        deferred: list[_Approved] = []
        for a in live:
            touched = set(a.projects) | await actor.action_projects(conn, proposal_actions(a.proposal))
            role = await lowest_role(conn, self.settings.librarian_role, touched)
            (deferred if role == "observer" else ready).append(a)
        return ready, deferred

    @staticmethod
    async def _batch_after_apply(
        conn: AsyncConnection, batch_id: str, status_changes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The batch row change of an apply (the row comes last in the lock order), from the
        questions' statuses AFTER this job: open questions left (still collecting, or handed back)
        → a decided batch is ``ready`` again (to be decided anew), any other keeps its status;
        approvals or ``accepted_pending`` answers still waiting (a touched project is observer) →
        unchanged; nothing left → ``applied``. The batch row is read ``FOR UPDATE`` (Sol 56 #2): a
        concurrent writer of the same batch (a write_review filling it, an owner decision) is
        serialized before this job's event id is allocated."""
        cur = await conn.execute(
            "SELECT status FROM librarian_batches WHERE batch_id = %s FOR UPDATE", (batch_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return []
        cur = await conn.execute(
            "SELECT question_id::text, status FROM librarian_questions WHERE batch_id = %s", (batch_id,)
        )
        after = {qid: st for qid, st in await cur.fetchall()}
        after.update({c["question_id"]: c["status"] for c in status_changes if c["question_id"] in after})
        left = set(after.values())
        if "open" in left:
            return [{"batch_id": batch_id, "status": "ready"}] if row[0] == "decided" else []
        if left & set(APPLICABLE) or row[0] == "applied":
            return []
        return [{"batch_id": batch_id, "status": "applied"}]

    async def apply(self, conn: AsyncConnection, job: LeasedJob, plan: Plan) -> None:
        caps = plan.capabilities
        project_id = job.payload.get("project_id")
        async with conn.transaction():
            await lock_role_order(conn, exclusive=False)  # a demotion cannot interleave (Sol 44 #4)
            T = await q.clock_now(conn)
            role = await effective_role(conn, self.settings.librarian_role, project_id)
            ids = await reserved_ids(conn)
            outcome = plan.outcome
            applied: list[dict[str, Any]] = []
            questions: list[dict[str, Any]] = []
            status_changes: list[dict[str, Any]] = []
            batch_changes: list[dict[str, Any]] = []
            superseded = 0
            recorded: list[datetime] = []
            replans: list[dict[str, Any]] = []
            detail: str | None = None
            if plan.op == "apply_batch" and outcome == "approved":
                live, status_changes, T = await self._lock_approved(conn, plan, T)
                ready, deferred = await self._split_by_role(conn, live)
                # a question with ANY observer project applies nothing: batch approvals are handed
                # back (open again; a new decision round re-approves them, Sol 44 #7 / Sol 46); an
                # owner's accepted_pending answer (D-074) stays until that project is promoted
                status_changes += [
                    {"question_id": a.question_id, "status": "open"}
                    for a in deferred
                    if a.status == "approved"
                ]
                if deferred:
                    plan.request_extra["role_deferred"] = [a.question_id for a in deferred]
                if ready:
                    applied, changes, superseded, recorded, T, replans = await self._apply_approved(
                        conn, job, plan, ready, T
                    )
                    status_changes += changes
                    outcome = "applied" if applied else "superseded" if superseded else "no_change"
                else:
                    outcome = "role_denied" if deferred else "no_change"
                batch_changes = await self._batch_after_apply(conn, job.payload["batch_id"], status_changes)
            elif (plan.proposals or plan.signals) and outcome in ("proposed", "approved"):
                try:
                    ctx = await actor.recheck(conn, caps, CLIENT)
                    for sig in plan.signals:  # annotate(P) on the subject: every role
                        if not actor.allowed(ctx, caps, "annotate", [int(p) for p in sig["project_ids"]]):
                            raise AuthorityLost("E_ANNOTATE_CAPABILITY")
                    await self._lock_targets(conn, plan)
                    direct, questions, superseded = await self._plan_questions(conn, job, plan, ctx, role, T)
                    applied, recorded = await actor.materialize(conn, ctx, caps, [*plan.signals, *direct])
                    batch_changes = await self._assign_batches(conn, job, questions)
                    if any(qn["status"] == "open" for qn in questions):
                        outcome = "proposed"
                    elif any(m["op"] != "signal_upsert" for m in applied):
                        outcome = "applied"
                    elif superseded:
                        outcome = "superseded"
                    else:
                        outcome = "annotated" if applied else "no_change"
                except AuthorityLost as exc:
                    outcome, applied, questions, status_changes, batch_changes = (
                        "authority_lost",
                        [],
                        [],
                        [],
                        [],
                    )
                    recorded = []
                    detail = error_code(exc)
            # Sol 56 #2: the event id (= the replay order) is allocated only now, AFTER every lock of
            # this job (items, batch rows, the project's batch lock). Two jobs that conflict on a lock
            # therefore get ids in their commit order, so replay (event-id order) re-applies their
            # batch/question changes in the same order as live: a job that waited for another job's
            # batch cannot carry the smaller id.
            (event_id,) = await q.allocate_ids(conn, "events", 1)
            T = max(T, await q.clock_now(conn))
            T = select_T(T, *recorded)  # a close supersedes rows: T is after every one of them
            for qn in questions:
                qn["expires_at"] = actor.ts(T + actor.QUESTION_TTL)
            extra_jobs = [*actor.close_embed_jobs(applied), *replans]
            child_jobs = await assign_job_ids(conn, [*self._child_jobs(job, plan), *extra_jobs])
            # run_after as it stands now (a retry/backoff moved it): replay restores it (Sol 37 #8)
            cur = await conn.execute("SELECT run_after FROM jobs WHERE job_id = %s", (job.job_id,))
            row = await cur.fetchone()
            resolved: dict[str, Any] = {
                "recorded_at": actor.ts(T),
                "outcome": outcome,
                "role": role,
                "mutations": applied,
                "questions": questions,
                "question_status": status_changes,
                "batches": batch_changes,
                "superseded": superseded,
                "batch_id": questions[0]["batch_id"] if questions else None,
                "jobs": child_jobs,
                "done": {
                    "dedupe_key": job.dedupe_key,
                    "done_at": actor.ts(T),
                    "attempts": job.attempts,
                    "run_after": actor.ts(row[0]) if row else None,
                },
            }
            if detail:
                resolved["detail"] = detail
            inserted = await insert_system_event(
                conn,
                kind="librarian",
                project_id=project_id,
                device_id=ids.librarian_device_id,
                client=CLIENT,
                request_id=uuid.uuid5(NS_LIBRARIAN, f"job:{job.dedupe_key}"),
                request=audit_request(plan, job),
                resolved=resolved,
                at=T,
                event_id=event_id,
            )
            if inserted is None:
                raise _Duplicate(job.dedupe_key)
            await actor.apply_mutations(conn, applied, event_id, T)
            await actor.apply_batch_changes(conn, batch_changes, event_id, T)
            await actor.insert_questions(conn, questions, event_id, T)
            await actor.set_question_status(conn, status_changes, T)
            if resolved["jobs"]:
                await insert_recorded_jobs(conn, resolved["jobs"], event_id, T)
            if not await mark_done(conn, job, T):
                raise _LeaseLost
        await conn.commit()
        log.info(
            "job %s (%s): %s, %s applied, %s questions, %s superseded",
            job.job_id,
            plan.op,
            outcome,
            len(applied),
            len(questions),
            superseded,
        )

    @staticmethod
    def _child_jobs(job: LeasedJob, plan: Plan) -> list[dict[str, Any]]:
        """Follow-up jobs inherit the parent's loop lineage (the call ceiling spans the loop)."""
        return [{**j, "payload": {**j["payload"], "lineage": job.lineage}} for j in plan.jobs]

    # ------------------------------------------------------------------ heartbeat
    async def heartbeat(self, *, force: bool = False, enabled: bool = True) -> dict[str, Any] | None:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < self.settings.librarian_heartbeat_s:
            return None
        self._last_heartbeat = now
        async with await self.connect() as conn:
            hb = await heartbeat_fields(conn, self.settings.librarian_role)
            await conn.commit()
        hb.update(
            enabled=enabled,
            breaker_state=self.breaker_state,
            jobs_done=self.stats.jobs_done,
            last_done_job=self.stats.last_done_job,
        )
        emit_heartbeat(hb, self.settings.librarian_heartbeat_file)
        return hb

    async def maybe_sweep(self) -> None:
        if self.budget is None or time.monotonic() - self._last_sweep < SWEEP_EVERY_S:
            return
        self._last_sweep = time.monotonic()
        swept = await self.budget.sweep()
        if swept:
            log.warning("librarian: swept %s expired reservation(s) as worst-case spend", swept)

    async def maybe_expire(self) -> None:
        """W2c: open questions older than 30 days expire (one recorded event per project)."""
        if time.monotonic() - self._last_expire < EXPIRE_EVERY_S:
            return
        self._last_expire = time.monotonic()
        from hlmemo.librarian.questions import expire_due

        async with await self.connect() as conn:
            async with conn.transaction():
                n = await expire_due(conn)
            await conn.commit()
        if n:
            log.info("librarian: expired %s question(s)", n)

    async def maybe_release(self, *, force: bool = False) -> int:
        """Sol 56 #1 safety net: every ``RELEASE_EVERY_S`` a librarian above observer re-evaluates
        EVERY ``accepted_pending`` answer (one query; nothing when there is none) and queues an
        ``apply_batch`` job for each batch whose answers are eligible now — whatever event would
        have released them was missed (a revision while the librarian was off, a crash). The
        release is ONE ``librarian`` event (op ``release_pending``, trigger ``sweep``) whose
        ``resolved.jobs`` replay as-is. Returns the number of jobs queued."""
        if self.settings.librarian_role == "observer":
            return 0
        if not force and time.monotonic() - self._last_release < RELEASE_EVERY_S:
            return 0
        self._last_release = time.monotonic()
        from hlmemo.librarian.roles import release_now

        async with await self.connect() as conn:
            async with conn.transaction():
                await lock_role_order(conn, exclusive=False)
                cur = await conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM librarian_questions WHERE status = 'accepted_pending'"
                    " AND kind <> 'widen_scope')"  # D-086: a widen waits for the owner's answer
                )
                if not (await cur.fetchone())[0]:
                    return 0
                (event_id,) = await q.allocate_ids(conn, "events", 1)
                jobs = await release_now(conn, self.settings.librarian_role, f":sweep{event_id}")
                if not jobs:
                    return 0
                await assign_job_ids(conn, jobs)
                T = await q.clock_now(conn)
                ids = await reserved_ids(conn)
                await insert_system_event(
                    conn,
                    kind="librarian",
                    project_id=None,
                    device_id=ids.librarian_device_id,
                    client=CLIENT,
                    request_id=uuid.uuid5(NS_LIBRARIAN, f"release:sweep:{event_id}"),
                    request={
                        "op": "release_pending",
                        "trigger": "sweep",
                        "released": [j["payload"]["batch_id"] for j in jobs],
                    },
                    resolved={"recorded_at": actor.ts(T), "jobs": jobs},
                    at=T,
                    event_id=event_id,
                )
                await insert_recorded_jobs(conn, jobs, event_id, T)
            await conn.commit()
        log.info("librarian: sweeper released %s pending batch(es)", len(jobs))
        return len(jobs)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """The service loop: up to ``HLM_LIBRARIAN_CONCURRENCY`` jobs in flight (``run_slots``
        semantics), with the sweep/expiry/heartbeat housekeeping between leases. On stop, no new
        job is leased and the jobs in flight end their own way (lease-fenced)."""
        async with await self.connect() as conn:
            await check_role_at_start(conn, self.settings.librarian_role)
            await conn.commit()
        in_flight: set[asyncio.Task[None]] = set()
        try:
            while not stop.is_set():
                leased = 0
                try:
                    await self.maybe_sweep()
                    await self.maybe_expire()
                    await self.maybe_release()
                    while len(in_flight) < self.concurrency and not stop.is_set():
                        job = await self.lease_one()
                        if job is None:
                            break
                        in_flight.add(asyncio.create_task(self._guarded(job), name=f"librarian-{job.job_id}"))
                        leased += 1
                    await self.heartbeat()
                except Exception as exc:  # noqa: BLE001 - database hiccups: retry after a pause
                    log.error("librarian loop error: %s: %s", type(exc).__name__, exc)
                if in_flight and len(in_flight) >= self.concurrency:
                    # every slot busy: wake when one frees (or after a poll interval, for housekeeping)
                    _done, in_flight = await asyncio.wait(
                        in_flight, timeout=self.settings.librarian_poll_s, return_when=asyncio.FIRST_COMPLETED
                    )
                elif leased == 0:
                    waiters: set[asyncio.Future[Any]] = {asyncio.ensure_future(stop.wait()), *in_flight}
                    await asyncio.wait(
                        waiters, timeout=self.settings.librarian_poll_s, return_when=asyncio.FIRST_COMPLETED
                    )
                    for w in waiters:
                        if w not in in_flight and not w.done():
                            w.cancel()
                    in_flight = {t for t in in_flight if not t.done()}
                else:
                    in_flight = {t for t in in_flight if not t.done()}
        finally:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)


_HEARTBEAT_SQL = """
    SELECT count(*) FILTER (WHERE ready)::bigint,
           COALESCE(EXTRACT(EPOCH FROM (now() - min(run_after) FILTER (WHERE ready))), 0)::float8,
           count(*) FILTER (WHERE status = 'running' AND lease_until >= now())::bigint,
           count(*) FILTER (WHERE status = 'failed' AND run_after >= now() - interval '24 hours')::bigint
      FROM (SELECT status, run_after, lease_until,
                   ((status = 'queued' AND run_after <= now())
                    OR (status = 'running' AND lease_until < now())) AS ready
              FROM jobs WHERE kind = ANY(%s)) j
"""


async def heartbeat_fields(conn: AsyncConnection, configured_role: str) -> dict[str, Any]:
    cur = await conn.execute(_HEARTBEAT_SQL, (list(LIBRARIAN_JOB_KINDS),))
    ready, oldest, in_flight, failed = await cur.fetchone()
    spend = await budget_snapshot(conn)
    return {
        "ready": int(ready),
        "in_flight": int(in_flight),
        "oldest_ready_age_s": round(float(oldest), 3) if ready else None,
        "failed_24h": int(failed),
        **{k: round(v, 6) for k, v in spend.items()},
        "role": await effective_role(conn, configured_role, None),
    }


def emit_heartbeat(hb: dict[str, Any], path: Any) -> None:
    log.info("librarian heartbeat: %s", " ".join(f"{k}={v}" for k, v in hb.items()))
    if not path:
        return
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({**hb, "ts": time.time()}, fh)
        os.replace(tmp, path)
    except OSError as exc:  # pragma: no cover - read-only fs outside /tmp
        log.warning("librarian: heartbeat file not written: %s", exc)


# --------------------------------------------------------------------------- entrypoint
def redact_dsn(dsn: str) -> str:
    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001
        return "<unparsable dsn>"
    return (
        " ".join(f"{k}={parts[k]}" for k in ("host", "port", "dbname", "user") if parts.get(k))
        or "<default dsn>"
    )


@contextlib.asynccontextmanager
async def open_pool(dsn: str, *, concurrency: int = 1) -> AsyncIterator[Any]:
    """A small autocommit pool for the ledger and the reservations (own short transactions): one
    connection per job slot (a job has at most one provider call, hence one bookkeeping
    transaction, in flight); part of the ``check_connection_envelope`` budget."""
    from psycopg_pool import AsyncConnectionPool

    size = max(1, concurrency)  # one pooled bookkeeping connection per job slot (Sol 56 #5)
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=size, kwargs={"autocommit": True}, open=False)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _idle(settings: Any, connect: ConnFactory, stop: asyncio.Event, reason: str) -> None:
    log.warning("librarian: %s; idling (no job is leased, no provider is called)", reason)
    while not stop.is_set():
        try:
            async with await connect() as conn:
                hb = await heartbeat_fields(conn, settings.librarian_role)
                await conn.commit()
            hb.update(enabled=False, breaker_state="disabled", jobs_done=0, last_done_job=None)
            emit_heartbeat(hb, settings.librarian_heartbeat_file)
        except Exception as exc:  # noqa: BLE001
            log.error("librarian idle heartbeat failed: %s", exc)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.librarian_heartbeat_s)


async def _amain() -> int:
    from hlmemo.config import get_settings

    settings = get_settings()

    async def connect() -> AsyncConnection:
        c = await AsyncConnection.connect(settings.db_dsn, autocommit=False)
        await c.execute("SET TIME ZONE 'UTC'")
        await c.commit()
        return c

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    log.info(
        "librarian: %s, role=%s, mode=%s, profile=%s, fallback=%s, concurrency=%s",
        redact_dsn(settings.db_dsn),
        settings.librarian_role,
        settings.llm_mode,
        settings.profile,
        settings.fallback_profile,
        settings.librarian_concurrency,
    )
    if not settings.librarian_enabled:
        await _idle(settings, connect, stop, "HLM_LIBRARIAN_ENABLED is false")
        return 0
    try:
        check_connection_envelope(settings)
    except LibrarianConfigError as exc:
        log.error("librarian: refusing to start: %s", exc)
        return 2
    if settings.llm_mode == "off":
        await _idle(settings, connect, stop, "HLM_LLM_MODE=off")
        return 0
    async with open_pool(settings.db_dsn, concurrency=settings.librarian_concurrency) as pool:
        provider = Provider.from_settings(settings, conn=pool.connection)
        budget = None if settings.llm_budget_disabled else provider.budget
        worker = LibrarianWorker(
            settings,
            provider=provider,
            connect=connect,
            budget=budget if isinstance(budget, DbBudget) else None,
        )
        try:
            await worker.run_forever(stop)
        except RoleNotAuthorized as exc:
            log.error("librarian: refusing to start: %s", exc)
            return 2
        finally:
            await provider.aclose()
    log.info("librarian: stopped")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request lines carry no secrets, but stay quiet
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "HANDLED_KINDS",
    "SYSTEMIC_HANDBACK_CODES",
    "LibrarianConfigError",
    "LibrarianWorker",
    "check_connection_envelope",
    "default_handlers",
    "emit_heartbeat",
    "heartbeat_fields",
    "main",
]
