"""D-184 (A): apply (or revert) the EXPLICIT supersession links of one project.

``plan`` loads the project's current items, runs ``core/explicit_supersession.propose`` and
drops the pairs a live ``supersedes`` link already joins (idempotency). ``apply`` then writes the
links through the SAME path the librarian's applied proposals take:

1. per-item locks on every endpoint (``write_queries.lock_logical_ids``, the write path's lock),
   then each endpoint's head is re-checked against the version the proposal was computed from;
2. ``librarian.actor.materialize`` turns the ``link_insert`` actions into records with allocated
   ``link_id`` (it skips a pair whose live link appeared meanwhile);
3. ONE ``librarian`` system event (schema_version 2, operator device 1, client ``hlm-explicit``)
   whose ``payload.resolved.mutations`` holds exactly those records and whose ``request`` holds
   the proposals (op ``explicit_supersession``, parser version);
4. ``librarian.actor.apply_mutations`` inserts the rows.

Replay (``db/replay._replay_system``) re-applies ``resolved.mutations`` as recorded, so the links
are rebuilt byte for byte. Nothing else changes: no version is closed or altered, links only.

``revert`` is the inverse on the same path: one ``librarian`` event whose mutations are
``link_supersede`` records for every live ``props.by = explicit`` link of the project, so the link
rows get ``superseded_at`` (bi-temporal: a read with an earlier ``known_at`` still sees them) and
a replay reproduces the reversal. A later ``apply`` may write them again (new rows).
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo import __version__
from hlmemo.auth.errors import HlmError
from hlmemo.core import explicit_supersession as es
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import write_queries as q

CLIENT = f"hlm-explicit/{__version__}"
OP = "explicit_supersession"
OP_REVERT = "explicit_supersession_revert"
OPERATOR_DEVICE_ID = 1  # the reserved admin device: the operator path (ops/librarian.py)


async def project_id(conn: AsyncConnection, slug: str) -> int:
    cur = await conn.execute("SELECT project_id FROM projects WHERE slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise HlmError("E_NOT_FOUND", "unknown project", {"project": slug})
    return int(row[0])


async def load_docs(conn: AsyncConnection, pid: int) -> list[es.Doc]:
    """The project's current items (open-ended, active heads), by version id."""
    cur = await conn.execute(
        """
        SELECT version_id, logical_id, source->>'path', body, valid_from FROM memory_versions
         WHERE %s = ANY(project_ids) AND superseded_at = 'infinity' AND valid_to = 'infinity'
           AND status = 'active'
         ORDER BY version_id
        """,
        (pid,),
    )
    return [es.Doc(int(v), int(lid), path, body, vf) for v, lid, path, body, vf in await cur.fetchall()]


async def live_supersedes(conn: AsyncConnection, logical_ids: list[int]) -> set[tuple[int, int]]:
    """``(src, dst)`` of every live (current, open-ended) ``supersedes`` link among ``logical_ids``."""
    if not logical_ids:
        return set()
    cur = await conn.execute(
        """
        SELECT src_logical_id, dst_logical_id FROM links
         WHERE rel = 'supersedes' AND superseded_at = 'infinity' AND valid_to = 'infinity'
           AND src_logical_id = ANY(%(l)s) AND dst_logical_id = ANY(%(l)s)
        """,
        {"l": sorted(set(logical_ids))},
    )
    return {(int(s), int(d)) for s, d in await cur.fetchall()}


async def plan(conn: AsyncConnection, slug: str) -> tuple[int, list[es.Doc], list[es.Proposal], int]:
    """``(project_id, docs, new proposals, proposals already linked)``."""
    pid = await project_id(conn, slug)
    docs = await load_docs(conn, pid)
    existing = await live_supersedes(conn, [d.logical_id for d in docs])
    proposals = es.propose(docs, existing)
    fresh = [p for p in proposals if (p.source_logical_id, p.target_logical_id) not in existing]
    return pid, docs, fresh, len(proposals) - len(fresh)


def _action(p: es.Proposal, docs: dict[int, es.Doc]) -> dict[str, Any]:
    src, dst = docs[p.source_version_id], docs[p.target_version_id]
    valid_from = max(src.valid_from, dst.valid_from)  # the supersession holds once both exist
    return {
        "op": "link_insert",
        "rel": "supersedes",
        "src_logical_id": p.source_logical_id,
        "dst_logical_id": p.target_logical_id,
        "dst_version_id": None,
        "valid_from": fmt_ts(valid_from),
        "props": p.props(),
        "assessed": {
            str(p.source_logical_id): p.source_version_id,
            str(p.target_logical_id): p.target_version_id,
        },
    }


