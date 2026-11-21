"""D-286: librarian class grants, one class: C1 "a newer fact replaces part of a current fact"
(``docs/decisions/PROMOTION-C1.md``).

The role ladder stays ``observer`` on every project. C1 is a separate grant, checked next to the
ladder in the write review's apply step (``worker._plan_questions``): it lets the librarian apply
exactly one action of exactly one class — the PINNED part ``supersedes`` link newer -> older — and
nothing else. Nothing pending is released and answers stay labels.

* Plan time (``tasks/write_review.c1_link``): conditions 1-6 of the class (relation, action tier,
  kinds, same class, time order, verified quote) put the link on ``Proposal.c1``.
* Apply time (``c1_action``): the switches (``HLM_LIBRARIAN_C1`` on; the latest ``set_class`` decision
  of EVERY touched project says on) and conditions 7-9 (the assessed heads are current, no pending
  question for the pair, the pair never reverted, the project's daily budget
  ``HLM_LIBRARIAN_C1_DAILY_MAX`` per UTC day) on the CURRENT rows. Any failure: today's path (a
  question), unchanged.
* Grants: ``record_class_decision`` (admin only, per project only) records a ``librarian`` event
  ``{op: set_class, class, on, decision}``; its ``resolved.set_class`` is what ``grant`` reads (the
  latest one per project wins). Grant decisions take the role-order lock EXCLUSIVE, like role
  decisions, so an apply sees a revocation that committed before it. A guest account's project
  (``policy.account``, D-274) is never granted, and never applied in (``guest_projects``).
* Revert (``revert``): ends the link (``link_supersede``) in ONE evented step, records the pair
  (never applied again: condition 8) and, when the project's reverted ÷ applied over the last 30 days
  is above ``DEMOTION_RATE``, records ``set_class C1 off`` for the project in the same event
  (``reason: auto-demotion``). Re-granting needs a new decision.
* Audit (``applied``): every C1 link with both items' clues and titles, the quote and the job event;
  a reverted one is marked.

Replay: the link and its revert are ordinary ``link_insert`` / ``link_supersede`` mutations in
``resolved.mutations``; the grant lives only in events, so a rebuild reads the same grants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import fmt_ts, parse_ts, select_T
from hlmemo.db import write_queries as q
from hlmemo.db.librarian_queries import ACCOUNT_POLICY
from hlmemo.librarian import actor
from hlmemo.librarian.errors import AuthorityLost
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event, lock_event_refs

C1 = "C1"
CLASSES = (C1,)
OP_SET_CLASS = "set_class"
OP_REVERT = "class_revert"
#: what a reader shows for a C1 link (``memory.raw`` ``superseded_by`` entries and links)
APPLIED_BY = {C1: "librarian (C1)"}
#: automatic demotion: reverted ÷ applied over the project's last ``DEMOTION_WINDOW`` above this
DEMOTION_RATE = 0.05
DEMOTION_WINDOW = timedelta(days=30)
_PENDING = ("open", "approved", "accepted_pending")


def applied_by(props: dict[str, Any] | None) -> str | None:
    """The reader label of a link applied by a class grant (``None`` for every other link)."""
    auto = (props or {}).get("auto")
    return APPLIED_BY.get(auto) if isinstance(auto, str) else None


# --------------------------------------------------------------------------- grants
async def grant(conn: AsyncConnection, project_id: int, cls: str = C1) -> tuple[bool, str | None]:
    """``(on, decision)`` of the LATEST grant decision of ``cls`` for the project (a ``set_class``
    event or a revert's auto-demotion); no decision = off. Callers that apply hold the role-order
    lock (shared)."""
    cur = await conn.execute(
        """
        SELECT payload->'resolved'->'set_class' FROM events
         WHERE kind = 'librarian' AND project_id = %s
           AND payload->'resolved'->'set_class'->>'class' = %s
         ORDER BY event_id DESC LIMIT 1
        """,
        (int(project_id), cls),
    )
    row = await cur.fetchone()
    if row is None or not isinstance(row[0], dict):
        return False, None
    decided = row[0]
    if decided.get("project_id") is not None and int(decided["project_id"]) != int(project_id):
        return False, None  # pragma: no cover - written with the event's own project
    return decided.get("on") is True, decided.get("decision")


async def guest_projects(conn: AsyncConnection, project_ids: list[int] | set[int]) -> list[int]:
    """The projects among ``project_ids`` that belong to a (guest) account (D-274: ``policy.account``;
    the owner's projects carry none, D-279). Cemal, 2026-11-21: they are never granted a class."""
    cur = await conn.execute(
        "SELECT project_id FROM projects WHERE project_id = ANY(%s) AND COALESCE(policy->>%s, '') <> ''"
        " ORDER BY project_id",
        (sorted({int(p) for p in project_ids}), ACCOUNT_POLICY),
    )
    return [int(r[0]) for r in await cur.fetchall()]


async def record_class_decision(
    conn: AsyncConnection,
    *,
    cls: str,
    on: bool,
    decided_by: AuthContext,
    decision: str,
    project_id: int | None,
) -> int:
    """Record a grant decision (``hlm.ops librarian class set C1 on|off --project P --decision D``):
    one ``librarian`` event ``{op: set_class, class, on, decision}`` on the project. Admin (the
    operator device) only; per project only (no deployment-wide grant). Caller commits."""
    if cls not in CLASSES:
        raise ToolError("E_INVALID_ARG", f"unknown librarian class {cls!r}")
    if project_id is None:
        raise ToolError("E_INVALID_ARG", "a class grant is per project: name the project")
    if not decided_by.is_admin:
        raise ToolError("E_FORBIDDEN", "a class grant is recorded by the operator (admin device) only")
    decision = str(decision or "").strip()[:200]
    if not decision:
        raise ToolError("E_INVALID_ARG", "a class grant names the decision that authorizes it")
    from hlmemo.librarian.roles import lock_role_order

    await lock_role_order(conn, exclusive=True)  # an apply in flight commits first
    if on and await guest_projects(conn, [int(project_id)]):
        raise ToolError("E_FORBIDDEN_PROJECT", "a guest account's project is never granted a class")
    at = await q.clock_now(conn)
    event_id = await insert_system_event(
        conn,
        kind="librarian",
        project_id=int(project_id),
        device_id=decided_by.device_id,
        client=decided_by.client,
        request_id=uuid.uuid4(),
        request={"actor": CLIENT, "op": OP_SET_CLASS, "class": cls, "on": bool(on), "decision": decision},
        resolved={
            "recorded_at": fmt_ts(at),
            "set_class": {"class": cls, "on": bool(on), "decision": decision, "project_id": int(project_id)},
        },
        at=at,
    )
    assert event_id is not None
    return event_id


# --------------------------------------------------------------------------- apply-time checks
async def pair_reverted(conn: AsyncConnection, src: int, dst: int) -> bool:
    """Condition 8: a C1 link of the pair (newer logical, older logical) was ever ended, or a C1
    revert recorded the pair. Reads ended links too (T6: no re-apply loop)."""
    cur = await conn.execute(
        """
        SELECT 1 FROM links
         WHERE rel = 'supersedes' AND src_logical_id = %(s)s AND dst_logical_id = %(d)s
           AND props->>'auto' = %(c)s AND superseded_at <> 'infinity'
        UNION ALL
        SELECT 1 FROM events
         WHERE kind = 'librarian' AND payload->'request'->>'op' = %(op)s
           AND payload->'request'->'pair' = jsonb_build_array(%(s)s::bigint, %(d)s::bigint)
        LIMIT 1
        """,
        {"s": int(src), "d": int(dst), "c": C1, "op": OP_REVERT},
    )
    return await cur.fetchone() is not None


async def pending_pair(conn: AsyncConnection, logical_ids: list[int]) -> bool:
    """Condition 7: a pending (open, approved or accepted_pending) question of ANY kind whose
    subjects are exactly these logical items, in any of their versions."""
    lids = sorted({int(x) for x in logical_ids})
    cur = await conn.execute(
        """
        SELECT 1 FROM librarian_questions lq
         WHERE lq.status = ANY(%(st)s)
           AND lq.subject_version_ids && ARRAY(SELECT version_id FROM memory_versions
                                                WHERE logical_id = ANY(%(lids)s))
           AND ARRAY(SELECT DISTINCT v.logical_id FROM memory_versions v
                      WHERE v.version_id = ANY(lq.subject_version_ids) ORDER BY 1) = %(lids)s::bigint[]
         LIMIT 1
        """,
        {"st": list(_PENDING), "lids": lids},
    )
    return await cur.fetchone() is not None


async def applied_on_day(conn: AsyncConnection, project_id: int, at: datetime) -> int:
    """Condition 9: the C1 links of the project recorded on ``at``'s UTC day (reverted ones too)."""
    cur = await conn.execute(
        """
        SELECT count(*) FROM links
         WHERE rel = 'supersedes' AND props->>'auto' = %s AND project_id = %s
           AND recorded_at >= (date_trunc('day', %s::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        """,
        (C1, int(project_id), at),
    )
    return int((await cur.fetchone())[0])


async def c1_action(
    conn: AsyncConnection,
    settings: Any,
    ctx: Any,
    capabilities: dict[str, Any],
    link: dict[str, Any],
    project_ids: list[int],
    at: datetime,
    used: dict[int, int],
) -> dict[str, Any] | None:
    """The C1 link to apply directly, with the grant's decision id in its props, or ``None`` (the
    proposal then takes today's path). The caller holds the item locks of the proposal and the
    role-order lock (shared), checked the cross-project policy and that the assessed heads are
    current (``actor.is_stale``). ``used``: the C1 links this job already applied, per project."""
    if not getattr(settings, "librarian_c1", False):
        return None
    if (
        link.get("op") != "link_insert"
        or link.get("rel") != "supersedes"
        or link.get("dst_version_id") is None
    ):
        return None  # pragma: no cover - c1_link builds exactly this shape
    props = link.get("props") or {}
    if props.get("auto") != C1 or props.get("scope") != "part" or not str(props.get("quote") or "").strip():
        return None  # pragma: no cover - c1_link builds exactly this shape
    # switch 3: every touched project, the recorded ones and those of the CURRENT rows (D-074)
    touched = {int(p) for p in project_ids} | await actor.action_projects(conn, [link])
    if not touched:
        return None
    grants = {p: await grant(conn, p) for p in sorted(touched)}
    if not all(on for on, _decision in grants.values()):
        return None
    if await guest_projects(conn, touched):  # a project moved into an account after its grant
        return None
    try:  # the annotate capability on the CURRENT endpoints (else a question, as today)
        src, dst = await actor.check_link(conn, ctx, capabilities, link)
    except AuthorityLost:
        return None
    # conditions 3, 4, 7 on the CURRENT rows: facts, one home project and device scope, unpinned,
    # the older item's head is the pinned version
    if (
        src.kind != "fact"
        or dst.kind != "fact"
        or src.project_id != dst.project_id
        or src.device_scope != dst.device_scope
        or src.pinned
        or dst.pinned
        or dst.version_id != int(link["dst_version_id"])
    ):
        return None
    if await actor.link_exists(conn, src.logical_id, dst.logical_id, "supersedes"):
        return None  # a live supersedes link joins the pair already: nothing for C1 to add
    if await pending_pair(conn, [src.logical_id, dst.logical_id]):
        return None  # 7: the pair waits for Cemal
    if await pair_reverted(conn, src.logical_id, dst.logical_id):
        return None  # 8
    home = src.project_id
    # 9: the daily budget, serialized per project by advisory namespace 6 (1-3 and 5 are taken): held
    # to commit, taken after the item locks and before the batch lock, like the batch lock itself
    await conn.execute("SELECT pg_advisory_xact_lock(6, hashtext(%s))", (f"librarian_c1:{home}",))
    cap = int(getattr(settings, "librarian_c1_daily_max", 0) or 0)
    if await applied_on_day(conn, home, at) + used.get(home, 0) >= cap:
        return None
    used[home] = used.get(home, 0) + 1
    return {**link, "props": {**props, "decision": grants[home][1]}}


# --------------------------------------------------------------------------- audit
async def applied(
    conn: AsyncConnection, project_id: int | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """Every C1 link (of one project, recorded since ``since``): both items' clues and titles
    (redacted), the quote, the job event and, for a reverted one, ``reverted`` with its revert
    event. Oldest first."""
    from hlmemo.librarian.redact import Redactor

    red = Redactor()
    cur = await conn.execute(
        """
        SELECT l.link_id, p.slug, l.src_logical_id, l.dst_logical_id, l.dst_version_id, l.props,
               l.recorded_at, nullif(l.superseded_at, 'infinity'), l.source_event_id,
               e.payload->'request'->>'dedupe_key',
               (SELECT m->'assessed' FROM jsonb_array_elements(e.payload->'resolved'->'mutations') m
                 WHERE (m->>'link_id')::bigint = l.link_id LIMIT 1),
               (SELECT r.event_id FROM events r
                 WHERE r.kind = 'librarian' AND r.payload->'request'->>'op' = %(op)s
                   AND (r.payload->'request'->>'link_id')::bigint = l.link_id
                 ORDER BY r.event_id LIMIT 1)
          FROM links l
          JOIN projects p ON p.project_id = l.project_id
          LEFT JOIN events e ON e.event_id = l.source_event_id
         WHERE l.rel = 'supersedes' AND l.props->>'auto' = %(c)s
           AND (%(pid)s::bigint IS NULL OR l.project_id = %(pid)s)
           AND (%(since)s::timestamptz IS NULL OR l.recorded_at >= %(since)s)
         ORDER BY l.link_id
        """,
        {"op": OP_REVERT, "c": C1, "pid": project_id, "since": since},
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for (
        link_id,
        slug,
        src,
        dst,
        dst_vid,
        props,
        recorded,
        ended,
        event_id,
        job,
        assessed,
        revert_event,
    ) in rows:
        assessed = assessed if isinstance(assessed, dict) else {}
        newer_vid = assessed.get(str(src))
        older_vid = dst_vid if dst_vid is not None else assessed.get(str(dst))
        sides = {}
        for side, vid in (("newer", newer_vid), ("older", older_vid)):
            title = None
            if vid is not None:
                c = await conn.execute("SELECT title FROM memory_versions WHERE version_id = %s", (int(vid),))
                r = await c.fetchone()
                title = red.text(r[0]) if r else None
            sides[side] = {"clue": f"v{vid}" if vid is not None else None, "title": title}
        out.append(
            {
                "link_id": int(link_id),
                "project": slug,
                "newer": {"logical_id": int(src), **sides["newer"]},
                "older": {"logical_id": int(dst), **sides["older"]},
                "quote": red.text(str((props or {}).get("quote") or "")),
                "confidence": (props or {}).get("confidence"),
                "decision": (props or {}).get("decision"),
                "recorded_at": fmt_ts(recorded),
                "event_id": int(event_id),
                "job": job,
                "reverted": ended is not None,
                "reverted_at": fmt_ts(ended) if ended is not None else None,
                "revert_event_id": int(revert_event) if revert_event is not None else None,
            }
        )
    return out


async def demotion_rate(
    conn: AsyncConnection, project_id: int, at: datetime, *, reverting: int | None = None
) -> dict[str, Any]:
    """The project's C1 links recorded in the last ``DEMOTION_WINDOW`` before ``at`` and how many
    of them are reverted (``reverting``: a link of the window being reverted now counts as
    reverted)."""
    cur = await conn.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE superseded_at <> 'infinity' OR link_id = %s)
          FROM links
         WHERE rel = 'supersedes' AND props->>'auto' = %s AND project_id = %s AND recorded_at >= %s
        """,
        (reverting, C1, int(project_id), at - DEMOTION_WINDOW),
    )
    n_applied, n_reverted = (int(x) for x in await cur.fetchone())
    rate = n_reverted / n_applied if n_applied else 0.0
    return {
        "applied": n_applied,
        "reverted": n_reverted,
        "rate": round(rate, 4),
        "days": DEMOTION_WINDOW.days,
    }


# --------------------------------------------------------------------------- revert
async def revert(conn: AsyncConnection, link_id: int, *, by: AuthContext, reason: str) -> dict[str, Any]:
    """End the C1 link ``link_id`` in ONE ``librarian`` event (op ``class_revert``): its
    ``link_supersede`` mutation (transaction time ends; nothing deleted), the pair (never applied
    again) and, when the project's 30-day reverted ÷ applied is above ``DEMOTION_RATE`` and the
    grant is on, ``resolved.set_class`` off (``reason: auto-demotion``). Caller commits."""
    reason = str(reason or "").strip()
    if not reason:
        raise ToolError("E_INVALID_ARG", "a revert names its reason")
    from hlmemo.librarian.roles import lock_role_order

    await lock_role_order(conn, exclusive=True)  # the demotion is a grant decision
    link = await q.get_link(conn, int(link_id))
    if link is None or link.rel != "supersedes" or (link.props or {}).get("auto") != C1:
        raise ToolError("E_NOT_FOUND", "no C1 link with this id")
    pid = int(link.project_id)
    union = {pid, *(int(p) for p in link.project_ids)}
    if not all(by.has(p, Role.WRITE) for p in sorted(union)):
        raise ToolError("E_FORBIDDEN_PROJECT", "reverting needs write on every project of the link")
    await q.lock_logical_ids(conn, [link.src_logical_id, link.dst_logical_id])
    cur = await conn.execute(
        "SELECT superseded_at = 'infinity' FROM links WHERE link_id = %s FOR UPDATE", (int(link_id),)
    )
    (live,) = await cur.fetchone()
    if not live:
        raise ToolError("E_VERSION_CONFLICT", "the C1 link is no longer live", status="reverted")
    await lock_event_refs(conn, pid, by.device_id)
    T = select_T(await q.clock_now(conn), link.recorded_at)  # after the link it ends
    rate = await demotion_rate(conn, pid, T, reverting=int(link_id))
    on, decision = await grant(conn, pid)
    demote = on and rate["rate"] > DEMOTION_RATE
    mutations = [{"op": "link_supersede", "link_id": int(link_id), "rel": "supersedes"}]
    resolved: dict[str, Any] = {"recorded_at": fmt_ts(T), "mutations": mutations}
    if demote:
        resolved["set_class"] = {
            "class": C1,
            "on": False,
            "decision": decision,
            "project_id": pid,
            "reason": "auto-demotion",
        }
    event_id = await insert_system_event(
        conn,
        kind="librarian",
        project_id=pid,
        device_id=by.device_id,
        client=by.client,
        request_id=uuid.uuid5(NS_LIBRARIAN, f"{OP_REVERT}:{int(link_id)}"),
        request={
            "actor": CLIENT,
            "op": OP_REVERT,
            "class": C1,
            "link_id": int(link_id),
            "pair": [int(link.src_logical_id), int(link.dst_logical_id)],
            "reason": reason[:500],
            "rate": rate,
        },
        resolved=resolved,
        at=T,
    )
    if event_id is None:  # pragma: no cover - checked above under the locks
        raise ToolError("E_VERSION_CONFLICT", "the C1 link was already reverted", status="reverted")
    await actor.apply_mutations(conn, mutations, event_id, T)
    return {
        "event_id": event_id,
        "link_id": int(link_id),
        "project_id": pid,
        "pair": [int(link.src_logical_id), int(link.dst_logical_id)],
        "rate": rate,
        "demoted": demote,
    }


def parse_since(value: str | None) -> datetime | None:
    """``--since``: an ISO date or timestamp (UTC when no offset is given)."""
    return parse_ts(value, field="since") if value else None


__all__ = [
    "APPLIED_BY",
    "C1",
    "CLASSES",
    "DEMOTION_RATE",
    "OP_REVERT",
    "OP_SET_CLASS",
    "applied",
    "applied_by",
    "applied_on_day",
    "c1_action",
    "demotion_rate",
    "grant",
    "guest_projects",
    "pair_reverted",
    "parse_since",
    "pending_pair",
    "record_class_decision",
    "revert",
]
