"""`python -m hlmemo.ops librarian …` (W2b/W2c, §4b): the operator's view of and control over the
librarian's proposals. Runs in the ops transaction (``ops/cli.py``); nothing prints a secret
(titles and reasons pass the redactor).

    librarian audit --project P [--batch B] [--status S] [--json]
        every proposal (question) of P with its subjects, relation, confidence, tier, verifier
        result, guard flags, proposed actions, batch and answer — the Phase 5 labeling sheet;
    librarian questions list [--project P] [--status open] [--json]
    librarian approve-batch B [--except Q1,Q7] [--reject] [--owner NAME]
        records the owner's decision on the batch's open questions: one ``answer`` event per
        question (the ops actor, device 1, with the owner's device name in ``client``); approved
        ones are applied by the librarian only in role ``assistant``+ (the ``apply_batch`` job);
    librarian role set observer|assistant|autonomous --decision D-NNN [--project P]
        the D-062 ``set_role`` event (a ``librarian`` event); a per-project role can only lower;
    librarian expire
        expires open questions older than 30 days now (the worker also sweeps periodically);
    librarian backfill --project P [--device REF] [--limit N]
        enqueues the W2b review (``librarian_write:<event_id>``, priority 6) for the committed
        write/call_the_day/import events of P that have none yet — history written before the
        librarian was enabled (roadmap §5: ``hlm.ops librarian backfill --project hlmemo``). The
        capabilities are those of each event's device NOW, or of ``--device`` (e.g. the owner's
        current device when the importer device was revoked).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from psycopg import AsyncConnection

from hlmemo import __version__
from hlmemo.auth.context import AuthContext
from hlmemo.auth.errors import HlmError
from hlmemo.core.errors import ToolError
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.roles import ROLES, record_batch_decision, record_role_decision

CLIENT = f"hlm-ops/{__version__}"
OPERATOR_DEVICE_ID = 1


def add_parser(sub: Any) -> None:
    lib = sub.add_parser("librarian", help="librarian proposals, batches and role (W2b/W2c)")
    lsub = lib.add_subparsers(dest="action", required=True)
    a = lsub.add_parser("audit", help="every proposal of a project (Phase 5 labeling sheet)")
    a.add_argument("--project", required=True)
    a.add_argument("--batch")
    a.add_argument("--status")
    a.add_argument("--json", action="store_true")
    qs = lsub.add_parser("questions", help="list questions")
    qsub = qs.add_subparsers(dest="qaction", required=True)
    ql = qsub.add_parser("list")
    ql.add_argument("--project")
    ql.add_argument("--status", default="open")
    ql.add_argument("--json", action="store_true")
    ab = lsub.add_parser("approve-batch", help="record the owner's decision on a batch")
    ab.add_argument("batch")
    ab.add_argument("--except", dest="except_ids", default="", metavar="Q1,Q2")
    ab.add_argument("--reject", action="store_true", help="reject every open question of the batch")
    ab.add_argument("--owner", default="owner", help="the owner's device name, recorded in the event")
    rl = lsub.add_parser("role", help="set the librarian role (a D-062 set_role event)")
    rsub = rl.add_subparsers(dest="raction", required=True)
    rs = rsub.add_parser("set")
    rs.add_argument("role", choices=ROLES)
    rs.add_argument("--decision", required=True, help="the D-entry that authorizes the role")
    rs.add_argument("--project")
    lsub.add_parser("expire", help="expire questions past their 30 days now")
    bf = lsub.add_parser("backfill", help="enqueue the W2b review for history written before the librarian")
    bf.add_argument("--project", required=True)
    bf.add_argument("--device", help="device id or name whose capabilities the jobs carry")
    bf.add_argument("--limit", type=int, default=None, help="at most N source events")


def _ops_ctx(owner: str = "owner") -> AuthContext:
    return AuthContext(
        device_id=OPERATOR_DEVICE_ID,
        device_class="server",
        is_admin=True,
        token_generation=0,
        grants={},
        client=f"{CLIENT} (owner:{owner})"[:200],
    )


async def _project_id(conn: AsyncConnection, slug: str) -> int:
    cur = await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise HlmError("E_NOT_FOUND", "unknown project", {"project": slug})
    return int(row[0])


async def audit(
    conn: AsyncConnection, project: str, *, batch: str | None = None, status: str | None = None
) -> dict[str, Any]:
    pid = await _project_id(conn, project)
    red = Redactor()
    cur = await conn.execute(
        """
        SELECT q.question_id::text, q.batch_id::text, q.kind, q.status, q.subject_clues,
               q.subject_version_ids, q.proposal, q.answer, q.created_at, q.decided_at, q.expires_at,
               q.job_key
          FROM librarian_questions q
         WHERE q.project_id = %(pid)s AND (%(b)s::uuid IS NULL OR q.batch_id = %(b)s::uuid)
           AND (%(s)s::text IS NULL OR q.status = %(s)s)
         ORDER BY q.created_at, q.question_id
        """,
        {"pid": pid, "b": batch, "s": status},
    )
    rows = await cur.fetchall()
    vids = sorted({int(v) for r in rows for v in r[5]})
    titles: dict[int, tuple[str, list[int], str]] = {}
    if vids:
        cur = await conn.execute(
            "SELECT version_id, title, project_ids, kind FROM memory_versions WHERE version_id = ANY(%s)",
            (vids,),
        )
        titles = {int(v): (t, list(p), k) for v, t, p, k in await cur.fetchall()}
    cur = await conn.execute("SELECT project_id, slug FROM projects")
    slugs = {int(p): s for p, s in await cur.fetchall()}
    proposals = []
    for qid, bid, kind, st, clues, _svids, prop, ans, created, decided, expires, job_key in rows:
        svids = [int(c[1:].split(".")[0]) for c in clues]  # the proposal's subject order
        proposals.append(
            {
                "question_id": qid,
                "batch_id": bid,
                "kind": kind,
                "status": st,
                "job": job_key,
                "subjects": [
                    {
                        "clue": f"v{v}",
                        "title": red.text(titles.get(int(v), ("?", [], ""))[0]),
                        "kind": titles.get(int(v), ("", [], "?"))[2],
                        "projects": [slugs.get(p, str(p)) for p in titles.get(int(v), ("", [], ""))[1]],
                    }
                    for v in svids
                ],
                "relation": prop.get("relation"),
                "supersedes": prop.get("supersedes"),
                "confidence": prop.get("confidence"),
                "tier": prop.get("tier"),
                "auto_class": prop.get("auto_class"),
                "verification": prop.get("verification"),
                "flags": prop.get("flags") or [],
                "reason": red.text(str(prop.get("reason") or "")),
                "actions": [
                    {
                        k: a.get(k)
                        for k in ("op", "rel", "src_logical_id", "dst_logical_id", "logical_id", "valid_to")
                    }
                    for a in prop.get("actions") or ([prop["mutation"]] if prop.get("mutation") else [])
                ],
                "answer": ans,
                "created_at": created.isoformat(),
                "decided_at": decided.isoformat() if decided else None,
                "expires_at": expires.isoformat() if expires else None,
                "label": None,  # filled by the Phase 5 auditor: correct | incorrect | near-miss
            }
        )
    cur = await conn.execute(
        """
        SELECT b.batch_id::text, b.status, b.created_at, b.decided_at, b.applied_at,
               count(q.*) FILTER (WHERE q.status = 'open'), count(q.*)
          FROM librarian_batches b LEFT JOIN librarian_questions q ON q.batch_id = b.batch_id
         WHERE b.project_id = %(pid)s AND (%(b)s::uuid IS NULL OR b.batch_id = %(b)s::uuid)
         GROUP BY b.batch_id ORDER BY b.created_at, b.batch_id
        """,
        {"pid": pid, "b": batch},
    )
    batches = [
        {
            "batch_id": b,
            "status": s,
            "created_at": c.isoformat(),
            "decided_at": d.isoformat() if d else None,
            "applied_at": a.isoformat() if a else None,
            "open": int(n_open),
            "questions": int(n),
        }
        for b, s, c, d, a, n_open, n in await cur.fetchall()
    ]
    summary: dict[str, dict[str, int]] = {"by_kind": {}, "by_status": {}, "by_relation": {}}
    for p in proposals:
        for key, val in (("by_kind", p["kind"]), ("by_status", p["status"]), ("by_relation", p["relation"])):
            summary[key][str(val)] = summary[key].get(str(val), 0) + 1
    return {"project": project, "batches": batches, "proposals": proposals, "summary": summary}


async def backfill(
    conn: AsyncConnection, project: str, *, device: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    """Enqueue ``write_review`` for the project's committed write-shaped events that have no
    ``librarian_write:<event_id>`` job yet (only their still-current versions). One ``librarian``
    enqueue event per source event (idempotent: the same keys never enqueue twice)."""
    from hlmemo.librarian.jobs import enqueue, job_spec
    from hlmemo.librarian.tasks.write_review import MAX_VERSIONS, OP

    pid = await _project_id(conn, project)
    override: int | None = None
    if device is not None:
        cur = await conn.execute(
            "SELECT device_id FROM devices WHERE device_id::text = %s OR name = %s", (device, device)
        )
        row = await cur.fetchone()
        if row is None:
            raise HlmError("E_NOT_FOUND", "unknown device", {"device": device})
        override = int(row[0])
    cur = await conn.execute(
        """
        SELECT e.event_id, e.kind, e.device_id, e.payload FROM events e
         WHERE e.project_id = %s AND e.kind IN ('write', 'call_the_day', 'import')
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.dedupe_key = 'librarian_write:' || e.event_id)
         ORDER BY e.event_id LIMIT %s
        """,
        (pid, limit),
    )
    events = await cur.fetchall()
    enqueued = jobs = 0
    for event_id, kind, device_id, payload in events:
        res = payload.get("resolved") or {}
        items = res.get("items") or []
        raw = (payload.get("request") or {}).get("items") if kind == "write" else None
        vids = [int(it["version_id"]) for it in items if "version_id" in it]
        if not vids:
            continue
        cur = await conn.execute(
            "SELECT version_id, kind, importance FROM memory_versions WHERE version_id = ANY(%s)"
            " AND superseded_at = 'infinity' AND valid_to = 'infinity' AND status = 'active'",
            (vids,),
        )
        current = {int(v): (k, imp) for v, k, imp in await cur.fetchall()}
        entries = []
        for it in items:
            vid = int(it.get("version_id", 0))
            if vid not in current:
                continue
            idx = it.get("index", 0)
            src = raw[idx] if isinstance(raw, list) and idx < len(raw) and isinstance(raw[idx], dict) else {}
            vkind, imp = current[vid]
            entries.append(
                {
                    "version_id": vid,
                    "kind": vkind,
                    "client_importance": imp if (kind != "write" or "importance" in src) else None,
                    "client_stability": kind != "write" or "stability" in src or vkind == "project_card",
                }
            )
        if not entries:
            continue
        specs = []
        for k in range(0, len(entries), MAX_VERSIONS):
            key = (
                f"librarian_write:{event_id}" if k == 0 else f"librarian_write:{event_id}:{k // MAX_VERSIONS}"
            )
            specs.append(
                job_spec(
                    kind="librarian_write",
                    dedupe_key=key,
                    priority=6,
                    payload={
                        "op": OP,
                        "event_id": event_id,
                        "trigger": "backfill",
                        "versions": entries[k : k + MAX_VERSIONS],
                    },
                )
            )
        if await enqueue(conn, project_id=pid, trigger_device_id=override or int(device_id), specs=specs):
            enqueued += 1
            jobs += len(specs)
    return {"project": project, "source_events": len(events), "enqueued_events": enqueued, "jobs": jobs}


async def questions_list(
    conn: AsyncConnection, project: str | None, status: str | None
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT q.question_id::text, p.slug, q.kind, q.status, q.subject_clues, q.batch_id::text, q.created_at,
               q.proposal->>'relation', q.proposal->>'supersedes'
          FROM librarian_questions q JOIN projects p ON p.project_id = q.project_id
         WHERE (%(p)s::text IS NULL OR p.slug = %(p)s) AND (%(s)s::text IS NULL OR q.status = %(s)s)
         ORDER BY q.created_at, q.question_id
        """,
        {"p": project, "s": status},
    )
    return [
        {
            "question_id": qid,
            "project": slug,
            "kind": kind,
            "status": st,
            "clues": list(clues),
            "batch_id": bid,
            "relation": rel,
            "supersedes": sup,
            "created_at": created.isoformat(),
            "awaiting": _awaiting(kind, st),
        }
        for qid, slug, kind, st, clues, bid, created, rel, sup in await cur.fetchall()
    ]


