"""W2c: librarian questions — ``memory.answer``, the 30-day expiry and the query notices.

``memory.answer {project, request_id, question_id, decision ∈ {accept, reject, custom}, note?,
token_budget?}`` → an ack, in ONE transaction (PHASE2-4-ROADMAP W2c, CC-3, D-062):

1. **Authorization** (CC-3, against the ANSWERING device): ``write`` on ``project``; the question
   must belong to it and every subject version must be readable by the device (scope + a read
   grant on one of its projects), else ``E_NOT_FOUND`` (no enumeration); ``write`` on the union
   of the question's projects and every project the proposed action touches, else
   ``E_FORBIDDEN_PROJECT``. Accepting never widens grants.
2. **Idempotency** per ``(project, device, request_id)`` like ``memory.write``: the stored ack
   (``replayed: true``) after re-authorization, or ``E_REQUEST_ID_CONFLICT`` on a different
   payload.
3. The question must be ``open`` (or an ``approved``/``accepted_pending`` ``widen_scope`` waiting
   for a writer on both projects) and not past ``expires_at``; otherwise ``E_VERSION_CONFLICT
   {status}``.
4. **D-074: in the OBSERVER role an answer is a LABEL, never an action.** Under the role-order lock
   (SHARED, like the worker's apply) the effective librarian role of EVERY touched project is read;
   if any is ``observer``, ``accept`` records the answer with status ``accepted_pending`` and changes
   no user item, link, validity or scope. After a promotion (``set_role`` assistant+), those
   questions are applied through the normal ``apply_batch`` path with the full recheck.
   ``accept`` otherwise: every assessed subject is locked and compared with its head; a revision since the
   proposal makes the question ``superseded`` and NOTHING is applied (G-Q3). Otherwise the
   proposed actions are applied as the answering device's act: links and the bi-temporal close
   through the actor's materialize/apply (recorded ids, replayed like every librarian mutation);
   ``widen_scope`` as an ordinary revision through the write service (its own ``write`` event).
   Status ``applied``. ``reject`` → ``rejected``. ``custom`` → ``answered`` plus a re-plan job
   (``write_review`` of the still-current subjects; the answer rule is in working memory).
5. **Every answer** is stored as a ``librarian-rule`` fact (``memory.write_rule``, rule text +
   clue refs only; a note that reproduces item text is dropped from the rule, D-062 overlap
   guard).

The answer event (kind ``answer``, schema_version 2) records the verbatim arguments as
``payload.request`` and the applied effects in ``payload.resolved`` (``question_status`` with the
answer, ``mutations``, ``jobs``); replay applies exactly that and never calls an LLM.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import Field, field_validator

from hlmemo.auth.context import AuthContext, Role
from hlmemo.config import get_settings
from hlmemo.core.budget import DEFAULT_WRITE_BUDGET, BudgetError, Meter, validate_budget
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import fmt_ts, select_T
from hlmemo.core.write_models import SLUG_RE, _Strict, parse_request
from hlmemo.core.write_service import payload_sha256
from hlmemo.db import write_queries as q
from hlmemo.librarian import actor
from hlmemo.librarian.events import NS_LIBRARIAN, SCHEMA_VERSION_SYSTEM, insert_system_event
from hlmemo.librarian.jobs import assign_job_ids, insert_recorded_jobs, job_spec
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.reserved import reserved_ids
from hlmemo.librarian.roles import lock_role_order, lowest_role
from hlmemo.librarian.tasks.apply_batch import proposal_actions

TOOL = "memory.answer"
NOTE_MAX = 2000
NOTICES_MAX = 3
ANSWERABLE = {"open"}
_METER: Meter | None = None


def _meter() -> Meter:
    global _METER
    if _METER is None:
        _METER = Meter()
    return _METER


class AnswerRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    request_id: str
    question_id: str
    decision: Literal["accept", "reject", "custom"]
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    token_budget: int | None = None

    @field_validator("request_id", "question_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))


# --------------------------------------------------------------------------- visibility
async def _subjects_readable(conn: AsyncConnection, ctx: AuthContext, version_ids: list[int]) -> bool:
    """Every subject version passes (a) for the device in SOME project it can read."""
    if not version_ids:
        return True
    cur = await conn.execute(
        "SELECT version_id, project_ids, device_scope FROM memory_versions WHERE version_id = ANY(%s)",
        (list(version_ids),),
    )
    rows = await cur.fetchall()
    if len(rows) != len(set(version_ids)):
        return False
    scopes = set(ctx.scope_values())
    return all(
        scope in scopes and any(ctx.has(int(p), Role.READ) for p in pids) for _vid, pids, scope in rows
    )


# --------------------------------------------------------------------------- memory.answer
async def answer(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: dict[str, Any],
    *,
    raw: dict[str, Any] | None = None,
    configured_role: str | None = None,
) -> dict[str, Any]:
    """``configured_role`` defaults to this process's ``HLM_LIBRARIAN_ROLE`` (R2: ``observer``;
    llm.env reaches api and librarian alike); the effective role is that, lowered by the recorded
    role decisions and project policy exactly as in the worker."""
    args = raw if raw is not None else req
    configured = configured_role or get_settings().librarian_role
    request = parse_request(AnswerRequest, req)
    try:
        budget = validate_budget(request.token_budget, default=DEFAULT_WRITE_BUDGET)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    async with conn.transaction():
        found = await q.resolve_projects(conn, [request.project])
        project = found.get(request.project)
        if project is None or project.archived or not ctx.has(project.project_id, Role.WRITE):
            raise ToolError(
                "E_FORBIDDEN_PROJECT",
                f"no write grant on project {request.project!r}",
                project=request.project,
            )
        pid = project.project_id
        await q.lock_request_key(conn, pid, ctx.device_id, request.request_id)
        await lock_role_order(conn, exclusive=False)  # D-074: the role read below cannot change under us
        cur = await conn.execute(
            """
            SELECT question_id::text, project_id, project_ids, kind, subject_clues, subject_version_ids,
                   proposal, status, expires_at, batch_id::text
              FROM librarian_questions WHERE question_id = %s AND project_id = %s FOR UPDATE
            """,
            (request.question_id, pid),
        )
        row = await cur.fetchone()
        not_found = ToolError("E_NOT_FOUND", "question not found")
        if row is None:
            raise not_found
        qid, _qpid, qprojects, kind, clues, subject_vids, proposal, status, expires_at, batch_id = row
        if not await _subjects_readable(conn, ctx, [int(v) for v in subject_vids]):
            raise not_found
        actions = proposal_actions(proposal)
        union = {pid, *(int(p) for p in qprojects), *await actor.action_projects(conn, actions)}
        if not all(ctx.has(p, Role.WRITE) for p in sorted(union)):
            raise ToolError(
                "E_FORBIDDEN_PROJECT", "answering needs write on every project the question touches"
            )

        sha = payload_sha256(args)
        prior = await q.find_event(conn, pid, ctx.device_id, request.request_id)
        if prior is not None:
            if prior.kind != "answer" or prior.payload_sha256 != sha or prior.result is None:
                raise ToolError(
                    "E_REQUEST_ID_CONFLICT", "request_id was already used with a different payload"
                )
            replay = dict(prior.result)
            replay["replayed"] = True
            _meter().settle(replay, budget)
            return replay

        now = await q.clock_now(conn)
        answerable = status in ANSWERABLE or (
            kind == "widen_scope" and status in ("approved", "accepted_pending")
        )
        expired = expires_at is not None and expires_at <= now
        if not answerable or expired:  # past 30 days nothing is applied, approved or not (Sol 44 #2)
            shown = "expired" if answerable and expired else status
            raise ToolError("E_VERSION_CONFLICT", f"the question is {shown}", status=shown)

        redactor = Redactor()
        note = redactor.text(request.note) if request.note else None
        answer_rec: dict[str, Any] = {"decision": request.decision, "by": ctx.device_id}
        if note:
            answer_rec["note"] = note
        records: list[dict[str, Any]] = []
        jobs: list[dict[str, Any]] = []
        recorded: list[datetime] = []
        applied: dict[str, Any] = {"links": 0, "closed": [], "widened": []}
        write_events: list[int] = []
        new_status = {"accept": "applied", "reject": "rejected", "custom": "answered"}[request.decision]
        role = await lowest_role(conn, configured, union)  # every touched project (D-074)
        if request.decision == "accept" and role == "observer":
            # D-074: a label only. No subject lock, no staleness verdict, no mutation, no widen:
            # the batch path applies it after a promotion, with the full recheck.
            new_status = "accepted_pending"
        elif request.decision == "accept":
            assessed: dict[str, int] = {}
            for a in actions:
                assessed.update(a.get("assessed") or {})
            if await actor.is_stale(conn, {"assessed": assessed}):
                new_status = "superseded"
            else:
                from hlmemo.librarian.errors import AuthorityLost
                from hlmemo.librarian.trigger import capabilities_from_ctx

                caps = capabilities_from_ctx(ctx, sorted(union))  # the ANSWERING device (CC-3)
                try:
                    records, recorded = await actor.materialize(
                        conn, ctx, caps, [a for a in actions if a.get("op") != "widen_scope"]
                    )
                except AuthorityLost as exc:
                    raise ToolError(
                        "E_FORBIDDEN_PROJECT", "the answering device may not apply this action"
                    ) from exc
                for a in actions:
                    if a.get("op") == "widen_scope":
                        vid, ev = await _widen(conn, ctx, a, request.request_id)
                        applied["widened"].append(f"v{vid}")
                        write_events.append(ev)
                applied["links"] = sum(1 for r in records if r["op"] == "link_insert")
                applied["closed"] = [
                    f"v{sv['version_id']}"
                    for r in records
                    if r["op"] == "version_close"
                    for sv in r["survivors"]
                ]
                jobs = actor.close_embed_jobs(records)
        elif request.decision == "custom":
            jobs = await _replan_job(conn, ctx, pid, qid, [int(v) for v in subject_vids], note)
        rule_vid = await _rule(
            conn, kind, proposal, request.decision, note, list(clues), qid, request.request_id
        )
        T = select_T(await q.clock_now(conn), *recorded)
        await assign_job_ids(conn, jobs)
        change = {"question_id": qid, "status": new_status, "decided_by": ctx.device_id, "answer": answer_rec}
        ack: dict[str, Any] = {
            "request_id": request.request_id,
            "question_id": qid,
            "status": new_status,
            "role": role,
            "applied": applied,
            "rule": None if rule_vid is None else f"v{rule_vid}",
            "replayed": False,
        }
        used = _meter().settle(ack, budget)
        if used > budget:
            raise ToolError("E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold the ack", min=used)
        (event_id,) = await q.allocate_ids(conn, "events", 1)
        resolved = {
            "recorded_at": fmt_ts(T),
            "question_status": [change],
            "mutations": records,
            "jobs": jobs,
            "rule_version_id": rule_vid,
            "write_events": write_events,
            "batch_id": batch_id,
            "role": role,
        }
        await conn.execute(
            """
            INSERT INTO events (event_id, project_id, device_id, client, request_id, kind, schema_version,
                                payload, payload_sha256, occurred_at, result)
            OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s, %s, 'answer', %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                pid,
                ctx.device_id,
                ctx.client,
                request.request_id,
                SCHEMA_VERSION_SYSTEM,
                Jsonb({"request": args, "resolved": resolved}),
                sha,
                T,
                Jsonb(ack),
            ),
        )
        await actor.apply_mutations(conn, records, event_id, T)
        await actor.set_question_status(conn, [change], T)
        if jobs:
            await insert_recorded_jobs(conn, jobs, event_id, T)
    return ack


