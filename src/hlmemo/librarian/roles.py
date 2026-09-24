"""The librarian role ladder (PHASE2-4-ROADMAP §4b, D-058; gate G-L8).

``observer`` (default) writes proposals only. ``assistant`` applies proposals only per batch after
an owner approval (``answer`` events). ``autonomous`` applies the W2b auto-rule class without
per-batch approval. The deployment role is ``HLM_LIBRARIAN_ROLE``; a role above ``observer`` is
honoured only while the latest deployment-level role decision event (``librarian`` event, op
``set_role``, recorded by the owner's device) names exactly that role: the worker refuses to start
otherwise, and a later decision demotes a running worker at its next job. A per-project override
(decision event with a project, or ``projects.policy.librarian_role``) can only be lower.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import write_queries as q
from hlmemo.librarian.actor import apply_batch_changes, set_question_status
from hlmemo.librarian.errors import RoleNotAuthorized
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event
from hlmemo.librarian.jobs import assign_job_ids, insert_recorded_jobs, job_spec

ROLES = ("observer", "assistant", "autonomous")
_RANK = {r: i for i, r in enumerate(ROLES)}


def lower(a: str, b: str | None) -> str:
    if b not in _RANK:
        return a
    return a if _RANK[a] <= _RANK[b] else b


async def lock_role_order(conn: AsyncConnection, *, exclusive: bool) -> None:
    """Serialize role decisions with librarian applies (Sol 44 #4): every apply holds the SHARED
    lock while it reads the role and commits; a role decision takes it EXCLUSIVE. A demotion that
    commits first is seen by the next apply; an apply in flight commits before the demotion."""
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await conn.execute(f"SELECT {fn}(4, 0)")


async def latest_role_decision(conn: AsyncConnection, project_id: int | None = None) -> str | None:
    scope = "project_id IS NULL" if project_id is None else "project_id = %s"
    cur = await conn.execute(
        f"""
        SELECT payload->'request'->>'role' FROM events
         WHERE kind = 'librarian' AND payload->'request'->>'op' = 'set_role' AND {scope}
         ORDER BY event_id DESC LIMIT 1
        """,
        () if project_id is None else (project_id,),
    )
    row = await cur.fetchone()
    return None if row is None else row[0]


async def record_role_decision(
    conn: AsyncConnection,
    *,
    role: str,
    decided_by: AuthContext,
    decision: str,
    project_id: int | None = None,
) -> int:
    """Record an owner role decision (``hlm.ops librarian role …``; a D-entry is the ``decision``).

    D-074: a promotion (assistant+) releases the ``accepted_pending`` answers recorded under
    observer to the normal batch path: the same event enqueues one ``apply_batch`` job per batch
    holding such questions whose project is assistant+ after this decision (``resolved.jobs``,
    replayed as-is). The job applies them with the full recheck (TTL, staleness, capabilities)."""
    if role not in _RANK:
        raise ToolError("E_INVALID_ARG", f"unknown librarian role {role!r}")
    if project_id is None and not decided_by.is_admin:
        raise ToolError("E_FORBIDDEN", "the deployment role is set by the operator (admin device) only")
    if project_id is not None and not decided_by.has(project_id, Role.ADMIN):
        raise ToolError("E_FORBIDDEN_PROJECT", "project role override needs admin on the project")
    await lock_role_order(conn, exclusive=True)
    at = await q.clock_now(conn)
    jobs = await pending_apply_jobs(conn, role, project_id)
    await assign_job_ids(conn, jobs)
    resolved: dict[str, Any] = {"recorded_at": fmt_ts(at)}
    if jobs:
        resolved["jobs"] = jobs
    event_id = await insert_system_event(
        conn,
        kind="librarian",
        project_id=project_id,
        device_id=decided_by.device_id,
        client=decided_by.client,
        request_id=uuid.uuid4(),
        request={"actor": CLIENT, "op": "set_role", "role": role, "decision": decision[:200]},
        resolved=resolved,
        at=at,
    )
    assert event_id is not None
    if jobs:
        await insert_recorded_jobs(conn, jobs, event_id, at)
    return event_id


async def _role_after(conn: AsyncConnection, role: str, decision_project: int | None, pid: int) -> str:
    """The effective role of ``pid`` once this decision is recorded (``effective_role`` with the
    decision as the configured role; the worker still needs a matching ``HLM_LIBRARIAN_ROLE``)."""
    deployment = role if decision_project is None else (await latest_role_decision(conn, None) or "observer")
    project = role if decision_project == pid else await latest_role_decision(conn, pid)
    cur = await conn.execute("SELECT policy->>'librarian_role' FROM projects WHERE project_id = %s", (pid,))
    row = await cur.fetchone()
    return lower(lower(deployment, project), row[0] if row else None)


async def pending_apply_jobs(
    conn: AsyncConnection, role: str, decision_project: int | None
) -> list[dict[str, Any]]:
    """D-074 promotion: one ``apply_batch`` job per batch holding ``accepted_pending`` questions
    (of ``decision_project`` only, for a project decision) whose project is assistant+ after the
    decision and which has no apply job queued or running (that one picks them up)."""
    if role == "observer":
        return []
    cur = await conn.execute(
        """
        SELECT DISTINCT ON (lq.batch_id) lq.batch_id::text, lq.project_id, lq.proposal->'capabilities'
          FROM librarian_questions lq
         WHERE lq.status = 'accepted_pending' AND lq.batch_id IS NOT NULL
           AND (%(p)s::bigint IS NULL OR lq.project_id = %(p)s)
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'librarian_write'
                            AND j.payload->>'op' = 'apply_batch'
                            AND j.payload->>'batch_id' = lq.batch_id::text
                            AND j.status IN ('queued', 'running'))
         ORDER BY lq.batch_id, lq.question_id
        """,
        {"p": decision_project},
    )
    jobs = []
    for batch_id, pid, caps in await cur.fetchall():
        if await _role_after(conn, role, decision_project, int(pid)) == "observer":
            continue
        n = await decision_round(conn, batch_id)
        jobs.append(apply_job(batch_id, int(pid), caps or {}, f":r{n}" if n else ""))
    return jobs


def apply_job(batch_id: str, project_id: int, capabilities: dict[str, Any], rnd: str) -> dict[str, Any]:
    """The ``apply_batch`` job of one decision round of ``batch_id`` (round 0 keeps the W2a key)."""
    key = f"librarian_apply:{batch_id}{rnd}"
    return job_spec(
        kind="librarian_write",
        dedupe_key=key,
        priority=4,
        payload={
            "op": "apply_batch",
            "batch_id": batch_id,
            "project_id": project_id,
            "capabilities": capabilities,
            "lineage": str(uuid.uuid5(NS_LIBRARIAN, f"lineage:{key}")),
        },
    )


async def check_role_at_start(conn: AsyncConnection, configured: str) -> None:
    """``assistant``/``autonomous`` refuse to start without a matching decision event (G-L8)."""
    if configured == "observer":
        return
    decided = await latest_role_decision(conn, None)
    if decided != configured:
        raise RoleNotAuthorized(
            f"HLM_LIBRARIAN_ROLE={configured} but the latest librarian role decision is {decided or 'none'}"
        )


async def effective_role(conn: AsyncConnection, configured: str, project_id: int | None) -> str:
    role = configured
    if role != "observer" and await latest_role_decision(conn, None) != role:
        role = "observer"  # demoted since start (or never authorized)
    if project_id is not None:
        role = lower(role, await latest_role_decision(conn, project_id))
        cur = await conn.execute(
            "SELECT policy->>'librarian_role' FROM projects WHERE project_id = %s", (project_id,)
        )
        row = await cur.fetchone()
        role = lower(role, row[0] if row else None)
    return role


# --------------------------------------------------------------------------- batch approval
async def batch_questions(
    conn: AsyncConnection, batch_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    """The question rows of ``batch_id`` (optionally only one status), locked for update. An open
    question past its ``expires_at`` is never returned as open: it cannot be approved (Sol 44 #2)."""
    cur = await conn.execute(
        """
        SELECT question_id::text, project_id, project_ids, status, proposal FROM librarian_questions
         WHERE batch_id = %s AND (%s::text IS NULL OR status = %s)
           AND NOT (status = 'open' AND expires_at IS NOT NULL AND expires_at <= clock_timestamp())
         ORDER BY question_id FOR UPDATE
        """,
        (batch_id, status, status),
    )
    keys = ("question_id", "project_id", "project_ids", "status", "proposal")
    return [dict(zip(keys, r, strict=True)) for r in await cur.fetchall()]


async def decision_round(conn: AsyncConnection, batch_id: str) -> int:
    """How many decisions of ``batch_id`` already reached an ``apply_batch`` job (each records one
    ``librarian`` event). An observer hand-back (``role_denied``) reopens the questions; the next
    decision is round n+1, with its own answer ``request_id`` and apply job key (Sol 46 #7)."""
    cur = await conn.execute(
        "SELECT count(*) FROM events WHERE kind = 'librarian'"
        " AND payload->'request'->>'op' = 'apply_batch' AND payload->'request'->>'batch_id' = %s",
        (batch_id,),
    )
    return int((await cur.fetchone())[0])


async def record_batch_decision(
    conn: AsyncConnection,
    *,
    batch_id: str,
    approver: AuthContext,
    decision: str,
    except_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Owner decision on the OPEN questions of a batch: one ``answer`` event per question (CC-3).

    ``accept`` approves every open question except ``except_ids`` (which are rejected); ``reject``
    rejects all. The approver must hold ``write`` on every project any question touches, else
    ``E_FORBIDDEN_PROJECT``; a batch without open questions is ``E_NOT_FOUND``. Each answer event
    records ``resolved.question_status`` (the row update, replayed as-is); when anything is
    approved, the last one enqueues the batch's ``apply_batch`` job (``resolved.jobs``).
    """
    if decision not in ("accept", "reject"):
        raise ToolError("E_INVALID_ARG", "decision must be accept or reject")
    rows = await batch_questions(conn, batch_id, "open")
    if not rows:
        raise ToolError("E_NOT_FOUND", "batch not found")
    project_id = int(rows[0]["project_id"])
    touched = {int(x) for r in rows for x in r["project_ids"]} | {project_id}
    if not all(approver.has(pid, Role.WRITE) for pid in touched):
        raise ToolError("E_FORBIDDEN_PROJECT", "approval needs write on every project the batch touches")
    excepted = set(except_ids or [])
    unknown = excepted - {r["question_id"] for r in rows}
    if unknown:
        raise ToolError("E_INVALID_ARG", f"unknown question ids {sorted(unknown)}")
    at = await q.clock_now(conn)
    n = await decision_round(conn, batch_id)
    rnd = f":r{n}" if n else ""  # round 0 keeps the W2a keys
    decided: dict[str, str] = {}
    approved = 0
    for i, r in enumerate(rows):
        status = "rejected" if decision == "reject" or r["question_id"] in excepted else "approved"
        decided[r["question_id"]] = status
        approved += status == "approved"
        jobs = []
        if i == len(rows) - 1 and approved:
            jobs = [apply_job(batch_id, project_id, rows[0]["proposal"].get("capabilities") or {}, rnd)]
        await assign_job_ids(conn, jobs)
        last = i == len(rows) - 1
        batches = (
            [{"batch_id": batch_id, "status": "decided", "decided_by": approver.device_id}] if last else []
        )
        change = {"question_id": r["question_id"], "status": status, "decided_by": approver.device_id}
        event_id = await insert_system_event(
            conn,
            kind="answer",
            project_id=project_id,
            device_id=approver.device_id,
            client=approver.client,
            request_id=uuid.uuid5(
                NS_LIBRARIAN, f"answer:{r['question_id']}:{approver.device_id}:{status}{rnd}"
            ),
            request={
                "op": "batch_decision",
                "batch_id": batch_id,
                "question_id": r["question_id"],
                "decision": status,
            },
            resolved={
                "recorded_at": fmt_ts(at),
                "question_status": [change],
                "jobs": jobs,
                "batches": batches,
            },
            at=at,
        )
        if event_id is None:
            continue
        await set_question_status(conn, [change], at)
        await apply_batch_changes(conn, batches, event_id, at)
        if jobs:
            await insert_recorded_jobs(conn, jobs, event_id, at)
    return {
        "batch_id": batch_id,
        "accepted": approved,
        "rejected": len(rows) - approved,
        "decisions": decided,
    }


__all__ = [
    "ROLES",
    "batch_questions",
    "apply_job",
    "check_role_at_start",
    "decision_round",
    "effective_role",
    "latest_role_decision",
    "lower",
    "pending_apply_jobs",
    "record_batch_decision",
    "record_role_decision",
]
