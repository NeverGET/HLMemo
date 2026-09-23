"""Librarian job enqueue and CC-3 capability sets.

A job carries the capability set of its **triggering device**, computed at enqueue from that
device's current grants, plus the device id. The set is rechecked at apply time inside the apply
transaction (``actor.recheck``): enqueue-time capabilities are an upper bound, never authority.

Jobs are projections of events (replay re-creates them): every enqueue happens inside an
event-producing transaction and the job descriptor (kind, dedupe key, priority, full payload) is
recorded in that event's ``payload.resolved.jobs``; ``run_after``/``created_at`` are the event's
``recorded_at`` so a rebuild reproduces the row byte for byte.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from hlmemo.core.temporal import fmt_ts
from hlmemo.db import auth_queries as aq
from hlmemo.db import write_queries as q
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event
from hlmemo.librarian.reserved import reserved_ids

LIBRARIAN_JOB_KINDS = (
    "librarian_write",
    "topic_summary",
    "consolidate",
    "ingest_extract",
    "import_postprocess",
    "pack_review",
    "experience_review",
)
DEFAULT_PRIORITY = 5


async def compute_capabilities(
    conn: AsyncConnection, trigger_device_id: int, project_ids: list[int]
) -> dict[str, Any]:
    """CC-3 capability set of ``trigger_device_id`` NOW (enqueue time).

    ``annotate``/``correct``: projects where the device holds ``write``; ``question``: projects it
    can read; ``librarian_memory``: always (the reserved memory project only);
    ``global_experience``: never at enqueue (only an accepted ``promote`` answer grants it).
    The admin device bypasses grants, so its sets are the job's own projects.
    """
    dev = await aq.select_device(conn, trigger_device_id)
    if dev is None or dev["status"] != "trusted":
        return {
            "trigger_device_id": trigger_device_id,
            "annotate": [],
            "correct": [],
            "question": [],
            "librarian_memory": True,
            "global_experience": False,
        }
    if dev["is_admin"]:
        write = read = sorted(set(project_ids))
    else:
        grants = await aq.select_active_grants(conn, trigger_device_id)
        write = sorted(pid for pid, role in grants if role in ("write", "admin"))
        read = sorted(pid for pid, _ in grants)
    return {
        "trigger_device_id": trigger_device_id,
        "annotate": write,
        "correct": write,
        "question": read,
        "librarian_memory": True,
        "global_experience": False,
    }


async def insert_job_row(
    conn: AsyncConnection,
    *,
    kind: str,
    dedupe_key: str,
    source_event_id: int,
    payload: dict[str, Any],
    priority: int,
    at: datetime,
    job_id: int | None = None,
) -> bool:
    if job_id is None:  # events recorded before job ids were (none in W2a's own paths)
        cur = await conn.execute(
            """
            INSERT INTO jobs (kind, dedupe_key, source_event_id, payload, run_after, created_at, priority)
            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING
            """,
            (kind, dedupe_key, source_event_id, Jsonb(payload), at, at, priority),
        )
    else:
        cur = await conn.execute(
            """
            INSERT INTO jobs (job_id, kind, dedupe_key, source_event_id, payload, run_after, created_at,
                              priority)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING
            """,
            (job_id, kind, dedupe_key, source_event_id, Jsonb(payload), at, at, priority),
        )
    return cur.rowcount == 1


async def assign_job_ids(conn: AsyncConnection, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allocate the job ids BEFORE the event is written, so the event records them and a replay
    re-creates the exact rows (Sol 37 #8: the full jobs projection, ids included)."""
    todo = [j for j in jobs if j.get("job_id") is None]
    for j, jid in zip(todo, await q.allocate_ids(conn, "jobs", len(todo)), strict=True):
        j["job_id"] = int(jid)
    return jobs


def job_spec(
    *, kind: str, dedupe_key: str, payload: dict[str, Any], priority: int = DEFAULT_PRIORITY
) -> dict[str, Any]:
    if kind not in LIBRARIAN_JOB_KINDS:
        raise ValueError(f"not a librarian job kind: {kind!r}")
    return {"kind": kind, "dedupe_key": dedupe_key, "priority": priority, "payload": payload}


async def insert_recorded_jobs(
    conn: AsyncConnection, jobs: list[dict[str, Any]], event_id: int, at: datetime
) -> int:
    """Insert the job descriptors recorded in an event (live path and replay share this)."""
    n = 0
    for j in jobs:
        n += await insert_job_row(
            conn,
            kind=j["kind"],
            dedupe_key=j["dedupe_key"],
            source_event_id=event_id,
            payload=j["payload"],
            priority=int(j.get("priority", DEFAULT_PRIORITY)),
            at=at,
            job_id=j.get("job_id"),
        )
    return n


async def enqueue(
    conn: AsyncConnection,
    *,
    project_id: int,
    trigger_device_id: int,
    specs: list[dict[str, Any]],
    client: str = CLIENT,
) -> int | None:
    """Enqueue librarian jobs through a ``librarian`` event (op ``enqueue``), in the caller's tx.

    Each spec's payload gains ``capabilities`` (computed now, CC-3) and ``project_id``. Returns the
    event id, or ``None`` when the same enqueue was already recorded (idempotent by dedupe keys).
    """
    ids = await reserved_ids(conn)
    jobs = []
    for s in specs:
        payload = dict(s["payload"])
        subject_projects = sorted({project_id, *payload.get("project_ids", [])})
        payload["project_id"] = project_id
        payload.setdefault("lineage", str(uuid.uuid5(NS_LIBRARIAN, "lineage:" + s["dedupe_key"])))
        payload["capabilities"] = await compute_capabilities(conn, trigger_device_id, subject_projects)
        jobs.append({**s, "payload": payload})
    await assign_job_ids(conn, jobs)
    keys = sorted(j["dedupe_key"] for j in jobs)
    at = await q.clock_now(conn)
    request = {"actor": CLIENT, "op": "enqueue", "trigger_device_id": trigger_device_id, "dedupe_keys": keys}
    event_id = await insert_system_event(
        conn,
        kind="librarian",
        project_id=project_id,
        device_id=ids.librarian_device_id,
        client=client,
        request_id=uuid.uuid5(NS_LIBRARIAN, "enqueue:" + "\n".join(keys)),
        request=request,
        resolved={"recorded_at": fmt_ts(at), "jobs": jobs},
        at=at,
    )
    if event_id is None:
        return None
    await insert_recorded_jobs(conn, jobs, event_id, at)
    return event_id


__all__ = [
    "DEFAULT_PRIORITY",
    "LIBRARIAN_JOB_KINDS",
    "assign_job_ids",
    "compute_capabilities",
    "enqueue",
    "insert_job_row",
    "insert_recorded_jobs",
    "job_spec",
]