async def _record(
    conn: AsyncConnection, pid: int, request: dict[str, Any], records: list[dict[str, Any]]
) -> tuple[int, Any]:
    """ONE ``librarian`` system event holding ``records`` as ``resolved.mutations``, then applied."""
    from hlmemo.librarian.actor import apply_mutations
    from hlmemo.librarian.events import insert_system_event, lock_event_refs

    await lock_event_refs(conn, pid, OPERATOR_DEVICE_ID)
    at = await q.clock_now(conn)
    (event_id,) = await q.allocate_ids(conn, "events", 1)
    inserted = await insert_system_event(
        conn,
        kind="librarian",
        project_id=pid,
        device_id=OPERATOR_DEVICE_ID,
        client=CLIENT,
        request_id=uuid.uuid4(),
        request=request,
        resolved={"recorded_at": fmt_ts(at), "mutations": records},
        at=at,
        event_id=event_id,
    )
    assert inserted == event_id
    await apply_mutations(conn, records, event_id, at)
    return event_id, at


async def apply(conn: AsyncConnection, slug: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Propose and (unless ``dry_run``) write the project's explicit supersession links. Runs in
    the caller's transaction; the caller commits (or rolls back a dry run)."""
    pid = await project_id(conn, slug)
    # one explicit pass per project at a time (a second one waits, then finds the links)
    await conn.execute("SELECT pg_advisory_xact_lock(3, hashtext(%s))", (f"explicit:{pid}",))
    _pid, docs, fresh, already = await plan(conn, slug)
    out: dict[str, Any] = {
        "project": slug,
        "parser": es.PARSER_VERSION,
        "dry_run": dry_run,
        "items": len(docs),
        "already_linked": already,
        "proposals": [p.as_dict() for p in fresh],
        "applied": 0,
        "stale": 0,
        "event_id": None,
    }
    if dry_run or not fresh:
        return out
    from hlmemo.librarian.actor import head_endpoint, materialize

    await q.lock_logical_ids(conn, [lid for p in fresh for lid in (p.source_logical_id, p.target_logical_id)])
    keep: list[es.Proposal] = []
    for p in fresh:  # a revision committed between the load and the locks makes a proposal stale
        src = await head_endpoint(conn, p.source_logical_id)
        dst = await head_endpoint(conn, p.target_logical_id)
        if src and dst and (src.version_id, dst.version_id) == (p.source_version_id, p.target_version_id):
            keep.append(p)
    out["stale"] = len(fresh) - len(keep)
    by_vid = {d.version_id: d for d in docs}
    records, _ = await materialize(conn, None, None, [_action(p, by_vid) for p in keep])
    if not records:
        return out
    request = {
        "actor": CLIENT,
        "op": OP,
        "parser": es.PARSER_VERSION,
        "project": slug,
        "proposals": [p.as_dict() for p in keep],
    }
    event_id, _at = await _record(conn, pid, request, records)
    out.update(applied=len(records), event_id=event_id)
    return out


async def explicit_links(conn: AsyncConnection, pid: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT link_id, src_logical_id, dst_logical_id, props FROM links
         WHERE rel = 'supersedes' AND props->>'by' = %s AND superseded_at = 'infinity'
           AND %s = ANY(project_ids)
         ORDER BY link_id
        """,
        (es.BY, pid),
    )
    return [
        {"link_id": int(i), "src_logical_id": int(s), "dst_logical_id": int(d), "props": props}
        for i, s, d, props in await cur.fetchall()
    ]


async def revert(conn: AsyncConnection, slug: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Supersede (``superseded_at``, never delete) every live explicit link of the project, in one
    evented ``link_supersede`` batch. Caller commits."""
    pid = await project_id(conn, slug)
    await conn.execute("SELECT pg_advisory_xact_lock(3, hashtext(%s))", (f"explicit:{pid}",))
    links = await explicit_links(conn, pid)
    out: dict[str, Any] = {
        "project": slug,
        "dry_run": dry_run,
        "links": links,
        "reverted": 0,
        "event_id": None,
    }
    if dry_run or not links:
        return out
    await q.lock_logical_ids(
        conn, [lid for ln in links for lid in (ln["src_logical_id"], ln["dst_logical_id"])]
    )
    links = await explicit_links(conn, pid)  # re-read under the locks
    records = [
        {
            "op": "link_supersede",
            "link_id": ln["link_id"],
            "src_logical_id": ln["src_logical_id"],
            "dst_logical_id": ln["dst_logical_id"],
        }
        for ln in links
    ]
    if not records:
        return out
    request = {"actor": CLIENT, "op": OP_REVERT, "project": slug, "link_ids": [r["link_id"] for r in records]}
    event_id, _at = await _record(conn, pid, request, records)
    out.update(links=links, reverted=len(records), event_id=event_id)
    return out


__all__ = [
    "CLIENT",
    "OP",
    "OPERATOR_DEVICE_ID",
    "OP_REVERT",
    "apply",
    "explicit_links",
    "live_supersedes",
    "load_docs",
    "plan",
    "project_id",
    "revert",
]