async def _widen(
    conn: AsyncConnection, ctx: AuthContext, action: dict[str, Any], request_id: str
) -> tuple[int, int]:
    """``widen_scope`` accepted: an ordinary revision of the item that adds the projects, through
    the write service (authorized there against the answering device: write on home ∪ old ∪ new).
    The interval is the item's own ``[valid_from, ∞)``: the content is unchanged, only its
    visibility widens. Returns ``(new version id, write event id)``."""
    from hlmemo.core.write_service import write

    lid = int(action["logical_id"])
    rows = await q.current_versions(conn, lid)
    head = max(rows, key=lambda r: r.version_id)
    slugs = await _slugs(
        conn, [head.project_id, *head.project_ids, *(int(p) for p in action["add_project_ids"])]
    )
    project_ids = [slugs[head.project_id]] + [
        slugs[p]
        for p in dict.fromkeys([*head.project_ids, *(int(x) for x in action["add_project_ids"])])
        if p != head.project_id
    ]
    item: dict[str, Any] = {
        "kind": head.kind,
        "logical_id": lid,
        "expected_version_id": head.version_id,
        "title": head.title,
        "body": head.body,
        "tags": list(head.tags),
        "pinned": head.pinned,
        "stability": head.stability,
        "project_ids": project_ids,
        "device_scope": head.device_scope,
        "valid_from": fmt_ts(head.valid_from),
    }
    if head.importance is not None:
        item["importance"] = head.importance
    req = {
        "project": slugs[head.project_id],
        "request_id": str(uuid.uuid5(NS_LIBRARIAN, f"widen:{request_id}:{lid}")),
        "client": ctx.client,
        "items": [item],
    }
    res = await write(conn, ctx, req)
    vid = res.versions[0].version_id
    cur = await conn.execute("SELECT source_event_id FROM memory_versions WHERE version_id = %s", (vid,))
    return vid, int((await cur.fetchone())[0])


