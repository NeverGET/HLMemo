"""Foundation task ``pair_check``: contradiction check of one subject version against listed
candidates (T2 prompt, one pair per call), producing ``contradicts``/``supersedes`` annotations.

This is the W2a scaffold that exercises the whole pipeline (privacy gates, redaction, provider,
budget, capabilities, role ladder, audit event, replay). W2b replaces candidate selection
(hybrid retrieval under the triggering device's scope) and batches ≤ 8 pairs per call.

Privacy (D-016 deviation, W2a): nothing is sent when the project's ``policy.librarian`` is
``off``; a ``device:*``-scoped subject is skipped and ``device:*`` candidates are dropped unless
``HLM_LIBRARIAN_SEND_DEVICE_SCOPED=true``; a candidate the triggering device cannot read (enqueue
capability ``question`` ∩ current device scope) is never put into a prompt.
"""

from __future__ import annotations

from typing import Any

from hlmemo.core.temporal import fmt_ts
from hlmemo.db import auth_queries as aq
from hlmemo.librarian.memory import load_rules
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.tasks import Plan, user_message

OP = "pair_check"
MAX_CANDIDATES = 8
MAX_TEXT_CHARS = 4000


def _text(title: str, body: str) -> str:
    t = f"{title}\n{body}"
    return t if len(t) <= MAX_TEXT_CHARS else t[:MAX_TEXT_CHARS] + " …"


async def _version(conn: Any, version_id: int) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        SELECT version_id, logical_id, project_id, project_ids, device_scope, kind, status, title, body,
               valid_from, superseded_at = 'infinity' AND valid_to = 'infinity' AS current, pinned
          FROM memory_versions WHERE version_id = %s
        """,
        (version_id,),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    keys = (
        "version_id",
        "logical_id",
        "project_id",
        "project_ids",
        "device_scope",
        "kind",
        "status",
        "title",
        "body",
        "valid_from",
        "current",
        "pinned",
    )
    return dict(zip(keys, r, strict=True))


def _policy_off(policy: dict[str, Any] | None) -> bool:
    return (policy or {}).get("librarian") == "off"


class PairCheck:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        p = job.payload
        caps = p.get("capabilities") or {}
        subject_vid = int(p["version_id"])
        candidates = [int(c) for c in p.get("candidates", [])][:MAX_CANDIDATES]
        send_scoped = bool(w.settings.librarian_send_device_scoped)
        async with await w.connect() as conn:
            cur = await conn.execute("SELECT policy FROM projects WHERE project_id = %s", (p["project_id"],))
            row = await cur.fetchone()
            subject = await _version(conn, subject_vid)
            if row is None or _policy_off(row[0]):
                await conn.commit()
                return Plan(OP, "policy_off", caps, request_extra={"subject": f"v{subject_vid}"})
            if subject is None or not subject["current"] or subject["status"] != "active":
                await conn.commit()
                return Plan(OP, "stale_subject", caps, request_extra={"subject": f"v{subject_vid}"})
            if subject["device_scope"].startswith("device:") and not send_scoped:
                await conn.commit()
                return Plan(OP, "skipped_device_scope", caps, request_extra={"subject": f"v{subject_vid}"})
            dev = await aq.select_device(conn, int(caps.get("trigger_device_id", 0)))
            scopes = (
                {"all", f"class:{dev['class']}", f"device:{dev['device_id']}"} if dev is not None else {"all"}
            )
            readable = set(caps.get("question") or [])
            kept: list[dict[str, Any]] = []
            dropped = 0
            for cvid in candidates:
                c = await _version(conn, cvid)
                ok = (
                    c is not None
                    and c["current"]
                    and c["status"] == "active"
                    and c["logical_id"] != subject["logical_id"]
                    and c["device_scope"] in scopes
                    and set(c["project_ids"]) <= readable
                    and (send_scoped or not c["device_scope"].startswith("device:"))
                )
                if ok:
                    kept.append(c)  # type: ignore[arg-type]
                else:
                    dropped += 1
            rules = await load_rules(
                conn,
                max_rules=w.settings.librarian_memory_rules,
                max_tokens=w.settings.librarian_memory_tokens,
                meter=w.meter,
            )
            await conn.commit()

        task = load_task("contradiction")
        plan = Plan(
            OP,
            "proposed",
            caps,
            request_extra={
                "subject": f"v{subject_vid}",
                "candidates": [f"v{c['version_id']}" for c in kept],
                "dropped_candidates": dropped,
                "rules": [r["clue"] for r in rules],
            },
        )
        for c in kept:
            payload = {
                "A": {"text": _text(c["title"], c["body"]), "t_valid": fmt_ts(c["valid_from"])},
                "B": {
                    "text": _text(subject["title"], subject["body"]),
                    "t_valid": fmt_ts(subject["valid_from"]),
                },
            }
            res = await w.provider.complete(
                task, user_message("contradiction", payload, rules), job_id=job.job_id
            )
            plan.calls.append(res.audit(w.provider.redactor))
            out = res.output
            if not out.get("contradicts"):
                continue
            same_class = (
                c["project_id"] == subject["project_id"]
                and c["device_scope"] == subject["device_scope"]
                and not c["pinned"]
                and not subject["pinned"]
                and "experience" not in (c["kind"], subject["kind"])
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
                "valid_from": fmt_ts(subject["valid_from"]),
                "project_ids": list(subject["project_ids"]),
                "dst_project_ids": list(c["project_ids"]),
            }
            plan.mutations.append(
                {
                    **base,
                    "rel": "contradicts",
                    "src_logical_id": subject["logical_id"],
                    "dst_logical_id": c["logical_id"],
                }
            )
            plan.auto_ok.append(same_class)
            plan.meta.append({"reason": reason, "pair": [f"v{subject_vid}", f"v{c['version_id']}"]})
            if out.get("supersedes") in ("A", "B"):
                newer, older = (subject, c) if out["supersedes"] == "B" else (c, subject)
                plan.mutations.append(
                    {
                        **base,
                        "rel": "supersedes",
                        "src_logical_id": newer["logical_id"],
                        "dst_logical_id": older["logical_id"],
                        "valid_from": fmt_ts(newer["valid_from"]),
                        "project_ids": list(newer["project_ids"]),
                        "dst_project_ids": list(older["project_ids"]),
                    }
                )
                plan.auto_ok.append(same_class)
                plan.meta.append(
                    {"reason": reason, "pair": [f"v{newer['version_id']}", f"v{older['version_id']}"]}
                )
        if not plan.mutations:
            plan.outcome = "no_change"
        return plan


__all__ = ["MAX_CANDIDATES", "OP", "PairCheck"]
