"""``apply_batch``: apply the owner-approved proposals of one batch (role ``assistant``+, §4b).

Enqueued by the last ``answer`` event of a batch decision. No LLM call. The worker applies the
accepted mutations only if the effective role is ``assistant`` or ``autonomous`` (in ``observer``
the outcome is ``role_denied`` and nothing changes) and only after the CC-3 recheck of the
original job's triggering device passes (else ``authority_lost``).
"""

from __future__ import annotations

from typing import Any

from hlmemo.librarian.tasks import Plan

OP = "apply_batch"


class ApplyBatch:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        batch_id = str(job.payload["batch_id"])
        async with await w.connect() as conn:
            cur = await conn.execute(
                "SELECT question_id::text, proposal FROM librarian_questions"
                " WHERE batch_id = %s AND status = 'approved' ORDER BY question_id",
                (batch_id,),
            )
            approved = await cur.fetchall()
            await conn.commit()
        caps = (
            job.payload.get("capabilities") or (approved[0][1].get("capabilities") if approved else {}) or {}
        )
        plan = Plan(
            OP,
            "approved" if approved else "not_approved",
            caps,
            request_extra={"batch_id": batch_id, "approved": [qid for qid, _ in approved]},
        )
        for qid, proposal in approved:
            plan.mutations.append(proposal["mutation"])
            plan.auto_ok.append(True)
            plan.meta.append({"question_id": qid})
        return plan


__all__ = ["OP", "ApplyBatch"]