async def _slugs(conn: AsyncConnection, project_ids: list[int]) -> dict[int, str]:
    cur = await conn.execute(
        "SELECT project_id, slug FROM projects WHERE project_id = ANY(%s)", (sorted(set(project_ids)),)
    )
    return {int(r[0]): r[1] for r in await cur.fetchall()}


async def _replan_job(
    conn: AsyncConnection,
    ctx: AuthContext,
    project_id: int,
    question_id: str,
    subject_vids: list[int],
    note: str | None,
) -> list[dict[str, Any]]:
    """``custom``: re-plan = a ``write_review`` of the subjects that are still current, under the
    ANSWERING device's capabilities; the owner's (redacted) note travels in this job's payload
    only and is shown to the model as a rule of this one job."""
    from hlmemo.librarian.tasks.write_review import MAX_VERSIONS, OP
    from hlmemo.librarian.trigger import capabilities_from_ctx, librarian_on

    if not await librarian_on(conn, project_id):
        return []
    cur = await conn.execute(
        "SELECT version_id, kind FROM memory_versions WHERE version_id = ANY(%s)"
        " AND superseded_at = 'infinity' AND valid_to = 'infinity' AND status = 'active' ORDER BY version_id",
        (list(subject_vids),),
    )
    current = await cur.fetchall()
    if not current:
        return []
    key = f"librarian_replan:{question_id}"
    return [
        job_spec(
            kind="librarian_write",
            dedupe_key=key,
            priority=4,
            payload={
                "op": OP,
                "trigger": "replan",
                "replan_of": question_id,
                "versions": [
                    {"version_id": int(v), "kind": k, "client_importance": None, "client_stability": True}
                    for v, k in current[:MAX_VERSIONS]
                ],
                "project_id": project_id,
                "capabilities": capabilities_from_ctx(ctx, [project_id]),
                "lineage": str(uuid.uuid5(NS_LIBRARIAN, "lineage:" + key)),
                **({"owner_note": note[:500]} if note else {}),
            },
        )
    ]


