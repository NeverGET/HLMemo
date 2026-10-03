"""``python -m hlmemo.ops librarian withdraw``: the operator retracts librarian proposals (D-244).

``withdraw(conn, project=SLUG, question_ids=[...], reason=TEXT, by=<ops ctx>)`` moves ``open``,
``approved`` and ``accepted_pending`` questions of ONE project to the terminal status ``withdrawn``
(migration 0011). It changes no user item, link, validity or scope; it only takes the proposals off
every apply path. ``dry_run`` runs every lock and check and records nothing.

**Locks** (the apply path's order, the part of it this command needs): the role-order lock SHARED
(``roles.lock_role_order``; a promotion takes it EXCLUSIVE, so it commits wholly before or after this
command), then the question rows ``FOR UPDATE`` sorted by id (an ``apply_batch`` job, ``memory.answer``,
a batch decision or the expiry holding one commits first and this command sees its result), then the
event's FK rows (``events.lock_event_refs``), then the event id (so replay order = commit order).

**All or nothing.** Every check runs under those locks; if ANY id fails one, nothing is written and the
refusal (``E_VERSION_CONFLICT``) lists every offending id per reason in ``details.refused``:
``unknown``, ``other_project``, ``applied``, ``running_apply`` (its batch has a RUNNING ``apply_batch``
job, which may have planned with it: re-run once the job is done), ``not_pending`` (any other status,
``withdrawn`` included, keyed by status) and, with ``resolved_by_link``, ``link_not_live`` /
``link_mismatch`` (the link does not join the question's subjects).

**Never applied later.** Every apply path re-reads the question row ``FOR UPDATE`` and applies only
``approved``/``accepted_pending`` (worker ``APPLICABLE``); a promotion, the sweeper and
``release_pending`` select only ``accepted_pending`` (``roles.releasable``); ``memory.answer`` answers
only ``open``; the expiry skips terminal rows.

**Event.** ONE ``librarian`` event: device ``by`` (the operator, device 1), its client (``hlm-ops/<v>
(owner:NAME)``), op ``withdraw``. ``request``: the sorted ids, the reason (redacted, at most 500 chars)
and the optional link. ``resolved.question_status``: one record per question ``{question_id, status:
"withdrawn", decided_by, answer}``; the answer keeps what it replaces (``prior_status``, ``prior`` = the
owner's earlier answer). ``actor.set_question_status`` applies those records live and ``db/replay``
applies them verbatim (the field every question status change uses: answers, batch decisions, the
expiry); ``resolved.mutations`` is empty.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import write_queries as q
from hlmemo.librarian.actor import set_question_status
from hlmemo.librarian.events import CLIENT, insert_system_event, lock_event_refs
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.roles import lock_role_order

OP = "withdraw"
STATUS = "withdrawn"
WITHDRAWABLE = ("open", "approved", "accepted_pending")
WITHDRAW_MAX = 1000
REASON_MAX = 500


def parse_ids(raw: Iterable[str]) -> list[str]:
    """Canonical question ids, de-duplicated and sorted; ``E_INVALID_ARG`` names every malformed one."""
    ids: set[str] = set()
    bad: list[str] = []
    for value in raw:
        token = str(value).strip()
        if not token:
            continue
        try:
            ids.add(str(uuid.UUID(token)))
        except ValueError:
            bad.append(token[:64])
    if bad:
        raise ToolError("E_INVALID_ARG", f"{len(bad)} malformed question id(s)", malformed=bad[:50])
    if not ids:
        raise ToolError("E_INVALID_ARG", "no question ids given")
    if len(ids) > WITHDRAW_MAX:
        raise ToolError("E_INVALID_ARG", f"at most {WITHDRAW_MAX} question ids per withdraw", count=len(ids))
    return sorted(ids)


async def _project_id(conn: AsyncConnection, slug: str) -> int:
    cur = await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise ToolError("E_NOT_FOUND", "unknown project", project=slug)
    return int(row[0])


async def _link_refusals(
    conn: AsyncConnection, link_id: int, rows: dict[str, tuple[Any, ...]], ids: list[str]
) -> dict[str, list[str]]:
    """``resolved_by_link`` is metadata, but never a false one: the link must be live and join two
    subjects of EVERY withdrawn question (its endpoints among their subjects' logical items).
    Returns the refused question ids per reason."""
    cur = await conn.execute(
        "SELECT src_logical_id, dst_logical_id FROM links WHERE link_id = %s AND superseded_at = 'infinity'",
        (int(link_id),),
    )
    link = await cur.fetchone()
    if link is None:
        return {"link_not_live": list(ids)}
    vids = sorted({int(v) for qid in ids for v in rows[qid][4]})
    cur = await conn.execute(
        "SELECT version_id, logical_id FROM memory_versions WHERE version_id = ANY(%s)", (vids,)
    )
    logical = {int(v): int(lid) for v, lid in await cur.fetchall()}
    ends = {int(link[0]), int(link[1])}
    mismatch = [qid for qid in ids if not ends <= {logical.get(int(v)) for v in rows[qid][4]}]
    return {"link_mismatch": mismatch} if mismatch else {}


async def withdraw(
    conn: AsyncConnection,
    *,
    project: str,
    question_ids: Iterable[str],
    reason: str,
    by: AuthContext,
    dry_run: bool = False,
    resolved_by_link: int | None = None,
) -> dict[str, Any]:
    """Withdraw the questions (module doc). Runs in the caller's transaction; the caller commits."""
    if not by.is_admin:
        raise ToolError("E_FORBIDDEN", "withdraw is an operator action (the admin device)")
    ids = parse_ids(question_ids)
    text = Redactor().text(" ".join(str(reason or "").split()))[:REASON_MAX]
    if not text:
        raise ToolError("E_INVALID_ARG", "a reason is required")
    pid = await _project_id(conn, project)
    await lock_role_order(conn, exclusive=False)  # a promotion commits wholly before or after this
    cur = await conn.execute(
        """
        SELECT question_id::text, project_id, status, batch_id::text, subject_version_ids, answer
          FROM librarian_questions WHERE question_id = ANY(%s::uuid[])
         ORDER BY question_id FOR UPDATE
        """,
        (ids,),
    )
    rows = {r[0]: r for r in await cur.fetchall()}
    refused: dict[str, Any] = {}
    bad: set[str] = set()

    def refuse(key: str, qid: str) -> None:
        refused.setdefault(key, []).append(qid)
        bad.add(qid)

    for qid in ids:
        row = rows.get(qid)
        if row is None:
            refuse("unknown", qid)
        elif int(row[1]) != pid:
            refuse("other_project", qid)
        elif row[2] == "applied":
            refuse("applied", qid)
        elif row[2] not in WITHDRAWABLE:
            refused.setdefault("not_pending", {}).setdefault(row[2], []).append(qid)
            bad.add(qid)
    ours = [qid for qid in ids if qid not in bad]
    cur = await conn.execute(
        """
        SELECT DISTINCT payload->>'batch_id' FROM jobs
         WHERE kind = 'librarian_write' AND status = 'running' AND payload->>'op' = 'apply_batch'
           AND payload->>'batch_id' = ANY(%s)
        """,
        (sorted({rows[qid][3] for qid in ours}),),
    )
    running = {r[0] for r in await cur.fetchall()}
    for qid in ours:
        if rows[qid][3] in running:
            refuse("running_apply", qid)
    if resolved_by_link is not None and ours:
        for key, qids in (await _link_refusals(conn, resolved_by_link, rows, ours)).items():
            for qid in qids:
                refuse(key, qid)
    if refused:
        raise ToolError(
            "E_VERSION_CONFLICT",
            f"withdraw refused for {len(bad)} of {len(ids)} question id(s); nothing was written",
            project=project,
            refused=refused,
        )
    by_status = dict(sorted(Counter(rows[qid][2] for qid in ids).items()))
    out: dict[str, Any] = {
        "project": project,
        "dry_run": dry_run,
        "withdrawn": len(ids),
        "by_status": by_status,
        "question_ids": ids,
        "reason": text,
        "resolved_by_link": resolved_by_link,
        "event_id": None,
    }
    if dry_run:
        return out
    changes = []
    for qid in ids:
        answer: dict[str, Any] = {
            "decision": OP,
            "by": by.device_id,
            "reason": text,
            "prior_status": rows[qid][2],
        }
        if rows[qid][5] is not None:
            answer["prior"] = rows[qid][5]
        if resolved_by_link is not None:
            answer["resolved_by_link"] = int(resolved_by_link)
        changes.append({"question_id": qid, "status": STATUS, "decided_by": by.device_id, "answer": answer})
    request: dict[str, Any] = {
        "actor": CLIENT,
        "op": OP,
        "project": project,
        "question_ids": ids,
        "reason": text,
    }
    if resolved_by_link is not None:
        request["resolved_by_link"] = int(resolved_by_link)
    await lock_event_refs(conn, pid, by.device_id)
    at = await q.clock_now(conn)
    (event_id,) = await q.allocate_ids(conn, "events", 1)
    inserted = await insert_system_event(
        conn,
        kind="librarian",
        project_id=pid,
        device_id=by.device_id,
        client=by.client,
        request_id=uuid.uuid4(),
        request=request,
        resolved={"recorded_at": fmt_ts(at), "mutations": [], "question_status": changes},
        at=at,
        event_id=event_id,
    )
    assert inserted == event_id  # a fresh request id never conflicts
    await set_question_status(conn, changes, at)
    out["event_id"] = event_id
    return out


__all__ = ["OP", "STATUS", "WITHDRAWABLE", "WITHDRAW_MAX", "parse_ids", "withdraw"]
