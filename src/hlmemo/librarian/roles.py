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
from hlmemo.librarian.actor import action_projects, apply_batch_changes, set_question_status
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
    (event_id,) = await q.allocate_ids(conn, "events", 1)
    jobs = await pending_apply_jobs(conn, role, project_id, event_id)
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
        event_id=event_id,
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
    conn: AsyncConnection, role: str, decision_project: int | None, event_id: int
) -> list[dict[str, Any]]:
    """D-074 promotion (Sol 49 #2, Sol 50, Sol 56 #1): the ``accepted_pending`` answers this
    decision can release — EVERY eligible one, not only those touching the decided project.

    A demotion (``observer``) releases nothing. Any other decision re-evaluates every pending
    answer (``releasable``) with the roles AFTER it: an answer whose CURRENT touched projects are
    all assistant+ gets an apply job, whichever project was decided (Sol 56: an answer whose
    action touched C and then no longer did would otherwise wait forever for a promotion of a
    project it no longer touches). Keyed by this event
    (``librarian_apply:<batch>:promo<event_id>``); recorded in ``resolved.jobs``."""
    if role == "observer":
        return []

    async def role_of(pid: int) -> str:
        return await _role_after(conn, role, decision_project, pid)

    return await releasable(conn, role_of, f":promo{event_id}")


async def releasable(
    conn: AsyncConnection,
    role_of: Any,
    suffix: str,
    logical_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """The ``apply_batch`` jobs that release eligible ``accepted_pending`` answers (the one rule
    behind a promotion, a project-scope revision and the sweeper, Sol 56 #1).

    A question is eligible when every project it touches NOW — its home, its recorded
    ``project_ids`` and the projects its actions touch on the CURRENT rows (``action_projects``) —
    has an effective role above observer (``role_of``). ``logical_ids`` restricts the check to the
    questions whose actions or assessed subjects name one of them (a revision's items). One job per
    batch, ``librarian_apply:<batch><suffix>``, unless an apply job of that batch is still QUEUED:
    it has not planned yet and will read the answer; a RUNNING one may hold an older snapshot (Sol
    54j #1). The job re-checks role, TTL, staleness and authority under its own locks: an answer
    whose action went stale is ``superseded`` there with the recorded reason ``stale``, never left
    pending.

    D-086 §1: a ``widen_scope`` answer is NEVER released here (propose-only in every role, D-058):
    the batch path would skip it anyway, so selecting it would requeue an apply job forever. It
    stays ``accepted_pending`` (``ops librarian questions list``: awaiting ``owner_apply``) until an
    explicit ``memory.answer`` by a device with write on every touched project applies it."""
    from hlmemo.librarian.tasks.apply_batch import proposal_actions

    cur = await conn.execute(
        """
        SELECT lq.batch_id::text, lq.project_id, lq.project_ids, lq.proposal
          FROM librarian_questions lq
         WHERE lq.status = 'accepted_pending' AND lq.batch_id IS NOT NULL
           AND lq.kind <> 'widen_scope'
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'librarian_write'
                            AND j.payload->>'op' = 'apply_batch'
                            AND j.payload->>'batch_id' = lq.batch_id::text
                            AND j.status = 'queued')
         ORDER BY lq.batch_id, lq.question_id
        """
    )
    released: dict[str, tuple[int, dict[str, Any]]] = {}
    for batch_id, home, pids, proposal in await cur.fetchall():
        if batch_id in released:
            continue
        actions = proposal_actions(proposal)  # both stored formats: W2b actions, W2a mutation
        if any(a.get("op") == "widen_scope" for a in actions):
            continue  # D-086 §1 (review 60: in Python, so a legacy W2a row is never dropped by SQL NULL)
        if logical_ids is not None:
            named = {int(k) for a in actions for k in (a.get("assessed") or {})} | {
                int(a[f])
                for a in actions
                for f in ("src_logical_id", "dst_logical_id", "logical_id")
                if a.get(f)
            }
            if not named & logical_ids:
                continue
        touched = {int(home), *(int(x) for x in pids)} | await action_projects(conn, actions)
        roles = [await role_of(p) for p in sorted(touched)]
        if "observer" not in roles:
            released[batch_id] = (int(home), proposal.get("capabilities") or {})
    return [apply_job(batch_id, home, caps, suffix) for batch_id, (home, caps) in sorted(released.items())]


async def release_now(
    conn: AsyncConnection, configured: str, suffix: str, logical_ids: set[int] | None = None
) -> list[dict[str, Any]]:
    """``releasable`` with the roles as they stand NOW for a librarian configured as ``configured``
    (a revision's ``release_pending`` job and the sweeper). The caller holds the role-order lock
    (shared). An observer librarian releases nothing (every effective role is observer)."""
    if configured == "observer":
        return []

    async def role_of(pid: int) -> str:
        return await effective_role(conn, configured, pid)

    return await releasable(conn, role_of, suffix, logical_ids)


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


async def lowest_role(conn: AsyncConnection, configured: str, project_ids: set[int] | list[int]) -> str:
    """The LOWEST effective role over every touched project (D-074; ``memory.answer``, the
    worker's ``apply_batch`` and its autonomous direct path). Callers hold the role-order lock."""
    roles = [await effective_role(conn, configured, int(p)) for p in sorted(set(project_ids))]
    return min(roles, key=_RANK.__getitem__) if roles else "observer"


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
    # Sol 56 #2: the batch row is locked BEFORE any event id of this decision is allocated, so a
    # librarian job that changes the same batch (fills it, marks it ready/applied) commits entirely
    # before or after this decision, and the event ids follow that order (replay = live order)
    await conn.execute("SELECT 1 FROM librarian_batches WHERE batch_id = %s FOR UPDATE", (batch_id,))
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
    "lowest_role",
    "pending_apply_jobs",
    "record_batch_decision",
    "release_now",
    "releasable",
    "record_role_decision",
]
