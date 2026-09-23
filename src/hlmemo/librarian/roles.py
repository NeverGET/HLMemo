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
from hlmemo.librarian.errors import RoleNotAuthorized
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event
from hlmemo.librarian.jobs import insert_recorded_jobs, job_spec

ROLES = ("observer", "assistant", "autonomous")
_RANK = {r: i for i, r in enumerate(ROLES)}


def lower(a: str, b: str | None) -> str:
    if b not in _RANK:
        return a
    return a if _RANK[a] <= _RANK[b] else b


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
    """Record an owner role decision (``hlm.ops librarian role …``; a D-entry is the ``decision``)."""
    if role not in _RANK:
        raise ToolError("E_INVALID_ARG", f"unknown librarian role {role!r}")
    if project_id is None and not decided_by.is_admin:
        raise ToolError("E_FORBIDDEN", "the deployment role is set by the operator (admin device) only")
    if project_id is not None and not decided_by.has(project_id, Role.ADMIN):
        raise ToolError("E_FORBIDDEN_PROJECT", "project role override needs admin on the project")
    at = await q.clock_now(conn)
    event_id = await insert_system_event(
        conn,
        kind="librarian",
        project_id=project_id,
        device_id=decided_by.device_id,
        client=decided_by.client,
        request_id=uuid.uuid4(),
        request={"actor": CLIENT, "op": "set_role", "role": role, "decision": decision[:200]},
        resolved={"recorded_at": fmt_ts(at)},
        at=at,
    )
    assert event_id is not None
    return event_id


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
async def batch_proposals(conn: AsyncConnection, batch_id: str) -> tuple[int | None, list[dict[str, Any]]]:
    """``(project_id, proposals)`` recorded by the librarian events of ``batch_id``."""
    cur = await conn.execute(
        """
        SELECT project_id, payload->'resolved'->'proposals' FROM events
         WHERE kind = 'librarian' AND payload->'resolved'->>'batch_id' = %s
         ORDER BY event_id
        """,
        (batch_id,),
    )
    project_id: int | None = None
    proposals: list[dict[str, Any]] = []
    for pid, props in await cur.fetchall():
        project_id = pid
        proposals.extend(props or [])
    return project_id, proposals


async def batch_decisions(conn: AsyncConnection, batch_id: str) -> dict[str, str]:
    """``question_id -> decision`` from the ``answer`` events of the batch (latest wins)."""
    cur = await conn.execute(
        """
        SELECT payload->'request'->>'question_id', payload->'request'->>'decision' FROM events
         WHERE kind = 'answer' AND payload->'request'->>'batch_id' = %s
         ORDER BY event_id
        """,
        (batch_id,),
    )
    return {qid: d for qid, d in await cur.fetchall()}


def _mutation_projects(p: dict[str, Any]) -> set[int]:
    m = p.get("mutation") or {}
    return {int(x) for x in m.get("project_ids", [])} | {int(x) for x in m.get("dst_project_ids", [])}


async def record_batch_decision(
    conn: AsyncConnection,
    *,
    batch_id: str,
    approver: AuthContext,
    decision: str,
    except_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Owner approval of a proposal batch: one ``answer`` event per proposal (CC-3 authorization).

    ``accept`` approves every proposal except ``except_ids`` (which are rejected); ``reject``
    rejects all. The approver must hold ``write`` on every project any proposal touches, else
    ``E_FORBIDDEN_PROJECT``; an unknown batch is ``E_NOT_FOUND``. When anything is accepted, the
    last answer event enqueues the batch's ``apply_batch`` job (recorded in its ``resolved.jobs``).
    Rejections become ``librarian-rule`` facts in a separate step (``memory.write_rule``).
    """
    if decision not in ("accept", "reject"):
        raise ToolError("E_INVALID_ARG", "decision must be accept or reject")
    project_id, proposals = await batch_proposals(conn, batch_id)
    if project_id is None or not proposals:
        raise ToolError("E_NOT_FOUND", "batch not found")
    touched = set().union(*(_mutation_projects(p) for p in proposals)) | {project_id}
    if not all(approver.has(pid, Role.WRITE) for pid in touched):
        raise ToolError("E_FORBIDDEN_PROJECT", "approval needs write on every project the batch touches")
    excepted = set(except_ids or [])
    unknown = excepted - {p["proposal_id"] for p in proposals}
    if unknown:
        raise ToolError("E_INVALID_ARG", f"unknown proposal ids {sorted(unknown)}")
    at = await q.clock_now(conn)
    decided: dict[str, str] = {}
    accepted = 0
    for i, p in enumerate(proposals):
        d = "reject" if decision == "reject" or p["proposal_id"] in excepted else "accept"
        decided[p["proposal_id"]] = d
        accepted += d == "accept"
        last = i == len(proposals) - 1
        jobs = []
        if last and accepted:
            first = proposals[0]
            jobs = [
                job_spec(
                    kind="librarian_write",
                    dedupe_key=f"librarian_apply:{batch_id}",
                    priority=4,
                    payload={
                        "op": "apply_batch",
                        "batch_id": batch_id,
                        "project_id": project_id,
                        "capabilities": first.get("capabilities") or {},
                    },
                )
            ]
        request = {
            "op": "batch_decision",
            "batch_id": batch_id,
            "question_id": p["proposal_id"],
            "decision": d,
        }
        event_id = await insert_system_event(
            conn,
            kind="answer",
            project_id=project_id,
            device_id=approver.device_id,
            client=approver.client,
            request_id=uuid.uuid5(NS_LIBRARIAN, f"answer:{p['proposal_id']}:{approver.device_id}:{d}"),
            request=request,
            resolved={"recorded_at": fmt_ts(at), "jobs": jobs},
            at=at,
        )
        if event_id is not None and jobs:
            await insert_recorded_jobs(conn, jobs, event_id, at)
    return {
        "batch_id": batch_id,
        "accepted": accepted,
        "rejected": len(proposals) - accepted,
        "decisions": decided,
    }


__all__ = [
    "ROLES",
    "batch_decisions",
    "batch_proposals",
    "check_role_at_start",
    "effective_role",
    "latest_role_decision",
    "lower",
    "record_batch_decision",
    "record_role_decision",
]
