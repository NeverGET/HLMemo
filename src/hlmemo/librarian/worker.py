"""The librarian service: ``python -m hlmemo.librarian.worker`` (PHASE2-4-ROADMAP §2, W2a).

Loop: lease one ready ``librarian_write`` job (``worker/lease.py``: ``(priority, run_after)``,
lease renewed every 30 s), let its handler plan it (reads committed, provider calls outside any
transaction), then apply the plan in ONE transaction:

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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from hlmemo.core.budget import Meter
from hlmemo.db import write_queries as q
from hlmemo.librarian import actor
from hlmemo.librarian.budget import DbBudget, budget_snapshot
from hlmemo.librarian.errors import (
    AuthorityLost,
    BudgetDeferred,
    JobCallCapExceeded,
    LlmDisabled,
    ProviderUnavailable,
    RoleNotAuthorized,
)
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event
from hlmemo.librarian.jobs import LIBRARIAN_JOB_KINDS, insert_recorded_jobs
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import REDACTION_VERSION
from hlmemo.librarian.reserved import reserved_ids
from hlmemo.librarian.roles import check_role_at_start, effective_role
from hlmemo.librarian.tasks import Handler, Plan
from hlmemo.librarian.tasks.apply_batch import ApplyBatch
from hlmemo.librarian.tasks.pair_check import PairCheck
from hlmemo.worker.lease import LeasedJob, keep_lease, lease_jobs, mark_done, mark_failed, release

log = logging.getLogger("hlmemo.librarian")

HANDLED_KINDS = ("librarian_write",)
BUDGET_PAUSE_S = 60.0
SWEEP_EVERY_S = 60.0

ConnFactory = Callable[[], Awaitable[AsyncConnection]]


def default_handlers() -> dict[str, Handler]:
    return {PairCheck.op: PairCheck(), ApplyBatch.op: ApplyBatch()}


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
        self.connect = connect
        self.handlers = handlers or default_handlers()
        self.budget = budget
        self.meter = Meter()
        self.stats = Stats()
        self.paused_until = 0.0
        self.pause_reason: str | None = None
        self._last_heartbeat = 0.0
        self._last_sweep = 0.0

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
    async def run_once(self) -> int:
        if self.paused:
            return 0
        async with await self.connect() as conn:
            jobs = await lease_jobs(conn, HANDLED_KINDS, 1, lease_seconds=self.settings.librarian_lease_s)
        for job in jobs:
            await self.process(job)
        return len(jobs)

    async def drain(self, *, max_jobs: int | None = None) -> int:
        """Process ready jobs until none is left (tests)."""
        n = 0
        while max_jobs is None or n < max_jobs:
            if await self.run_once() == 0:
                break
            n += 1
        return n

    async def process(self, job: LeasedJob) -> None:
        handler = self.handlers.get(str(job.payload.get("op")))
        conn = await self.connect()
        await conn.commit()  # never sit "idle in transaction" through a long provider call
        try:
            if handler is None:
                await mark_failed(
                    conn, job, f"unknown librarian op {job.payload.get('op')!r}", max_attempts=1
                )
                self.stats.jobs_failed += 1
                return
            async with keep_lease(
                self.connect,
                job,
                lease_seconds=self.settings.librarian_lease_s,
                every_s=self.settings.librarian_lease_renew_s,
            ) as lost:
                try:
                    plan = await handler.plan(self, job)
                    if lost.is_set():
                        raise _LeaseLost
                    await self.apply(conn, job, plan)
                    self.stats.jobs_done += 1
                    self.stats.last_done_job = job.job_id
                except _Duplicate:
                    await conn.rollback()
                    async with conn.transaction():
                        await mark_done(conn, job)
                    await conn.commit()
                except _LeaseLost:
                    await conn.rollback()
                    log.warning("job %s: lease lost before commit; nothing applied", job.job_id)
                except BudgetDeferred as exc:
                    await conn.rollback()
                    await release(conn, job, delay_s=BUDGET_PAUSE_S, reason=f"budget: {exc}")
                    self.stats.jobs_released += 1
                    self.pause("budget", BUDGET_PAUSE_S)
                except JobCallCapExceeded as exc:
                    await conn.rollback()
                    await mark_failed(conn, job, f"call cap: {exc}", max_attempts=1)
                    self.stats.jobs_failed += 1
                    self.pause("budget", BUDGET_PAUSE_S)
                except ProviderUnavailable as exc:
                    await conn.rollback()
                    await release(conn, job, delay_s=max(1.0, exc.retry_after_s), reason=f"provider: {exc}")
                    self.stats.jobs_released += 1
                except LlmDisabled as exc:
                    await conn.rollback()
                    await release(conn, job, delay_s=30.0, reason=str(exc))
                    self.stats.jobs_released += 1
                except Exception as exc:  # noqa: BLE001 - one bad job must not stop the queue
                    await conn.rollback()
                    status = await mark_failed(conn, job, f"{type(exc).__name__}: {exc}")
                    if status == "failed":
                        self.stats.jobs_failed += 1
                    log.warning(
                        "job %s attempt %s: %s: %s", job.job_id, job.attempts, type(exc).__name__, exc
                    )
        finally:
            with contextlib.suppress(Exception):
                await conn.close()

    # ------------------------------------------------------------------ apply
    async def apply(self, conn: AsyncConnection, job: LeasedJob, plan: Plan) -> None:
        caps = plan.capabilities
        project_id = job.payload.get("project_id")
        async with conn.transaction():
            T = await q.clock_now(conn)
            role = await effective_role(conn, self.settings.librarian_role, project_id)
            ids = await reserved_ids(conn)
            (event_id,) = await q.allocate_ids(conn, "events", 1)
            outcome = plan.outcome
            applied: list[dict[str, Any]] = []
            proposals: list[dict[str, Any]] = []
            detail: str | None = None
            if plan.mutations and outcome in ("proposed", "approved"):
                try:
                    ctx = await actor.recheck(conn, caps, CLIENT)
                    if plan.op == "apply_batch":
                        if role == "observer":
                            outcome = "role_denied"
                        else:
                            applied = await actor.materialize_links(conn, ctx, caps, plan.mutations)
                            outcome = "applied"
                    else:
                        auto = role == "autonomous"
                        direct = [
                            m for m, ok in zip(plan.mutations, plan.auto_ok, strict=True) if auto and ok
                        ]
                        applied = await actor.materialize_links(conn, ctx, caps, direct)
                        for i, (m, ok) in enumerate(zip(plan.mutations, plan.auto_ok, strict=True)):
                            if auto and ok:
                                continue
                            await actor.check_link(conn, ctx, caps, m)  # annotate(P) must hold to propose
                            if not actor.allowed(
                                ctx, caps, "question", [int(x) for x in m.get("dst_project_ids", [])]
                            ):
                                raise AuthorityLost("question(P) not held on a proposal subject")
                            proposals.append(
                                {
                                    "proposal_id": str(uuid.uuid5(NS_LIBRARIAN, f"{job.dedupe_key}#{i}")),
                                    "kind": "link",
                                    "capability": "annotate",
                                    "mutation": m,
                                    "capabilities": caps,
                                    **plan.meta[i],
                                }
                            )
                        outcome = "proposed" if proposals else ("applied" if applied else "no_change")
                except AuthorityLost as exc:
                    outcome, applied, proposals, detail = "authority_lost", [], [], str(exc)[:200]
            batch_id = str(uuid.uuid5(NS_LIBRARIAN, f"batch:{job.dedupe_key}")) if proposals else None
            request = {
                "audit": "llm/1",
                "op": plan.op,
                "job_id": job.job_id,
                "dedupe_key": job.dedupe_key,
                "capabilities": caps,
                "redaction_version": REDACTION_VERSION,
                "calls": plan.calls,
                **plan.request_extra,
            }
            resolved: dict[str, Any] = {
                "recorded_at": actor.ts(T),
                "outcome": outcome,
                "role": role,
                "mutations": applied,
                "proposals": proposals,
                "batch_id": batch_id,
                "done_job": job.dedupe_key,
                "jobs": plan.jobs,
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
                request=request,
                resolved=resolved,
                at=T,
                event_id=event_id,
            )
            if inserted is None:
                raise _Duplicate(job.dedupe_key)
            await actor.apply_mutations(conn, applied, event_id, T)
            if plan.jobs:
                await insert_recorded_jobs(conn, plan.jobs, event_id, T)
            if not await mark_done(conn, job):
                raise _LeaseLost
        await conn.commit()
        log.info(
            "job %s (%s): %s, %s applied, %s proposed",
            job.job_id,
            plan.op,
            outcome,
            len(applied),
            len(proposals),
        )

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

    async def run_forever(self, stop: asyncio.Event) -> None:
        async with await self.connect() as conn:
            await check_role_at_start(conn, self.settings.librarian_role)
            await conn.commit()
        while not stop.is_set():
            leased = 0
            try:
                await self.maybe_sweep()
                leased = await self.run_once()
                await self.heartbeat()
            except Exception as exc:  # noqa: BLE001 - database hiccups: retry after a pause
                log.error("librarian loop error: %s: %s", type(exc).__name__, exc)
            if leased == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.librarian_poll_s)


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
async def open_pool(dsn: str) -> AsyncIterator[Any]:
    """A small autocommit pool for the ledger and the reservations (own short transactions)."""
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(dsn, min_size=1, max_size=4, kwargs={"autocommit": True}, open=False)
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
        "librarian: %s, role=%s, mode=%s, profile=%s, fallback=%s",
        redact_dsn(settings.db_dsn),
        settings.librarian_role,
        settings.llm_mode,
        settings.profile,
        settings.fallback_profile,
    )
    if not settings.librarian_enabled:
        await _idle(settings, connect, stop, "HLM_LIBRARIAN_ENABLED is false")
        return 0
    if settings.llm_mode == "off":
        await _idle(settings, connect, stop, "HLM_LLM_MODE=off")
        return 0
    async with open_pool(settings.db_dsn) as pool:
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
    "LibrarianWorker",
    "default_handlers",
    "emit_heartbeat",
    "heartbeat_fields",
    "main",
]
