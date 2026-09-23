"""W2b trigger: every committed ``write`` / ``call_the_day`` / ``import`` event enqueues
``librarian_write:<event_id>`` IN THE SAME TRANSACTION (PHASE2-4-ROADMAP W2b).

``plan_jobs`` is called by the write path after the event id is allocated and before the event
row is written; it returns the job descriptors (ids allocated) that the event records in
``payload.resolved.librarian_jobs`` — replay re-creates the exact rows from there — and that the
write path inserts after the event row. Nothing is enqueued when the librarian is disabled
(``HLM_LIBRARIAN_ENABLED``, read into ``WriteDeps.librarian_enqueue``), for a project with
``policy.librarian = off`` (this includes the reserved projects) or for an empty batch.

Priorities: write / call_the_day 3, import 6. A batch is split into jobs of at most
``write_review.MAX_VERSIONS`` versions (``librarian_write:<event_id>``, ``…:<event_id>:1``, …) so
one job stays well inside the per-lineage call ceiling. Capabilities (CC-3) are computed from
the request's own ``AuthContext`` — resolved in this transaction under ``FOR SHARE`` — so the
enqueue costs no extra device/grant query.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.librarian.events import NS_LIBRARIAN
from hlmemo.librarian.jobs import assign_job_ids, job_spec
from hlmemo.librarian.tasks.write_review import MAX_VERSIONS, OP

PRIORITY = {"write": 3, "call_the_day": 3, "import": 6, "ingest": 6}


def capabilities_from_ctx(ctx: AuthContext, project_ids: list[int]) -> dict[str, Any]:
    """The CC-3 capability set of the calling device NOW (same shape as ``jobs.compute_capabilities``)."""
    if ctx.is_admin:
        write = read = sorted(set(project_ids))
    else:
        write = sorted(pid for pid, role in ctx.grants.items() if role in (Role.WRITE, Role.ADMIN))
        read = sorted(ctx.grants)
    return {
        "trigger_device_id": ctx.device_id,
        "annotate": write,
        "correct": write,
        "question": read,
        "librarian_memory": True,
        "global_experience": False,
    }


async def librarian_on(conn: AsyncConnection, project_id: int) -> bool:
    cur = await conn.execute(
        "SELECT COALESCE(policy->>'librarian', 'on') <> 'off' FROM projects WHERE project_id = %s",
        (project_id,),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def plan_jobs(
    conn: AsyncConnection,
    ctx: AuthContext,
    *,
    enabled: bool,
    kind: str,
    event_id: int,
    project_id: int,
    versions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Job descriptors for the new versions of event ``event_id`` (``[]`` = none).

    ``versions``: ``[{version_id, kind, client_importance, client_stability, project_ids}]`` in
    item order (the write path's new versions, not survivors)."""
    if not enabled or not versions or kind not in PRIORITY:
        return []
    if not await librarian_on(conn, project_id):
        return []
    touched = sorted({project_id, *(p for v in versions for p in v.get("project_ids", []))})
    caps = capabilities_from_ctx(ctx, touched)
    specs = []
    for k in range(0, len(versions), MAX_VERSIONS):
        key = f"librarian_write:{event_id}" if k == 0 else f"librarian_write:{event_id}:{k // MAX_VERSIONS}"
        entries = [
            {
                "version_id": int(v["version_id"]),
                "kind": v["kind"],
                "client_importance": v.get("client_importance"),
                "client_stability": bool(v.get("client_stability")),
            }
            for v in versions[k : k + MAX_VERSIONS]
        ]
        spec = job_spec(
            kind="librarian_write",
            dedupe_key=key,
            priority=PRIORITY[kind],
            payload={
                "op": OP,
                "event_id": event_id,
                "trigger": kind,
                "versions": entries,
                "project_id": project_id,
                "capabilities": caps,
                "lineage": str(uuid.uuid5(NS_LIBRARIAN, "lineage:" + key)),
            },
        )
        specs.append(spec)
    return await assign_job_ids(conn, specs)


__all__ = ["PRIORITY", "capabilities_from_ctx", "librarian_on", "plan_jobs"]
