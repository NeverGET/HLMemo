"""Shared helpers for the librarian gates (G-L1..G-L8, G-L3 on the retrieval clone).

* ``ScriptedLLM``: an in-process OpenAI-compatible stub (``httpx.MockTransport``) that records every
  request body and answers from a script (JSON objects, raw text, HTTP statuses, stalls).
* ``FakeClock``: records the provider's backoff sleeps and advances a fake monotonic clock.
* ``stub_chain``: two priced stub profiles (primary + fallback) — no vendor anywhere.
* ``lib_settings``: ``Settings`` for tests with small leases, no heartbeat file.
* ``make_provider`` / ``make_worker``: DB ledger + DB (or no) budget against the test DSN.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import psycopg

from hlmemo.config import Settings
from hlmemo.librarian.budget import Caps, DbBudget, NoBudget
from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.provider import Clock, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.reserved import ReservedIds, ensure_reserved_rows
from hlmemo.librarian.worker import LibrarianWorker

PRIMARY = "stub-primary"
FALLBACK = "stub-fallback"


def stub_profile(name: str, *, price_in: str = "1.0", price_out: str = "2.0") -> LlmProfile:
    return LlmProfile(
        name=name,
        base_url=f"http://{name}.invalid/v1",
        model_id=f"stub/{name}",
        api_key="test-key-not-secret",
        reasoning=None,
        extra={"response_format": {"type": "json_object"}, "temperature": 0},
        price_in_per_m=Decimal(price_in),
        price_out_per_m=Decimal(price_out),
        supports_json_schema=False,
        prompt_overrides={},
    )


def stub_chain(*, fallback: bool = True) -> list[LlmProfile]:
    return [stub_profile(PRIMARY)] + ([stub_profile(FALLBACK)] if fallback else [])


def chat(
    content: Any, *, cost: float | None = None, prompt_tokens: int = 100, completion_tokens: int = 20
) -> dict:
    text = content if isinstance(content, str) else json.dumps(content)
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    return {
        "model": "stub",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": usage,
    }


CONTRADICTS_B = {"contradicts": True, "supersedes": "B", "reason": "B is later and replaces A"}
NO_CONTRADICTION = {"contradicts": False, "supersedes": "none", "reason": "compatible"}


@dataclass
class ScriptedLLM:
    """Answers per request from ``script`` (a list consumed in order, then ``default``).

    A script entry is a dict (JSON answer), a str (raw content), an int (HTTP status), a
    ``("stall", seconds, entry)`` tuple, or a callable ``(request_json) -> entry``.
    """

    script: list[Any] = field(default_factory=list)
    default: Any = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    on_request: Callable[[dict[str, Any]], Any] | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.hosts.append(request.url.host)
        if self.on_request is not None:
            res = self.on_request(body)
            if asyncio.iscoroutine(res):
                await res
        entry = self.script.pop(0) if self.script else self.default
        while True:
            if callable(entry):
                entry = entry(body)
                continue
            if isinstance(entry, tuple) and entry and entry[0] == "stall":
                await asyncio.sleep(entry[1])
                entry = entry[2]
                continue
            break
        if entry == "connect_error":
            raise httpx.ConnectError("scripted transport failure")
        if isinstance(entry, int):
            return httpx.Response(entry, json={"error": {"code": entry, "message": "scripted"}})
        if isinstance(entry, dict) and "choices" in entry:
            return httpx.Response(200, json=entry)
        return httpx.Response(200, json=chat(entry))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    @property
    def calls(self) -> int:
        return len(self.requests)


class FakeClock(Clock):
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def monotonic(self) -> float:
        return self.t


def lib_settings(db_dsn: str, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "db_dsn": db_dsn,
        "librarian_enabled": True,
        "librarian_lease_s": 30,
        "librarian_lease_renew_s": 5.0,
        "librarian_poll_s": 0.05,
        "librarian_heartbeat_s": 3600.0,
        "librarian_heartbeat_file": None,
        "llm_mode": "live",
        "llm_budget_disabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def conn_ctx(db_dsn: str) -> Callable[[], contextlib.AbstractAsyncContextManager[psycopg.AsyncConnection]]:
    @contextlib.asynccontextmanager
    async def _ctx() -> AsyncIterator[psycopg.AsyncConnection]:
        conn = await psycopg.AsyncConnection.connect(db_dsn, autocommit=True)
        try:
            yield conn
        finally:
            await conn.close()

    return _ctx


def make_provider(
    db_dsn: str,
    llm: ScriptedLLM,
    *,
    chain: list[LlmProfile] | None = None,
    clock: Clock | None = None,
    caps: Caps | None = None,
    budget_disabled: bool = False,
    mode: str = "live",
    **kw: Any,
) -> Provider:
    ctx = conn_ctx(db_dsn)
    budget = (
        NoBudget()
        if budget_disabled
        else DbBudget(ctx, caps or Caps(Decimal(100), Decimal(100), Decimal(100)))
    )
    return Provider(
        chain or stub_chain(),
        mode=mode,
        budget=budget,
        ledger=DbLedger(ctx),
        transport=llm.transport,
        clock=clock or FakeClock(),
        redactor=Redactor(),
        budget_disabled=budget_disabled,
        **kw,
    )


def make_worker(settings: Settings, provider: Provider, connect: Any, **kw: Any) -> LibrarianWorker:
    return LibrarianWorker(settings, provider=provider, connect=connect, **kw)


async def seed_reserved(conn: psycopg.AsyncConnection) -> ReservedIds:
    ids = await ensure_reserved_rows(conn)
    await conn.commit()
    return ids


async def enqueue_pair(
    conn: psycopg.AsyncConnection,
    *,
    project_id: int,
    trigger_device_id: int,
    subject_vid: int,
    candidate_vids: list[int],
    key: str,
    priority: int = 5,
) -> int | None:
    spec = job_spec(
        kind="librarian_write",
        dedupe_key=f"librarian_write:{key}",
        priority=priority,
        payload={"op": "pair_check", "version_id": subject_vid, "candidates": candidate_vids},
    )
    return await enqueue(conn, project_id=project_id, trigger_device_id=trigger_device_id, specs=[spec])


async def outcomes(conn: psycopg.AsyncConnection) -> list[str]:
    cur = await conn.execute(
        "SELECT payload->'resolved'->>'outcome' FROM events WHERE kind = 'librarian'"
        " AND payload->'request'->>'audit' = 'llm/1' ORDER BY event_id"
    )
    return [r[0] for r in await cur.fetchall()]


async def dump_full_jobs_and_questions(conn: psycopg.AsyncConnection) -> dict[str, list[str]]:
    """The FULL jobs projection (Sol 37 #8) and the question rows, for replay equality.

    Librarian-era jobs are compared on EVERY column (``SELECT j.*``: job_id, lease columns,
    last_error, attempts, run_after, done_at, created_at …): their ids, completion time, attempts
    and final run_after are recorded in events — except the two scheduling hints of a job that is
    still QUEUED (``run_after``, ``last_error``): a systemic hand-back changes only those and
    writes no event (Sol 56 #4; the attempts are compared). Phase-0 embed jobs are written by the
    pre-existing write path, which records neither ids nor creation time, so for those rows
    ``job_id`` and ``created_at`` are masked (unchanged Phase-0 behaviour; not a W2a projection).
    """
    cur = await conn.execute(
        """
        SELECT CASE WHEN kind IN ('embed', 'reembed')
                    THEN (NULL::bigint, kind, dedupe_key, payload::text, source_event_id, status, attempts,
                          priority, run_after, done_at, lease_token, lease_until, last_error,
                          NULL::timestamptz)::text
                    WHEN status = 'queued'
                    THEN (job_id, kind, dedupe_key, payload::text, source_event_id, status, attempts,
                          priority, NULL::timestamptz, done_at, lease_token, lease_until, NULL::text,
                          created_at)::text
                    ELSE j::text END
          FROM jobs j ORDER BY dedupe_key
        """
    )
    jobs = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute("SELECT t::text FROM librarian_questions t ORDER BY question_id")
    return {"jobs": jobs, "librarian_questions": [r[0] for r in await cur.fetchall()]}


async def ledger_rows(conn: psycopg.AsyncConnection) -> list[tuple[str, str]]:
    cur = await conn.execute("SELECT profile, outcome FROM llm_calls ORDER BY created_at, call_id")
    return [(r[0], r[1]) for r in await cur.fetchall()]


class SlowHandler:
    """A job that holds its lease for ``sleep_s`` without any LLM call (G-L6)."""

    op = "slow"

    def __init__(self, sleep_s: float) -> None:
        self.sleep_s = sleep_s

    async def plan(self, w: Any, job: Any) -> Any:
        from hlmemo.librarian.tasks import Plan

        await asyncio.sleep(self.sleep_s)
        return Plan(self.op, "no_change", job.payload.get("capabilities") or {})


async def run_slow_worker(db_dsn: str, lease_s: str = "2", renew_s: str = "0.5") -> None:
    """Subprocess entry for the G-L6 SIGKILL test: lease one job and hang inside it."""

    async def connect() -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(db_dsn, autocommit=False)

    settings = lib_settings(db_dsn, librarian_lease_s=int(lease_s), librarian_lease_renew_s=float(renew_s))
    provider = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    worker = make_worker(settings, provider, connect, handlers={"slow": SlowHandler(600)})
    print("worker-started", flush=True)
    await worker.run_once()


async def add_new_kind_events(connect: Any, db_dsn: str, world: Any, deps: Any) -> int:
    """Append events of EVERY CC-2 kind through their live paths (G6 / G-L5); returns how many.

    librarian (enqueue, observer proposals, assistant apply) and answer (batch approval) go
    through the worker and the role/approval helpers; question, consolidation, pack_import, import,
    ingest (system-shaped) and device_minted through ``insert_system_event`` plus the same appliers
    replay uses. Two write events (the subject items) are appended too.
    """
    from datetime import UTC, datetime

    from hlmemo.core.temporal import fmt_ts
    from hlmemo.core.write_service import write
    from hlmemo.db import write_queries as q
    from hlmemo.librarian.actor import apply_mutations
    from hlmemo.librarian.events import insert_system_event
    from hlmemo.librarian.jobs import assign_job_ids, insert_recorded_jobs
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision
    from tests.integration._write_fixtures import MAIN, item, write_req

    async with await connect() as conn:
        cur = await conn.execute("SELECT count(*) FROM events")
        (n0,) = await cur.fetchone()
        ids = await ensure_reserved_rows(conn)
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Queue",
                        "Jobs retry 3 times.",
                        valid_from=datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
                    ),
                    item(
                        "Queue",
                        "Jobs retry 5 times now.",
                        valid_from=datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
                    ),
                ],
            ),
            deps=deps,
        )
        old, new = res.versions
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new.version_id,
            candidate_vids=[old.version_id],
            key="g6-kinds",
        )
        await conn.commit()
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    observer = make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect)
    assert await observer.drain() == 1
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'resolved'->>'batch_id' FROM events WHERE kind = 'librarian'"
            " AND payload->'resolved'->>'outcome' = 'proposed' ORDER BY event_id DESC LIMIT 1"
        )
        (batch_id,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch_id, approver=world.ctx_a, decision="accept")
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-g6")
        await conn.commit()
    assistant = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    assert await assistant.drain() == 1
    await provider.aclose()

    def link(rel: str, src: Any, dst: Any, pinned: bool = False) -> dict[str, Any]:
        return {
            "op": "link_insert",
            "rel": rel,
            "src_logical_id": src.logical_id,
            "dst_logical_id": dst.logical_id,
            "dst_version_id": dst.version_id if pinned else None,
            "project_id": world.main_id,
            "project_ids": [world.main_id],
            "device_scope": "all",
            "props": {"by": "g6"},
            "valid_from": fmt_ts(datetime(2026, 8, 1, tzinfo=UTC)),
            "valid_to": None,
        }

    consolidate_job = {
        "kind": "consolidate",
        "dedupe_key": "consolidate:g6",
        "priority": 7,
        "payload": {"op": "noop"},
    }
    synthetic: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = [
        ("question", [link("relates_to", old, new)], []),
        ("consolidation", [link("relates_to", new, old)], [consolidate_job]),
        ("pack_import", [link("depends_on", old, new)], []),
        ("import", [link("depends_on", new, old)], []),
        ("ingest", [link("derived_from", new, old, pinned=True)], []),
        ("device_minted", [], []),
    ]
    async with await connect() as conn:
        for kind, mutations, jobs in synthetic:
            T = await q.clock_now(conn)
            for m, lid in zip(mutations, await q.allocate_ids(conn, "links", len(mutations)), strict=True):
                m["link_id"] = lid
            (event_id,) = await q.allocate_ids(conn, "events", 1)
            await assign_job_ids(conn, jobs)  # ids recorded in the event (full projection)
            resolved: dict[str, Any] = {"recorded_at": fmt_ts(T), "mutations": mutations, "jobs": jobs}
            system = kind != "device_minted"
            if not system:
                resolved = {"device_id": world.dev_b}
            await insert_system_event(
                conn,
                kind=kind,
                project_id=world.main_id if system else None,
                device_id=ids.librarian_device_id if system else 1,
                client="pytest/0",
                request_id=uuid.uuid4(),
                request={"actor": "pytest", "op": kind},
                resolved=resolved,
                at=T,
                event_id=event_id,
            )
            if system:
                await apply_mutations(conn, mutations, event_id, T)
                await insert_recorded_jobs(conn, jobs, event_id, T)
        await conn.commit()
        cur = await conn.execute("SELECT count(*) FROM events")
        (n1,) = await cur.fetchone()
    return int(n1 - n0)