def rule_text(kind: str, proposal: dict[str, Any], decision: str, clues: list[str]) -> str:
    """Deterministic rule text for an answer (never model text, never item text)."""
    relation = proposal.get("relation") or kind
    sup = proposal.get("supersedes")
    what = f"{kind} proposal ({relation}" + (f", supersedes {sup}" if sup and sup != "none" else "") + ")"
    verb = {"accept": "accepted", "reject": "rejected", "custom": "answered differently"}[decision]
    return f"Owner {verb} a {what} for {' / '.join(clues)}."


async def _rule(
    conn: AsyncConnection,
    kind: str,
    proposal: dict[str, Any],
    decision: str,
    note: str | None,
    clues: list[str],
    question_id: str,
    request_id: str,
) -> int | None:
    """Every answer becomes a ``librarian-rule`` fact built ONLY from the structured decision (the
    deterministic ``rule_text``: decision, kind, relation, clue refs). The free-text note never
    enters working memory, which every job of every project loads (Sol 44 #1); it stays in the
    question's ``answer`` and reaches only this project's re-plan job."""
    from hlmemo.librarian.memory import MAX_RULE_CHARS, write_rule

    projects = [int(p) for a in proposal_actions(proposal) for p in a.get("project_ids") or []]
    try:
        async with conn.transaction():
            return await write_rule(
                conn,
                title=f"Answer: {kind} {question_id[:8]}",
                text=rule_text(kind, proposal, decision, clues)[:MAX_RULE_CHARS],
                clue_refs=[c for c in clues if c.startswith("v")],
                dedupe=f"answer:{question_id}:{request_id}",
                source_project_ids=projects,
            )
    except ToolError:
        return None


