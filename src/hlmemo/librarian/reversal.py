"""D-118: the reversal of a writer's own update — a COMPENSATING event, never a delete.

``revert_write_update(conn, event_id=E, index=I, update=K, by=owner, reason=…)`` (``python -m
hlmemo.ops librarian revert-update E --item I --update K --reason …``) undoes what the ``memory.write``
event ``E`` did for ``items[I].updates[K]`` (``core/write_updates.py``):

* a span revision (``version_revise``): ``actor.unrevise_records`` restores the pre-revision text
  as a NEW version with its original validity and ends the revision's self-link;
* a close (``version_close``): ``actor.reopen_record`` restores the closed rows;
* the update's ``supersedes`` link (``link_insert``): ``link_supersede`` (transaction time ends).

The records of the update are selected by the tags every one of them carries (``write_event_id`` +
``update``). Lock order and rechecks as on every apply path (D-083, D-095): the items (one sorted
lock), the cross-project policy (``actor.policy_blocked``, project rows ``FOR SHARE``), then the
version check: refused (``E_VERSION_CONFLICT``) while a later change depends on the update, or when
it was already reverted. ONE ``librarian`` event (op ``revert_write_update``, a deterministic
request id per update) records the mutations with their allocated ids and the embed jobs of the
restored rows; replay applies exactly that. The event history keeps the update AND its reversal.

B3 port: B-real's ``revert_close`` / ``revert_revision`` (librarian proposals) are not part of the
minimal D-118 port; only the writer's reversal is.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import fmt_ts, parse_ts, select_T
from hlmemo.db import write_queries as q
from hlmemo.librarian import actor
from hlmemo.librarian.events import CLIENT, NS_LIBRARIAN, insert_system_event, lock_event_refs
from hlmemo.librarian.jobs import assign_job_ids, insert_recorded_jobs

OP_WRITE_UPDATE = "revert_write_update"


async def _link_live(conn: AsyncConnection, link_id: int) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM links WHERE link_id = %s AND superseded_at = 'infinity'", (int(link_id),)
    )
    return await cur.fetchone() is not None


async def revert_write_update(
    conn: AsyncConnection, *, event_id: int, index: int, update: int, by: AuthContext, reason: str
) -> dict[str, Any]:
    """Revert the writer's update ``items[index].updates[update]`` of the ``write`` event
    ``event_id`` (see the module doc). Caller commits."""
    cur = await conn.execute(
        "SELECT project_id, kind, payload->'resolved' FROM events WHERE event_id = %s", (int(event_id),)
    )
    row = await cur.fetchone()
    if row is None or row[1] != "write":
        raise ToolError("E_NOT_FOUND", "write event not found")
    pid, resolved = int(row[0]), row[2] or {}
    entry = next(
        (
            u
            for u in resolved.get("updates") or []
            if int(u.get("index", -1)) == int(index) and int(u.get("update", -1)) == int(update)
        ),
        None,
    )
    if entry is None:
        raise ToolError("E_NOT_FOUND", "the write event holds no such update")
    records = [m for m in entry.get("mutations") or [] if m.get("update") == [int(index), int(update)]]
    if entry.get("status") not in ("applied", "linked") or not records:
        raise ToolError("E_VERSION_CONFLICT", f"the update was {entry.get('status')}: nothing to revert")
    union = {pid, *await actor.action_projects(conn, records)}
    if not all(by.has(p, Role.WRITE) for p in sorted(union)):
        raise ToolError("E_FORBIDDEN_PROJECT", "reverting needs write on every project the update touched")
    request_id = uuid.uuid5(NS_LIBRARIAN, f"{OP_WRITE_UPDATE}:{int(event_id)}:{int(index)}:{int(update)}")
    await q.lock_logical_ids(conn, actor.action_logical_ids(records))  # items → policy → version
    if await actor.policy_blocked(conn, records, union):
        raise ToolError("E_FORBIDDEN_PROJECT", "the cross-project policy forbids this relation now")
    cur = await conn.execute(
        "SELECT event_id FROM events WHERE project_id = %s AND device_id = %s AND request_id = %s",
        (pid, by.device_id, request_id),
    )
    if await cur.fetchone() is not None:
        raise ToolError("E_VERSION_CONFLICT", "the update was already reverted", status="reverted")
    written_at = parse_ts(resolved["recorded_at"], field="recorded_at")
    out: list[dict[str, Any]] = []
    recorded = []
    for rec in records:
        if rec["op"] == "version_revise":
            res = await actor.unrevise_records(conn, rec)
            if isinstance(res, str):
                raise ToolError("E_VERSION_CONFLICT", f"{res}: revert that one first", status="changed")
            out.extend(res[0])
            recorded.extend(res[1])
        elif rec["op"] == "version_close":
            reopened = await actor.reopen_record(conn, rec, written_at)
            if reopened is None:
                raise ToolError(
                    "E_VERSION_CONFLICT", "the closed memory was changed since the update", status="changed"
                )
            out.append(reopened[0])
            recorded.extend(reopened[1])
        elif rec["op"] == "link_insert":
            link = await q.get_link(conn, int(rec["link_id"]))
            if link is None or not await _link_live(conn, int(rec["link_id"])):
                raise ToolError("E_VERSION_CONFLICT", "the update's link is no longer live", status="changed")
            out.append({"op": "link_supersede", "link_id": int(rec["link_id"]), "rel": rec["rel"]})
            recorded.append(link.recorded_at)  # review 76 #5: T follows the link it ends
    jobs = actor.close_embed_jobs(out)
    await assign_job_ids(conn, jobs)
    await lock_event_refs(conn, pid, by.device_id)
    T = select_T(await q.clock_now(conn), *recorded)  # after every row and link it supersedes
    new_event = await insert_system_event(
        conn,
        kind="librarian",
        project_id=pid,
        device_id=by.device_id,
        client=by.client,
        request_id=request_id,
        request={
            "actor": CLIENT,
            "op": OP_WRITE_UPDATE,
            "reverts_event": int(event_id),
            "index": int(index),
            "update": int(update),
            "reason": reason[:500],
        },
        resolved={"recorded_at": fmt_ts(T), "mutations": out, "jobs": jobs},
        at=T,
    )
    if new_event is None:  # pragma: no cover - checked above under the item locks
        raise ToolError("E_VERSION_CONFLICT", "the update was already reverted", status="reverted")
    await actor.apply_mutations(conn, out, new_event, T)
    if jobs:
        await insert_recorded_jobs(conn, jobs, new_event, T)
    return {
        "event_id": new_event,
        "reverts_event": int(event_id),
        "index": int(index),
        "update": int(update),
        "restored": [
            f"v{rv['version_id']}" for r in out if r["op"] == "version_reopen" for rv in r["restored"]
        ],
        "links_superseded": sum(1 for r in out if r["op"] == "link_supersede"),
        "links_restored": sum(1 for r in out if r["op"] == "link_insert"),
    }


__all__ = ["OP_WRITE_UPDATE", "revert_write_update"]
