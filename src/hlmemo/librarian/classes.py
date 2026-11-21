"""D-286: librarian class grants, one class: C1 "a newer fact replaces part of a current fact"
(``docs/decisions/PROMOTION-C1.md``).

The role ladder stays ``observer`` on every project. C1 is a separate grant, checked next to the
ladder in the write review's apply step (``worker._plan_questions``): it lets the librarian apply
exactly one action of exactly one class — the PINNED part ``supersedes`` link newer -> older — and
nothing else. Nothing pending is released and answers stay labels.

* Plan time (``tasks/write_review.c1_link``): conditions 1-6 of the class (relation, action tier,
  kinds, same class, time order, verified quote) put the link on ``Proposal.c1``.
* Apply time (``c1_action``): the switches (``HLM_LIBRARIAN_C1`` on; the latest ``set_class`` decision
  of EVERY touched project says on) and conditions 7-9 (the assessed heads are current; no question
  for the pair but an expired or superseded one; no live supersedes link and no ended C1 link of
  the pair, either way; the project's daily budget ``HLM_LIBRARIAN_C1_DAILY_MAX`` per UTC day) on the
  CURRENT rows. Any failure: today's path (a
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

import re
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
#: a grant names the decision that authorizes it: a DECISIONS.md id (round 2 L2)
DECISION_RE = re.compile(r"^D-\d{3,4}$")


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
    decision = str(decision or "").strip()
    if not DECISION_RE.match(decision):
        raise ToolError("E_INVALID_ARG", "a class grant names the decision that authorizes it (D-NNN)")
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


async def c1_status(conn: AsyncConnection, settings: Any) -> dict[str, Any]:
    """Read-only C1 state for the heartbeat and ``ops status`` (review 1 L2): the process switch,
    the daily cap and how many projects the latest grant decision has on."""
    cur = await conn.execute(
        """
        SELECT count(*) FROM (
            SELECT DISTINCT ON (project_id) payload->'resolved'->'set_class'->>'on' AS on_
              FROM events
             WHERE kind = 'librarian' AND project_id IS NOT NULL
               AND payload->'resolved'->'set_class'->>'class' = %s
             ORDER BY project_id, event_id DESC) g
         WHERE g.on_ = 'true'
        """,
        (C1,),
    )
    return {
        "c1": "on" if getattr(settings, "librarian_c1", False) else "off",
        "c1_cap": int(getattr(settings, "librarian_c1_daily_max", 0) or 0),
        "c1_granted": int((await cur.fetchone())[0]),
    }


# --------------------------------------------------------------------------- what counts (review 1 M3)
#: an APPLIED C1 link (alias ``l``): written by a write_review job's event with ``props.auto = C1``.
#: A writer's survivor copy of it (a close or a link restatement cuts the link and copies its props)
#: is NOT an apply: it is never counted, listed or demoted on.
_APPLIED = """l.rel = 'supersedes' AND l.props->>'auto' = %(c)s
           AND EXISTS (SELECT 1 FROM events je WHERE je.event_id = l.source_event_id
                        AND je.kind = 'librarian' AND je.payload->'request'->>'op' = 'write_review')"""
#: an applied link (alias ``l``) is REVERTED when a ``class_revert`` event names it as its origin
_REVERT_OF = """(SELECT r.event_id FROM events r
                  WHERE r.kind = 'librarian' AND r.payload->'request'->>'op' = %(op)s
                    AND (r.payload->'request'->>'origin_link_id')::bigint = l.link_id
                  ORDER BY r.event_id LIMIT 1)"""


# --------------------------------------------------------------------------- apply-time checks
async def pair_reverted(conn: AsyncConnection, a: int, b: int) -> bool:
    """Condition 8, in EITHER direction (review 1 M1): a C1 link between the two items was ever
    ended (by a revert or by a writer: conservative), or a C1 revert recorded the pair. Reads ended
    links too (T6: no re-apply loop)."""
    cur = await conn.execute(
        """
        SELECT 1 FROM links
         WHERE rel = 'supersedes' AND props->>'auto' = %(c)s AND superseded_at <> 'infinity'
           AND ((src_logical_id = %(a)s AND dst_logical_id = %(b)s)
                OR (src_logical_id = %(b)s AND dst_logical_id = %(a)s))
        UNION ALL
        SELECT 1 FROM events
         WHERE kind = 'librarian' AND payload->'request'->>'op' = %(op)s
           AND payload->'request'->'pair' IN (jsonb_build_array(%(a)s::bigint, %(b)s::bigint),
                                              jsonb_build_array(%(b)s::bigint, %(a)s::bigint))
        LIMIT 1
        """,
        {"a": int(a), "b": int(b), "c": C1, "op": OP_REVERT},
    )
    return await cur.fetchone() is not None


async def pair_asked(conn: AsyncConnection, logical_ids: list[int]) -> bool:
    """Condition 7 (review 1 M2: a decided pair is sticky): a question of ANY kind whose subjects
    are exactly these logical items, in any of their versions, in any status but ``expired`` or
    ``superseded`` — pending ones (the pair waits for Cemal) and decided ones alike (applied,
    rejected, withdrawn, answered: Cemal or the operator has spoken on the pair)."""
    lids = sorted({int(x) for x in logical_ids})
    cur = await conn.execute(
        """
        SELECT 1 FROM librarian_questions lq
         WHERE lq.status NOT IN ('expired', 'superseded')
           AND lq.subject_version_ids && ARRAY(SELECT version_id FROM memory_versions
                                                WHERE logical_id = ANY(%(lids)s))
           AND ARRAY(SELECT DISTINCT v.logical_id FROM memory_versions v
                      WHERE v.version_id = ANY(lq.subject_version_ids) ORDER BY 1) = %(lids)s::bigint[]
         LIMIT 1
        """,
        {"lids": lids},
    )
    return await cur.fetchone() is not None


async def supersedes_between(conn: AsyncConnection, a: int, b: int) -> bool:
    """A live ``supersedes`` link joins the two items in EITHER direction (review 1 M1)."""
    return await actor.link_exists(conn, a, b, "supersedes") or await actor.link_exists(
        conn, b, a, "supersedes"
    )


async def applied_on_day(conn: AsyncConnection, project_id: int, at: datetime) -> int:
    """Condition 9: the APPLIED C1 links of the project recorded on ``at``'s UTC day (reverted ones
    too; a writer's survivor copy never)."""
    cur = await conn.execute(
        f"""
        SELECT count(*) FROM links l
         WHERE {_APPLIED} AND l.project_id = %(pid)s
           AND l.recorded_at >= (date_trunc('day', %(at)s::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        """,  # noqa: S608 - fixed fragments
        {"c": C1, "pid": int(project_id), "at": at},
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
    used: dict[int, set[tuple[int, int]]],
) -> dict[str, Any] | None:
    """The C1 link to apply directly, with the grant's decision id in its props, or ``None`` (the
    proposal then takes today's path). The caller holds the item locks of the proposal and the
    role-order lock (shared), checked the cross-project policy and that the assessed heads are
    current (``actor.is_stale``). ``used``: the pairs this job already applies, per project (a set,
    so a link the materialization skips as a repeat is never counted twice, review 1 L4)."""
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
    if await supersedes_between(conn, src.logical_id, dst.logical_id):
        return None  # a live supersedes link joins the pair already (either way): nothing to add
    if await pair_asked(conn, [src.logical_id, dst.logical_id]):
        return None  # 7: the pair waits for Cemal, or he (or the operator) decided it
    if await pair_reverted(conn, src.logical_id, dst.logical_id):
        return None  # 8
    home = src.project_id
    # 9: the daily budget, serialized per project by advisory namespace 6 (1-3 and 5 are taken): held
    # to commit, taken after the item locks and before the batch lock, like the batch lock itself
    await conn.execute("SELECT pg_advisory_xact_lock(6, hashtext(%s))", (f"librarian_c1:{home}",))
    cap = int(getattr(settings, "librarian_c1_daily_max", 0) or 0)
    pairs = used.setdefault(home, set())
    key = (src.logical_id, dst.logical_id)
    if key not in pairs and await applied_on_day(conn, home, at) + len(pairs) >= cap:
        return None
    pairs.add(key)
    return {**link, "props": {**props, "decision": grants[home][1]}}


