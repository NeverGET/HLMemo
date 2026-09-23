"""``apply_batch``: apply the owner-approved proposals of one batch (role ``assistant``+, §4b).

Enqueued by the last ``answer`` event of a batch decision. No LLM call. The worker applies the
accepted mutations only if the effective role is ``assistant`` or ``autonomous`` (in ``observer``
the outcome is ``role_denied`` and nothing changes) and only after the CC-3 recheck of the
original job's triggering device passes (else ``authority_lost``).
"""

from __future__ import annotations

from typing import Any

from hlmemo.librarian.roles import batch_decisions, batch_proposals
from hlmemo.librarian.tasks import Plan

OP = "apply_batch"


class ApplyBatch:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        batch_id = str(job.payload["batch_id"])
        async with await w.connect() as conn:
            _, proposals = await batch_proposals(conn, batch_id)
            decisions = await batch_decisions(conn, batch_id)
            await conn.commit()
        accepted = [p for p in proposals if decisions.get(p["proposal_id"]) == "accept"]
        caps = job.payload.get("capabilities") or (accepted[0].get("capabilities") if accepted else {}) or {}
        plan = Plan(
            OP,
            "approved" if accepted else "not_approved",
            caps,
            request_extra={"batch_id": batch_id, "accepted": [p["proposal_id"] for p in accepted]},
        )
        for p in accepted:
            plan.mutations.append(p["mutation"])
            plan.auto_ok.append(True)
            plan.meta.append({"proposal_id": p["proposal_id"]})
        return plan


__all__ = ["OP", "ApplyBatch"]