# --------------------------------------------------------------------------- expiry (30 days)
async def expire_due(conn: AsyncConnection, *, limit: int = 500) -> int:
    """Expire open or approved-but-not-applied questions past ``expires_at`` (Sol 46 #2): one
    ``librarian`` event (op ``expire``) per project records the status changes (replayed as-is).
    Caller commits."""
    now = await q.clock_now(conn)
    cur = await conn.execute(
        """
        SELECT question_id::text, project_id FROM librarian_questions
         WHERE status IN ('open', 'approved', 'accepted_pending') AND expires_at <= %s
         ORDER BY project_id, question_id LIMIT %s FOR UPDATE SKIP LOCKED
        """,
        (now, limit),
    )
    rows = await cur.fetchall()
    if not rows:
        return 0
    ids = await reserved_ids(conn)
    by_project: dict[int, list[str]] = {}
    for qid, pid in rows:
        by_project.setdefault(int(pid), []).append(qid)
    for pid, qids in by_project.items():
        changes = [{"question_id": qid, "status": "expired"} for qid in qids]
        event_id = await insert_system_event(
            conn,
            kind="librarian",
            project_id=pid,
            device_id=ids.librarian_device_id,
            client="hlm-librarian/expire",
            request_id=uuid.uuid5(NS_LIBRARIAN, "expire:" + ",".join(qids)),
            request={"op": "expire", "questions": qids},
            resolved={"recorded_at": fmt_ts(now), "question_status": changes},
            at=now,
        )
        if event_id is not None:
            await actor.set_question_status(conn, changes, now)
    return len(rows)


# --------------------------------------------------------------------------- query notices
_VISIBLE = """
    lq.project_id = %(pid)s AND lq.status = 'open' AND (lq.expires_at IS NULL OR lq.expires_at > %(now)s)
    AND NOT EXISTS (
        SELECT 1 FROM memory_versions mv WHERE mv.version_id = ANY(lq.subject_version_ids)
           AND NOT (mv.device_scope = ANY(%(scopes)s)
                    AND (%(admin)s OR mv.project_ids && %(readable)s::bigint[])))
"""


def notice_text(kind: str, clues: list[str], proposal: dict[str, Any]) -> str:
    """Deterministic, template-only notice (never model output: no injection path into the agent)."""
    a, b = (clues + ["?", "?"])[:2]
    rel = proposal.get("relation") or kind
    if kind == "widen_scope":
        return f"widen_scope: {b} (another project) may also apply here ({rel} of {a})"
    if kind == "contradiction":
        sup = proposal.get("supersedes")
        tail = (
            f"; proposed: {a if sup == 'new' else b} supersedes {b if sup == 'new' else a}"
            if sup
            in (
                "new",
                "old",
            )
            else ""
        )
        return f"contradiction: {a} vs {b}{tail}"
    return f"{rel}: {a} ~ {b}"


async def pending_block(
    conn: AsyncConnection, ctx: AuthContext, project_id: int, now: datetime, *, limit: int = NOTICES_MAX
) -> dict[str, Any] | None:
    """``{pending_questions, notices[≤3]}`` for ``memory.query`` (query/2), or ``None`` when the
    device can see no open question of the project."""
    params = {
        "pid": project_id,
        "now": now,
        "scopes": list(ctx.scope_values()),
        "admin": ctx.is_admin,
        "readable": sorted(ctx.grants),
    }
    cur = await conn.execute(f"SELECT count(*) FROM librarian_questions lq WHERE {_VISIBLE}", params)  # noqa: S608
    (n,) = await cur.fetchone()
    if not n:
        return None
    cur = await conn.execute(
        f"""
        SELECT question_id::text, kind, subject_clues, proposal FROM librarian_questions lq
         WHERE {_VISIBLE} ORDER BY created_at DESC, question_id LIMIT %(lim)s
        """,  # noqa: S608
        {**params, "lim": limit},
    )
    notices = [
        {"question_id": qid, "kind": kind, "clues": list(clues), "text": notice_text(kind, list(clues), prop)}
        for qid, kind, clues, prop in await cur.fetchall()
    ]
    return {"pending_questions": int(n), "notices": notices}


__all__ = [
    "NOTICES_MAX",
    "TOOL",
    "AnswerRequest",
    "answer",
    "expire_due",
    "notice_text",
    "pending_block",
    "rule_text",
]
