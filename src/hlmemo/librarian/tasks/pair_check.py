"""Foundation task ``pair_check``: contradiction check of one subject version against listed
candidates (T2 prompt, one pair per call), producing ``contradicts``/``supersedes`` annotations.

This is the W2a scaffold that exercises the whole pipeline (privacy gate, redaction, provider,
budget, capabilities, role ladder, question rows, audit event, replay). W2b replaces candidate
selection (hybrid retrieval under the triggering device's scope) and batches ≤ 8 pairs per call.

Privacy is default-deny (``librarian.privacy``): the gate runs over subject + candidates at plan
time and AGAIN over the pair immediately before each provider call, so a device revoked or a grant
or policy changed between enqueue and call stops the content from leaving. Every mutation records
the exact versions the model assessed; apply compares them against the locked heads (stale →
superseded, nothing applied).
"""

from __future__ import annotations

from typing import Any

from hlmemo.core.temporal import fmt_ts
from hlmemo.librarian import privacy
from hlmemo.librarian.errors import AuthorityLost, PrivacyDenied
from hlmemo.librarian.memory import load_rules, readable_rules, rules_still_readable
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.tasks import Plan, Proposal, user_message

OP = "pair_check"
MAX_CANDIDATES = 8
MAX_TEXT_CHARS = 4000


def _text(title: str, body: str) -> str:
    t = f"{title}\n{body}"
    return t if len(t) <= MAX_TEXT_CHARS else t[:MAX_TEXT_CHARS] + " …"


_SUBJECT_OUTCOME = {
    privacy.POLICY_OFF: "policy_off",
    privacy.DEVICE_SCOPED: "skipped_device_scope",
    privacy.NOT_CURRENT: "stale_subject",
}


class PairCheck:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        p = job.payload
        caps = p.get("capabilities") or {}
        subject_vid = int(p["version_id"])
        candidates = [int(c) for c in p.get("candidates", [])][:MAX_CANDIDATES]
        extra: dict[str, Any] = {"subject": f"v{subject_vid}"}
        verdict, items = await privacy.gate(w.connect, caps, [subject_vid, *candidates])
        if not verdict.device_ok:
            return Plan(OP, "authority_lost", caps, request_extra=extra)
        if not verdict.allowed(subject_vid):
            reason = verdict.denied[subject_vid]
            return Plan(
                OP, _SUBJECT_OUTCOME.get(reason, "denied"), caps, request_extra={**extra, "reason": reason}
            )
        subject = items[subject_vid]
        kept = [
            items[c] for c in candidates if verdict.allowed(c) and items[c].logical_id != subject.logical_id
        ]
        dropped: dict[str, int] = {}
        for c in candidates:
            if not verdict.allowed(c):
                reason = verdict.denied[c]
                dropped[reason] = dropped.get(reason, 0) + 1
        async with await w.connect() as conn:
            rules = await load_rules(
                conn,
                max_rules=w.settings.librarian_memory_rules,
                max_tokens=w.settings.librarian_memory_tokens,
                meter=w.meter,
                redactor=w.provider.redactor,
            )
            await conn.commit()
        rules = await readable_rules(w.connect, caps, rules)  # every ref visible to the device now
        task = load_task("contradiction")
        plan = Plan(
            OP,
            "proposed",
            caps,
            request_extra={**extra, "dropped_candidates": dropped, "rules": [r["clue"] for r in rules]},
        )
        for c in kept:
            # re-evaluate immediately before the call: authority/grants/policy may have changed
            pair, fresh = await privacy.gate(w.connect, caps, [subject_vid, c.version_id])
            if not pair.device_ok:
                return Plan(OP, "authority_lost", caps, calls=plan.calls, request_extra=plan.request_extra)
            if not (pair.allowed(subject_vid) and pair.allowed(c.version_id)):
                reason = pair.denied.get(c.version_id) or pair.denied.get(subject_vid) or "denied"
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            subj, cand = fresh[subject_vid], fresh[c.version_id]
            payload = {
                "A": {"text": _text(cand.title, cand.body), "t_valid": fmt_ts(cand.valid_from)},
                "B": {"text": _text(subj.title, subj.body), "t_valid": fmt_ts(subj.valid_from)},
            }
            pair_ids = [subject_vid, c.version_id]

            async def precheck(ids: list[int] = pair_ids) -> None:  # before EVERY provider attempt
                again, _ = await privacy.gate(w.connect, caps, ids)
                if not again.device_ok:
                    raise AuthorityLost("E_AUTHORITY_LOST")
                if not all(again.allowed(v) for v in ids):
                    raise PrivacyDenied("E_PRIVACY_DENIED")
                if not await rules_still_readable(w.connect, caps, rules):  # rule refs too (Sol 44 #1)
                    raise PrivacyDenied("E_PRIVACY_DENIED")

            try:
                res = await w.provider.complete(
                    task,
                    user_message("contradiction", payload, rules),
                    job_id=job.job_id,
                    lineage=job.lineage,
                    precheck=precheck,
                )
            except AuthorityLost:
                return Plan(OP, "authority_lost", caps, calls=plan.calls, request_extra=plan.request_extra)
            except PrivacyDenied:
                dropped["denied_before_attempt"] = dropped.get("denied_before_attempt", 0) + 1
                continue
            plan.calls.append(res.audit(w.provider.redactor))
            out = res.output
            if not out.get("contradicts"):
                continue
            assessed = {str(subj.logical_id): subj.version_id, str(cand.logical_id): cand.version_id}
            same_class = (
                cand.project_id == subj.project_id
                and cand.device_scope == subj.device_scope
                and not cand.pinned
                and not subj.pinned
                and "experience" not in (cand.kind, subj.kind)
            )
            reason = w.provider.redactor.text(str(out.get("reason", "")))[:200]
            base = {
                "op": "link_insert",
                "dst_version_id": None,
                "props": {
                    "by": "librarian",
                    "task": "contradiction",
                    "confidence": out.get("confidence", "n/a"),
                },
                "assessed": assessed,
            }
            pair_clues = [f"v{subj.version_id}", f"v{cand.version_id}"]
            touched = sorted({*subj.project_ids, *cand.project_ids})

            def proposal(
                m: dict[str, Any],
                auto: bool = same_class,
                assessed: dict[str, int] = assessed,
                clues: list[str] = pair_clues,
                touched: list[int] = touched,
                reason: str = reason,
            ) -> Proposal:
                return Proposal("contradiction", [m], auto, assessed, clues, touched, {"reason": reason})

            plan.proposals.append(
                proposal(
                    {
                        **base,
                        "rel": "contradicts",
                        "src_logical_id": subj.logical_id,
                        "dst_logical_id": cand.logical_id,
                        "valid_from": fmt_ts(subj.valid_from),
                        "project_ids": list(subj.project_ids),
                        "dst_project_ids": list(cand.project_ids),
                    }
                )
            )
            if out.get("supersedes") in ("A", "B"):
                newer, older = (subj, cand) if out["supersedes"] == "B" else (cand, subj)
                plan.proposals.append(
                    proposal(
                        {
                            **base,
                            "rel": "supersedes",
                            "src_logical_id": newer.logical_id,
                            "dst_logical_id": older.logical_id,
                            "valid_from": fmt_ts(newer.valid_from),
                            "project_ids": list(newer.project_ids),
                            "dst_project_ids": list(older.project_ids),
                        }
                    )
                )
        plan.request_extra["candidates"] = [f"v{c.version_id}" for c in kept]
        if not plan.proposals:
            plan.outcome = "no_change"
        return plan


__all__ = ["MAX_CANDIDATES", "OP", "PairCheck"]
