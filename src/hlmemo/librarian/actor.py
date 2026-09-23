"""The librarian system actor: CC-3 apply-time recheck and mutation application.

The librarian never acts with "all grants". A mutation names the capability it needs; at apply
time, inside the apply transaction, ``recheck`` re-resolves the job's triggering device under the
same ordering rule as a request (shared advisory lock + ``FOR SHARE`` on the device row, spec §2),
reloads its current grants and checks every project the mutation touches:

* ``annotate(P)``: a link ``relates_to|contradicts|supersedes|member_of``; every endpoint's
  ``project_ids ⊆ P`` (P = projects where the device holds ``write`` NOW, intersected with the
  enqueue-time set) and both endpoints pass authz (a) for the device (``device_scope`` visible);
* ``correct(P)``: item home ∈ P and all its ``project_ids ⊆ P`` (the W2b auto-resolve rule);
* ``question(P)``: every subject readable by the device;
* ``librarian_memory``: the reserved ``hlm-librarian`` project only (``memory.write_rule``);
* ``global_experience``: only via an accepted ``promote`` answer (W4a), never at enqueue.

A failed recheck applies nothing; the caller records ``resolved.outcome = "authority_lost"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.resolve import context_from_row, lock_device_access
from hlmemo.core.temporal import fmt_ts, parse_opt_ts, parse_ts
from hlmemo.db import auth_queries as aq
from hlmemo.db import write_queries as q
from hlmemo.librarian.errors import AuthorityLost

ANNOTATE_RELS = frozenset({"relates_to", "contradicts", "supersedes", "member_of"})
CAPABILITY_ROLE = {"annotate": Role.WRITE, "correct": Role.WRITE, "question": Role.READ}


@dataclass(frozen=True, slots=True)
class Endpoint:
    logical_id: int
    version_id: int
    project_id: int
    project_ids: tuple[int, ...]
    device_scope: str
    pinned: bool
    kind: str


async def head_endpoint(conn: AsyncConnection, logical_id: int) -> Endpoint | None:
    """The current head version of ``logical_id`` (greatest current version id, spec §1.1)."""
    cur = await conn.execute(
        """
        SELECT logical_id, version_id, project_id, project_ids, device_scope, pinned, kind
          FROM memory_versions
         WHERE logical_id = %s AND superseded_at = 'infinity' AND status = 'active'
         ORDER BY version_id DESC LIMIT 1
        """,
        (logical_id,),
    )
    r = await cur.fetchone()
    return None if r is None else Endpoint(r[0], r[1], r[2], tuple(r[3]), r[4], r[5], r[6])


async def recheck(conn: AsyncConnection, capabilities: dict[str, Any], client: str) -> AuthContext:
    """Re-resolve the triggering device now (FOR SHARE); ``AuthorityLost`` if it is not trusted."""
    device_id = int(capabilities["trigger_device_id"])
    await lock_device_access(conn, device_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {aq.DEVICE_COLUMNS} FROM devices WHERE device_id = %s FOR SHARE", (device_id,)
        )
        row = await cur.fetchone()
    if row is None or row["status"] != "trusted":
        raise AuthorityLost(f"triggering device {device_id} is no longer trusted")
    grants = await aq.select_active_grants(conn, device_id)
    return context_from_row(row, grants, client)


def allowed(ctx: AuthContext, capabilities: dict[str, Any], capability: str, project_ids: list[int]) -> bool:
    """Enqueue-time set ∩ current grants covers every project."""
    granted = set(capabilities.get(capability) or [])
    role = CAPABILITY_ROLE[capability]
    return all(pid in granted and ctx.has(pid, role) for pid in project_ids)


async def check_link(
    conn: AsyncConnection, ctx: AuthContext, capabilities: dict[str, Any], m: dict[str, Any]
) -> tuple[Endpoint, Endpoint]:
    """``annotate`` check of one ``link_insert`` against the CURRENT endpoints (raises)."""
    if m.get("rel") not in ANNOTATE_RELS:
        raise AuthorityLost(f"rel {m.get('rel')!r} is not an annotate relation")
    src = await head_endpoint(conn, int(m["src_logical_id"]))
    dst = await head_endpoint(conn, int(m["dst_logical_id"]))
    if src is None or dst is None:
        raise AuthorityLost("a link endpoint is no longer current")
    scopes = set(ctx.scope_values())
    for ep in (src, dst):
        if ep.device_scope not in scopes:
            raise AuthorityLost(f"endpoint v{ep.version_id} is not visible to the triggering device")
        if not allowed(ctx, capabilities, "annotate", list(ep.project_ids)):
            raise AuthorityLost(f"annotate not held on every project of v{ep.version_id}")
    return src, dst


async def link_exists(conn: AsyncConnection, src: int, dst: int, rel: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM links WHERE src_logical_id = %s AND dst_logical_id = %s AND rel = %s"
        " AND superseded_at = 'infinity' AND valid_to = 'infinity'",
        (src, dst, rel),
    )
    return await cur.fetchone() is not None


async def materialize_links(
    conn: AsyncConnection, ctx: AuthContext, capabilities: dict[str, Any], mutations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Recheck every ``link_insert`` and resolve it to a concrete, id-allocated link record.

    Raises ``AuthorityLost`` if any mutation fails its capability (then nothing is applied).
    Mutations whose live link already exists are dropped (idempotent annotation).
    """
    out: list[dict[str, Any]] = []
    for m in mutations:
        if m.get("op") != "link_insert":
            raise AuthorityLost(f"unsupported mutation {m.get('op')!r} in W2a")
        src, _dst = await check_link(conn, ctx, capabilities, m)
        if await link_exists(conn, src.logical_id, int(m["dst_logical_id"]), m["rel"]):
            continue
        out.append(
            {
                "op": "link_insert",
                "capability": "annotate",
                "rel": m["rel"],
                "src_logical_id": src.logical_id,
                "dst_logical_id": int(m["dst_logical_id"]),
                "dst_version_id": m.get("dst_version_id"),
                "project_id": src.project_id,
                "project_ids": list(src.project_ids),
                "device_scope": src.device_scope,
                "props": m.get("props") or {},
                "valid_from": m["valid_from"],
                "valid_to": m.get("valid_to"),
                "assessed": m.get("assessed") or {},
            }
        )
    ids = await q.allocate_ids(conn, "links", len(out))
    for rec, link_id in zip(out, ids, strict=True):
        rec["link_id"] = link_id
    return out