# --------------------------------------------------------------------------- audit
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def _printable(text: str) -> str:
    """Round 2 L3: no control or bidi characters in what ``class applied`` prints (a title or
    quote can carry any text); tabs and newlines become spaces."""
    return _CONTROL.sub("", text.replace("\t", " ").replace("\n", " "))


async def applied(
    conn: AsyncConnection, project_id: int | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """Every APPLIED C1 link (of one project, recorded since ``since``): both items' clues and
    titles (redacted), the quote, the job event, and its state: ``reverted`` (a ``class_revert``
    names it; ``revert_event_id``), ``ended_by_write`` (a writer's close or link restatement ended
    it; not a revert) and ``live`` (it, or a writer's survivor copy of it, still stands). Oldest
    first."""
    from hlmemo.librarian.redact import Redactor

    red = Redactor()
    cur = await conn.execute(
        f"""
        SELECT l.link_id, p.slug, l.src_logical_id, l.dst_logical_id, l.dst_version_id, l.props,
               l.recorded_at, nullif(l.superseded_at, 'infinity'), l.source_event_id,
               e.payload->'request'->>'dedupe_key',
               (SELECT m->'assessed' FROM jsonb_array_elements(e.payload->'resolved'->'mutations') m
                 WHERE (m->>'link_id')::bigint = l.link_id LIMIT 1),
               {_REVERT_OF}
          FROM links l
          JOIN projects p ON p.project_id = l.project_id
          JOIN events e ON e.event_id = l.source_event_id
         WHERE {_APPLIED}
           AND (%(pid)s::bigint IS NULL OR l.project_id = %(pid)s)
           AND (%(since)s::timestamptz IS NULL OR l.recorded_at >= %(since)s)
         ORDER BY l.link_id
        """,  # noqa: S608 - fixed fragments
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
                title = _printable(red.text(r[0])) if r else None
            sides[side] = {"clue": f"v{vid}" if vid is not None else None, "title": title}
        reverted = revert_event is not None
        live = bool(await live_copies(conn, int(link_id)))
        out.append(
            {
                "link_id": int(link_id),
                "project": slug,
                "newer": {"logical_id": int(src), **sides["newer"]},
                "older": {"logical_id": int(dst), **sides["older"]},
                "quote": _printable(red.text(str((props or {}).get("quote") or ""))),
                "confidence": (props or {}).get("confidence"),
                "decision": (props or {}).get("decision"),
                "recorded_at": fmt_ts(recorded),
                "event_id": int(event_id),
                "job": job,
                "live": live,
                "reverted": reverted,
                "revert_event_id": int(revert_event) if reverted else None,
                "ended_by_write": ended is not None and not reverted,  # the applied row itself
                "ended_at": fmt_ts(ended) if ended is not None else None,
            }
        )
    return out


async def demotion_rate(
    conn: AsyncConnection, project_id: int, at: datetime, *, reverting: int | None = None
) -> dict[str, Any]:
    """The project's APPLIED C1 links recorded in the last ``DEMOTION_WINDOW`` before ``at`` and
    how many of them are REVERTED (a ``class_revert`` names them; ``reverting``: the origin being
    reverted now counts too). A link a writer ended is not a revert (review 1 M3)."""
    cur = await conn.execute(
        f"""
        SELECT count(*), count(*) FILTER (WHERE {_REVERT_OF} IS NOT NULL OR l.link_id = %(rev)s)
          FROM links l
         WHERE {_APPLIED} AND l.project_id = %(pid)s AND l.recorded_at >= %(since)s
        """,  # noqa: S608 - fixed fragments
        {"op": OP_REVERT, "c": C1, "rev": reverting, "pid": int(project_id), "since": at - DEMOTION_WINDOW},
    )
    n_applied, n_reverted = (int(x) for x in await cur.fetchone())
    rate = n_reverted / n_applied if n_applied else 0.0
    return {
        "applied": n_applied,
        "reverted": n_reverted,
        "rate": round(rate, 4),
        "days": DEMOTION_WINDOW.days,
    }


async def _origin(conn: AsyncConnection, link: q.LinkRow) -> q.LinkRow | None:
    """The APPLIED C1 link ``link`` stands for: itself, or the link a writer's survivor copy was cut
    from (``supersedes_link_id``, transitively; a visited set stops a cycle)."""
    seen: set[int] = set()
    cur_link: q.LinkRow | None = link
    while cur_link is not None and cur_link.link_id not in seen:
        seen.add(cur_link.link_id)
        cur = await conn.execute(
            "SELECT kind = 'librarian' AND payload->'request'->>'op' = 'write_review' FROM events"
            " WHERE event_id = %s",
            (int(cur_link.source_event_id),),
        )
        row = await cur.fetchone()
        if row is not None and row[0]:
            return cur_link
        if cur_link.supersedes_link_id is None:
            return None
        cur_link = await q.get_link(conn, int(cur_link.supersedes_link_id))
    return None


async def live_copies(conn: AsyncConnection, origin_id: int, *, lock: bool = False) -> list[tuple[int, Any]]:
    """``[(link_id, recorded_at)]`` of every LIVE link that stands for the applied link ``origin_id``:
    itself and every copy cut from it (``supersedes_link_id``, transitively: a writer's close or
    restatement can leave several survivor copies, round 2 HIGH). Only C1 copies (``props.auto``):
    a writer's own restated link also names the link it replaced and is never ended here.
    ``lock``: ``FOR UPDATE``."""
    cur = await conn.execute(
        """
        WITH RECURSIVE d(link_id) AS (
            SELECT %s::bigint
          UNION
            SELECT c.link_id FROM links c JOIN d ON c.supersedes_link_id = d.link_id
             WHERE c.rel = 'supersedes' AND c.props->>'auto' = 'C1')
        SELECT l.link_id, l.recorded_at FROM links l JOIN d ON d.link_id = l.link_id
         WHERE l.superseded_at = 'infinity' AND l.props->>'auto' = 'C1'
         ORDER BY l.link_id
        """
        + (" FOR UPDATE OF l" if lock else ""),
        (int(origin_id),),
    )
    return [(int(i), r) for i, r in await cur.fetchall()]


# --------------------------------------------------------------------------- revert
async def revert(conn: AsyncConnection, link_id: int, *, by: AuthContext, reason: str) -> dict[str, Any]:
    """Revert the applied C1 link that ``link_id`` stands for (the applied link, or any writer's
    survivor copy of it) in ONE ``librarian`` event (op ``class_revert``): a ``link_supersede`` of
    EVERY live copy of it (transaction time ends; nothing deleted), the applied link
    (``origin_link_id``), the pair (never applied again) and, when the project's 30-day reverted ÷
    applied is above ``DEMOTION_RATE`` and the grant is on, ``resolved.set_class`` off (``reason:
    auto-demotion``). An applied link with no live copy left is a no-op that says so (``noop``;
    nothing recorded). Caller commits."""
    reason = str(reason or "").strip()
    if not reason:
        raise ToolError("E_INVALID_ARG", "a revert names its reason")
    from hlmemo.librarian.roles import lock_role_order

    await lock_role_order(conn, exclusive=True)  # the demotion is a grant decision
    link = await q.get_link(conn, int(link_id))
    if link is None or link.rel != "supersedes" or (link.props or {}).get("auto") != C1:
        raise ToolError("E_NOT_FOUND", "no C1 link with this id")
    origin = await _origin(conn, link)
    if origin is None:
        raise ToolError("E_NOT_FOUND", "no C1 link with this id")
    pid = int(origin.project_id)
    union = {pid, int(link.project_id), *(int(p) for p in link.project_ids)}
    if not all(by.has(p, Role.WRITE) for p in sorted(union)):
        raise ToolError("E_FORBIDDEN_PROJECT", "reverting needs write on every project of the link")
    pair = [int(origin.src_logical_id), int(origin.dst_logical_id)]
    await q.lock_logical_ids(conn, pair)
    copies = await live_copies(conn, int(origin.link_id), lock=True)
    out: dict[str, Any] = {"link_id": int(link_id), "origin_link_id": int(origin.link_id), "project_id": pid}
    if not copies:
        cur = await conn.execute(
            "SELECT event_id FROM events WHERE kind = 'librarian' AND payload->'request'->>'op' = %s"
            " AND (payload->'request'->>'origin_link_id')::bigint = %s ORDER BY event_id LIMIT 1",
            (OP_REVERT, int(origin.link_id)),
        )
        done = await cur.fetchone()
        why = "already reverted" if done else "ended by a writer"
        return {
            **out,
            "noop": True,
            "reason": f"no live copy of the C1 link is left ({why}); nothing recorded",
            "revert_event_id": int(done[0]) if done else None,
        }
    await lock_event_refs(conn, pid, by.device_id)
    T = select_T(await q.clock_now(conn), *(r for _i, r in copies))  # after every link it ends
    rate = await demotion_rate(conn, pid, T, reverting=int(origin.link_id))
    on, decision = await grant(conn, pid)
    demote = on and rate["rate"] > DEMOTION_RATE
    mutations = [{"op": "link_supersede", "link_id": i, "rel": "supersedes"} for i, _r in copies]
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
        request_id=uuid.uuid5(NS_LIBRARIAN, f"{OP_REVERT}:{int(origin.link_id)}"),
        request={
            "actor": CLIENT,
            "op": OP_REVERT,
            "class": C1,
            "link_id": int(link_id),
            "origin_link_id": int(origin.link_id),
            "link_ids": [i for i, _r in copies],
            "pair": pair,
            "reason": reason[:500],
            "rate": rate,
        },
        resolved=resolved,
        at=T,
    )
    if event_id is None:  # pragma: no cover - a recorded revert left no live copy (checked above)
        raise ToolError("E_VERSION_CONFLICT", "the C1 link was already reverted", status="reverted")
    await actor.apply_mutations(conn, mutations, event_id, T)
    return {
        **out,
        "event_id": event_id,
        "links_ended": [i for i, _r in copies],
        "pair": pair,
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
    "live_copies",
    "c1_action",
    "c1_status",
    "demotion_rate",
    "grant",
    "guest_projects",
    "pair_reverted",
    "supersedes_between",
    "parse_since",
    "pair_asked",
    "record_class_decision",
    "revert",
]
