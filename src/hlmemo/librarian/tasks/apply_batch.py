"""``apply_batch``: apply the owner-approved proposals of one batch (role ``assistant``+, §4b).

Enqueued by the last ``answer`` event of a batch decision, or by a promotion (``set_role``
assistant+) for the ``accepted_pending`` answers recorded under observer (D-074); both statuses
are applied the same way. No LLM call. The worker applies the
accepted proposals only if the effective role is ``assistant`` or ``autonomous`` (in ``observer``
the outcome is ``role_denied`` and nothing changes). Each question is applied under ITS OWN
proposing job's capability set (W2b: one batch collects the proposals of many jobs): the CC-3
recheck of that job's triggering device must pass (else the question becomes ``authority_lost``)
and every assessed version must still be the head (else ``superseded``). ``widen_scope`` is never
applied by the librarian (D-058: propose-only); an approved one waits for ``memory.answer`` by a
device holding write on every project it touches.
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
                "SELECT question_id::text, kind, proposal FROM librarian_questions"
                " WHERE batch_id = %s AND status IN ('approved', 'accepted_pending') ORDER BY question_id",
                (batch_id,),
            )
            approved = await cur.fetchall()
            await conn.commit()
        caps = job.payload.get("capabilities") or {}
        plan = Plan(
            OP,
            "approved" if approved else "not_approved",
            caps,
            request_extra={"batch_id": batch_id, "approved": [qid for qid, _k, _p in approved]},
        )
        plan.approved = [(qid, {"kind": kind, **proposal}) for qid, kind, proposal in approved]
        return plan


def proposal_actions(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """The actions of a stored proposal (W2b ``actions``; W2a rows carried one ``mutation``)."""
    if "actions" in proposal:
        return list(proposal["actions"])
    return [proposal["mutation"]] if proposal.get("mutation") else []


def legacy_close(proposal: dict[str, Any]) -> bool:
    """A stored proposal that closes an item WITHOUT the v2 whole-item evidence (``close_ok``:
    every statement quoted and outdated, verifier-confirmed; D-076). Questions raised before the
    fact-level rule (R2 observer, v1 prompts) can carry whole-item closes of multi-fact items: they
    are never applied — the question is superseded and its subjects re-reviewed (Sol 54j #2)."""
    has_close = any(a.get("op") == "version_close" for a in proposal_actions(proposal))
    return has_close and proposal.get("close_ok") is not True


__all__ = ["OP", "ApplyBatch", "legacy_close", "proposal_actions"]