async def apply_mutations(
    conn: AsyncConnection, mutations: list[dict[str, Any]], event_id: int, at: datetime
) -> int:
    """Insert recorded mutations (live path after the event row exists, and replay)."""
    n = 0
    for m in mutations:
        if m["op"] == "link_insert":
            await q.insert_link(
                conn,
                q.LinkRow(
                    link_id=int(m["link_id"]),
                    project_id=int(m["project_id"]),
                    project_ids=[int(p) for p in m["project_ids"]],
                    device_scope=m["device_scope"],
                    src_logical_id=int(m["src_logical_id"]),
                    dst_logical_id=int(m["dst_logical_id"]),
                    dst_version_id=m.get("dst_version_id"),
                    rel=m["rel"],
                    props=m.get("props") or {},
                    valid_from=parse_ts(m["valid_from"], field="valid_from"),
                    valid_to=parse_opt_ts(m.get("valid_to"), field="valid_to"),
                    recorded_at=at,
                    source_event_id=event_id,
                ),
            )
            n += 1
        elif m["op"] == "link_supersede":
            n += await q.supersede_links(conn, [int(m["link_id"])], at)
        else:  # pragma: no cover - guarded by materialize_links
            raise ValueError(f"unknown mutation {m['op']!r}")
    return n


async def mark_done_by_key(conn: AsyncConnection, done: dict[str, Any], at: datetime) -> None:
    """Replay: the job whose effect an event records is done, at the recorded completion time and
    attempt count (``resolved.done``), so the jobs projection rebuilds identically."""
    await conn.execute(
        "UPDATE jobs SET status = 'done', done_at = %s, attempts = %s, lease_token = NULL,"
        " lease_until = NULL, last_error = NULL WHERE dedupe_key = %s",
        (
            parse_ts(done.get("done_at") or fmt_ts(at), field="done_at"),
            int(done.get("attempts", 0)),
            done["dedupe_key"],
        ),
    )


# --------------------------------------------------------------------------- stale proposals (Sol #5)
async def is_stale(conn: AsyncConnection, mutation: dict[str, Any]) -> bool:
    """Lock every assessed logical item (the write path's per-item lock, sorted) and compare its
    head with the version the model assessed. A revision since then makes the mutation stale."""
    assessed = {int(k): int(v) for k, v in (mutation.get("assessed") or {}).items()}
    if not assessed:
        return False
    await q.lock_logical_ids(conn, list(assessed))
    for lid, vid in assessed.items():
        ep = await head_endpoint(conn, lid)
        if ep is None or ep.version_id != vid:
            return True
    return False


# --------------------------------------------------------------------------- question rows (CC-3)
async def insert_questions(
    conn: AsyncConnection, rows: list[dict[str, Any]], event_id: int, at: datetime
) -> None:
    """Insert recorded question rows (live path after the event row, and replay)."""
    for r in rows:
        await conn.execute(
            """
            INSERT INTO librarian_questions (question_id, job_id, batch_id, project_id, project_ids, kind,
                                             subject_clues, subject_version_ids, proposal, status,
                                             created_at, source_event_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                r["question_id"],
                int(r["job_id"]),
                r["batch_id"],
                int(r["project_id"]),
                [int(x) for x in r["project_ids"]],
                r["kind"],
                list(r["subject_clues"]),
                [int(x) for x in r["subject_version_ids"]],
                Jsonb(r["proposal"]),
                r.get("status", "open"),
                at,
                event_id,
            ),
        )


async def set_question_status(conn: AsyncConnection, changes: list[dict[str, Any]], at: datetime) -> None:
    """Apply recorded status changes ``{question_id, status, decided_by?}`` (live and replay)."""
    for c in changes:
        await conn.execute(
            "UPDATE librarian_questions SET status = %s, decided_at = %s,"
            " decided_by = COALESCE(%s, decided_by) WHERE question_id = %s",
            (c["status"], at, c.get("decided_by"), c["question_id"]),
        )


def ts(dt: datetime) -> str:
    out = fmt_ts(dt)
    assert out is not None
    return out


__all__ = [
    "ANNOTATE_RELS",
    "Endpoint",
    "allowed",
    "apply_mutations",
    "check_link",
    "insert_questions",
    "is_stale",
    "set_question_status",
    "head_endpoint",
    "link_exists",
    "mark_done_by_key",
    "materialize_links",
    "recheck",
    "ts",
]
