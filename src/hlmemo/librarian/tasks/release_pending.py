"""``release_pending`` (Sol 56 #1): re-evaluate the ``accepted_pending`` answers a project-scope
revision may have made applicable, without an LLM call.

Enqueued by the write path in the same transaction as a revision that changes an item's
``project_ids`` (``librarian_release:<event_id>``, payload ``logical_ids``; recorded in the write
event's ``resolved.librarian_jobs``). The plan reads, under the shared role-order lock, which
pending answers naming those items are eligible now (``roles.release_now``: every current touched
project assistant+) and hands their ``apply_batch`` jobs to the worker as follow-up jobs of this
job's ``librarian`` event (``resolved.jobs``, replayed as-is). The apply job re-checks everything;
an answer whose action went stale is superseded there with the reason ``stale``. The worker's
periodic sweeper applies the same rule to every pending answer as a safety net.
"""

from __future__ import annotations

from typing import Any

from hlmemo.librarian.tasks import Plan

OP = "release_pending"


class ReleasePending:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        from hlmemo.librarian.roles import lock_role_order, release_now

        lids = {int(x) for x in job.payload.get("logical_ids") or []}
        suffix = f":rel{job.payload.get('event_id', job.job_id)}"
        async with await w.connect() as conn:
            async with conn.transaction():
                await lock_role_order(conn, exclusive=False)
                jobs = await release_now(conn, w.settings.librarian_role, suffix, lids)
            await conn.commit()
        plan = Plan(
            OP,
            "released" if jobs else "no_change",
            {},
            request_extra={
                "logical_ids": sorted(lids),
                "released": [j["payload"]["batch_id"] for j in jobs],
            },
        )
        plan.jobs = jobs
        return plan


def release_job(event_id: int, project_id: int, logical_ids: list[int]) -> dict[str, Any]:
    """The job a project-scope revision (event ``event_id``) enqueues."""
    import uuid

    from hlmemo.librarian.events import NS_LIBRARIAN
    from hlmemo.librarian.jobs import job_spec

    key = f"librarian_release:{event_id}"
    return job_spec(
        kind="librarian_write",
        dedupe_key=key,
        priority=4,
        payload={
            "op": OP,
            "event_id": event_id,
            "project_id": project_id,
            "logical_ids": sorted(set(logical_ids)),
            "lineage": str(uuid.uuid5(NS_LIBRARIAN, "lineage:" + key)),
        },
    )


__all__ = ["OP", "ReleasePending", "release_job"]