def _awaiting(kind: str, status: str) -> str | None:
    """What an accepted question still waits for: a widen is applied ONLY by an explicit owner
    ``memory.answer`` in a role above observer (D-086 §1, ``owner_apply``); any other accepted
    answer is applied by the batch path once every touched project is promoted (``promotion``)."""
    if kind == "widen_scope" and status in ("approved", "accepted_pending"):
        return "owner_apply"
    if status == "accepted_pending":
        return "promotion"
    return None


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


async def dispatch(conn: AsyncConnection, args: argparse.Namespace) -> int:
    action = args.action
    try:
        if action == "audit":
            out = await audit(conn, args.project, batch=args.batch, status=args.status)
            if args.json:
                _print(out)
            else:
                for b in out["batches"]:
                    sys.stdout.write(
                        f"batch {b['batch_id']} {b['status']:<8} open={b['open']}/{b['questions']}\n"
                    )
                for p in out["proposals"]:
                    subj = " vs ".join(s["clue"] for s in p["subjects"])
                    sys.stdout.write(
                        f"  {p['question_id']} {p['kind']:<13} {p['status']:<9} {subj:<16} "
                        f"{p['relation'] or '-'}/{p['supersedes'] or '-'} conf={p['confidence'] or '-'} "
                        f"tier={p['tier'] or '-'} auto={p['auto_class']}\n"
                    )
            return 0
        if action == "questions":
            rows = await questions_list(conn, args.project, args.status)
            if args.json:
                _print({"questions": rows})
            else:
                for r in rows:
                    wait = f" awaiting={r['awaiting']}" if r["awaiting"] else ""
                    sys.stdout.write(
                        f"{r['question_id']} {r['project']:<20} {r['kind']:<13} {r['status']:<9}"
                        f" {','.join(r['clues'])}{wait}\n"
                    )
            return 0
        if action == "approve-batch":
            excepted = [x.strip() for x in args.except_ids.split(",") if x.strip()]
            summary = await record_batch_decision(
                conn,
                batch_id=args.batch,
                approver=_ops_ctx(args.owner),
                decision="reject" if args.reject else "accept",
                except_ids=excepted,
            )
            _print(summary)
            return 0
        if action == "role":
            project_id = await _project_id(conn, args.project) if args.project else None
            event_id = await record_role_decision(
                conn, role=args.role, decided_by=_ops_ctx(), decision=args.decision, project_id=project_id
            )
            _print(
                {"role": args.role, "project": args.project, "decision": args.decision, "event_id": event_id}
            )
            return 0
        if action == "backfill":
            _print(await backfill(conn, args.project, device=args.device, limit=args.limit))
            return 0
        if action == "expire":
            from hlmemo.librarian.questions import expire_due

            _print({"expired": await expire_due(conn)})
            return 0
    except ToolError as exc:  # the librarian helpers raise ToolError; ops reports HlmError
        raise HlmError(exc.code, exc.message, exc.details) from exc
    raise HlmError("E_INVALID_ARG", f"unknown librarian command {action}")


__all__ = ["add_parser", "audit", "backfill", "dispatch", "questions_list"]
