"""The librarian system actor: CC-3 apply-time recheck and mutation application.

The librarian never acts with "all grants". A mutation names the capability it needs; at apply
time, inside the apply transaction, ``recheck`` re-resolves the job's triggering device under the
same ordering rule as a request (shared advisory lock + ``FOR SHARE`` on the device row, spec §2),
reloads its current grants and checks every project the mutation touches:

* ``annotate(P)``: a link ``relates_to|contradicts|supersedes|member_of`` and a placement signal
  (``version_signals``); every endpoint's ``project_ids ⊆ P`` (P = projects where the device
  holds ``write`` NOW, intersected with the enqueue-time set) and both endpoints pass authz (a)
  for the device (``device_scope`` visible);
* ``correct(P)``: the bi-temporal close of an item (``version_close``: ``valid_to`` set, nothing
  deleted): item home ∈ P and all its ``project_ids ⊆ P`` (the W2b auto-resolve rule);
* ``question(P)``: every subject readable by the device;
* ``librarian_memory``: the reserved ``hlm-librarian`` project only (``memory.write_rule``);
* ``global_experience``: only via an accepted ``promote`` answer (W4a), never at enqueue.

A failed recheck applies nothing; the caller records ``resolved.outcome = "authority_lost"``.

Every mutation is first MATERIALIZED (ids allocated, rows computed) into a record that the event's
``payload.resolved.mutations`` stores; ``apply_mutations`` then writes exactly that record — on the
live path after the event row exists, and on replay (which never recomputes anything).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.resolve import context_from_row, lock_device_access
from hlmemo.core.normalize import normalize
from hlmemo.core.temporal import fmt_ts, overlaps, parse_opt_ts, parse_ts
from hlmemo.db import auth_queries as aq
from hlmemo.db import write_queries as q
from hlmemo.librarian.errors import AuthorityLost

ANNOTATE_RELS = frozenset({"relates_to", "contradicts", "supersedes", "member_of"})
CAPABILITY_ROLE = {"annotate": Role.WRITE, "correct": Role.WRITE, "question": Role.READ}
#: W2c: an unanswered question expires after this long (``hlm.ops``/the worker sweep records it)
QUESTION_TTL = timedelta(days=30)


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
    """Re-resolve the triggering device now (FOR SHARE); ``AuthorityLost`` if it is not trusted
    (revoked, pending, or expired — W0a: expired is treated exactly like revoked)."""
    device_id = int(capabilities["trigger_device_id"])
    await lock_device_access(conn, device_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {aq.DEVICE_COLUMNS} FROM devices WHERE device_id = %s FOR SHARE", (device_id,)
        )
        row = await cur.fetchone()
    if row is None or row["status"] != "trusted" or row.get("expired"):
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


async def check_correct(
    conn: AsyncConnection, ctx: AuthContext, capabilities: dict[str, Any], m: dict[str, Any]
) -> Endpoint:
    """``correct`` check of one ``version_close`` against the CURRENT head (raises)."""
    head = await head_endpoint(conn, int(m["logical_id"]))
    if head is None:
        raise AuthorityLost("the item to close is no longer current")
    if head.device_scope not in set(ctx.scope_values()):
        raise AuthorityLost(f"v{head.version_id} is not visible to the triggering device")
    if head.project_id not in (capabilities.get("correct") or []) or not allowed(
        ctx, capabilities, "correct", list(head.project_ids)
    ):
        raise AuthorityLost(f"correct not held on every project of v{head.version_id}")
    return head


def readable(ctx: AuthContext, capabilities: dict[str, Any], project_ids: list[int]) -> bool:
    """``question(P)``: every project readable now and in the enqueue-time set."""
    return allowed(ctx, capabilities, "question", sorted(set(project_ids)))


async def link_exists(conn: AsyncConnection, src: int, dst: int, rel: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM links WHERE src_logical_id = %s AND dst_logical_id = %s AND rel = %s"
        " AND superseded_at = 'infinity' AND valid_to = 'infinity'",
        (src, dst, rel),
    )
    return await cur.fetchone() is not None


# --------------------------------------------------------------------------- materialization
async def _link_record(conn: AsyncConnection, src: Endpoint, m: dict[str, Any]) -> dict[str, Any] | None:
    if await link_exists(conn, src.logical_id, int(m["dst_logical_id"]), m["rel"]):
        return None  # idempotent annotation: the live link already exists
    return {
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


async def _close_record(conn: AsyncConnection, m: dict[str, Any]) -> tuple[dict[str, Any], list[datetime]]:
    """Plan the bi-temporal close of ``m.logical_id`` at ``m.valid_to`` (the cut): every current
    row overlapping ``[cut, ∞)`` is superseded; the part before the cut survives as a copy with
    ``valid_to = cut`` (spec §1.1 (3) survivor semantics, same content and chunks). Nothing is
    deleted and nothing after the cut is kept: the item stops being valid at the cut."""
    lid = int(m["logical_id"])
    cut = parse_ts(m["valid_to"], field="valid_to")
    rows = await q.current_versions(conn, lid)
    hit = [r for r in rows if overlaps(r.valid_from, r.valid_to, cut, None)]
    if not hit:
        raise AuthorityLost("nothing to close: no current segment overlaps the cut")
    survivors: list[dict[str, Any]] = []
    keep = [r for r in hit if r.valid_from < cut]
    vids = await q.allocate_ids(conn, "memory_versions", len(keep))
    for r, svid in zip(keep, vids, strict=True):
        chunks = await q.chunks_of_version(conn, r.version_id)
        cids = await q.allocate_ids(conn, "chunks", len(chunks))
        survivors.append(
            {
                "version_id": svid,
                "from_version_id": r.version_id,
                "valid_from": fmt_ts(r.valid_from),
                "valid_to": fmt_ts(cut),
                "chunks": [
                    {
                        "chunk_id": cid,
                        "ordinal": c.ordinal,
                        "char_start": c.char_start,
                        "char_end": c.char_end,
                        "e5_tokens": c.e5_tokens,
                    }
                    for c, cid in zip(chunks, cids, strict=True)
                ],
            }
        )
    record = {
        "op": "version_close",
        "capability": m.get("capability", "correct"),
        "logical_id": lid,
        "valid_to": fmt_ts(cut),
        "superseded": sorted(r.version_id for r in hit),
        "survivors": survivors,
        "assessed": m.get("assessed") or {},
    }
    return record, [r.recorded_at for r in hit]


def signal_record(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "signal_upsert",
        "capability": "annotate",
        "version_id": int(m["version_id"]),
        "importance": m.get("importance"),
        "importance_src": m.get("importance_src"),
        "stability_suggested": m.get("stability_suggested"),
        "topic_hint": m.get("topic_hint"),
        "tags_add": list(m.get("tags_add") or []),
    }


async def materialize(
    conn: AsyncConnection,
    ctx: AuthContext | None,
    capabilities: dict[str, Any] | None,
    actions: list[dict[str, Any]],
    planned: dict[str, set[Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[datetime]]:
    """Recheck (``ctx``/``capabilities``; ``None`` = the caller already authorized the actions,
    e.g. ``memory.answer`` against the answering device) and materialize ``actions`` into records
    with allocated ids. Returns ``(records, recorded_at of every row a close supersedes)``.
    Raises ``AuthorityLost`` if any action fails its capability (then nothing is applied).

    ``planned`` (shared across calls that end in ONE event) remembers the links and closes already
    materialized: a repeated link is skipped and a second close of the same logical item is
    skipped, so two proposals can never plan conflicting rows (Sol 41 #3)."""
    planned = planned if planned is not None else {"links": set(), "closed": set()}
    out: list[dict[str, Any]] = []
    superseded_recorded: list[datetime] = []
    for m in actions:
        op = m.get("op")
        if op == "link_insert":
            if ctx is not None and capabilities is not None:
                src, _dst = await check_link(conn, ctx, capabilities, m)
            else:
                src = await head_endpoint(conn, int(m["src_logical_id"]))
                if src is None or await head_endpoint(conn, int(m["dst_logical_id"])) is None:
                    raise AuthorityLost("a link endpoint is no longer current")
            rec = await _link_record(conn, src, m)
            if rec is not None:
                key = (rec["src_logical_id"], rec["dst_logical_id"], rec["rel"])
                if key not in planned["links"]:
                    planned["links"].add(key)
                    out.append(rec)
        elif op == "version_close":
            if ctx is not None and capabilities is not None:
                await check_correct(conn, ctx, capabilities, m)
            if int(m["logical_id"]) in planned["closed"]:
                continue
            rec, recorded = await _close_record(conn, m)
            planned["closed"].add(int(m["logical_id"]))
            out.append(rec)
            superseded_recorded.extend(recorded)
        elif op == "signal_upsert":
            out.append(signal_record(m))
        else:
            raise AuthorityLost(f"unsupported librarian mutation {op!r}")
    link_ids = await q.allocate_ids(conn, "links", sum(1 for r in out if r["op"] == "link_insert"))
    for rec in out:
        if rec["op"] == "link_insert":
            rec["link_id"] = link_ids.pop(0)
    return out, superseded_recorded


async def materialize_links(
    conn: AsyncConnection, ctx: AuthContext, capabilities: dict[str, Any], mutations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """W2a entry point (links only)."""
    for m in mutations:
        if m.get("op") != "link_insert":
            raise AuthorityLost(f"unsupported mutation {m.get('op')!r} in W2a")
    out, _ = await materialize(conn, ctx, capabilities, mutations)
    return out


def close_embed_jobs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``embed`` job descriptors for the survivor versions of ``version_close`` records (the
    worker copies the predecessor's vectors: same text, ``supersedes_version_id`` set). Recorded in
    ``resolved.jobs`` with their ids so a replay re-creates the exact rows."""
    from hlmemo.core.write_service import embed_dedupe_key, embed_job_payload, embedder_descriptor

    embedder = embedder_descriptor()
    jobs = []
    for rec in records:
        if rec["op"] != "version_close":
            continue
        for sv in rec["survivors"]:
            if sv["chunks"]:
                jobs.append(
                    {
                        "kind": "embed",
                        "dedupe_key": embed_dedupe_key(sv["version_id"]),
                        "priority": 5,
                        "payload": embed_job_payload(sv["version_id"], embedder),
                    }
                )
    return jobs


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
        elif m["op"] == "signal_upsert":
            await upsert_signal(conn, m, event_id, at)
            n += 1
        elif m["op"] == "version_close":
            n += await _apply_close(conn, m, event_id, at)
        else:  # pragma: no cover - guarded by materialize
            raise ValueError(f"unknown mutation {m['op']!r}")
    return n


async def upsert_signal(conn: AsyncConnection, m: dict[str, Any], event_id: int, at: datetime) -> None:
    await conn.execute(
        """
        INSERT INTO version_signals (version_id, importance, importance_src, stability_suggested,
                                     topic_hint, tags_add, recorded_at, source_event_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (version_id) DO UPDATE SET
          importance = EXCLUDED.importance, importance_src = EXCLUDED.importance_src,
          stability_suggested = EXCLUDED.stability_suggested, topic_hint = EXCLUDED.topic_hint,
          tags_add = EXCLUDED.tags_add, recorded_at = EXCLUDED.recorded_at,
          source_event_id = EXCLUDED.source_event_id
        """,
        (
            int(m["version_id"]),
            m.get("importance"),
            m.get("importance_src"),
            m.get("stability_suggested"),
            m.get("topic_hint"),
            list(m.get("tags_add") or []),
            at,
            event_id,
        ),
    )


async def _apply_close(conn: AsyncConnection, m: dict[str, Any], event_id: int, at: datetime) -> int:
    sup = [int(v) for v in m["superseded"]]
    if await q.supersede_versions(conn, sup, at) != len(sup):
        raise AuthorityLost("a version to close changed concurrently")
    for sv in m["survivors"]:
        base = await q.get_version(conn, int(sv["from_version_id"]))
        if base is None:  # pragma: no cover - the close record names an existing row
            raise ValueError(f"close survivor base {sv['from_version_id']} missing")
        await q.insert_version(
            conn,
            q.VersionRow(
                version_id=int(sv["version_id"]),
                logical_id=base.logical_id,
                project_id=base.project_id,
                project_ids=list(base.project_ids),
                device_scope=base.device_scope,
                kind=base.kind,
                status=base.status,
                title=base.title,
                body=base.body,
                tags=list(base.tags),
                pinned=base.pinned,
                stability=base.stability,
                importance=base.importance,
                token_count=base.token_count,
                valid_from=parse_ts(sv["valid_from"], field="valid_from"),
                valid_to=parse_ts(sv["valid_to"], field="valid_to"),
                recorded_at=at,
                source_event_id=event_id,
                supersedes_version_id=base.version_id,
                last_access_at=base.last_access_at,
            ),
        )
        await q.insert_chunks(
            conn,
            [
                q.ChunkRow(
                    chunk_id=int(c["chunk_id"]),
                    version_id=int(sv["version_id"]),
                    project_ids=list(base.project_ids),
                    device_scope=base.device_scope,
                    ordinal=int(c["ordinal"]),
                    char_start=int(c["char_start"]),
                    char_end=int(c["char_end"]),
                    text=base.body[int(c["char_start"]) : int(c["char_end"])],
                    text_norm=normalize(base.body[int(c["char_start"]) : int(c["char_end"])]),
                    e5_tokens=int(c["e5_tokens"]),
                )
                for c in sv["chunks"]
            ],
        )
    return 1 + len(m["survivors"])


async def mark_done_by_key(conn: AsyncConnection, done: dict[str, Any], at: datetime) -> None:
    """Replay: the job whose effect an event records is done, at the recorded completion time and
    attempt count (``resolved.done``), so the jobs projection rebuilds identically."""
    run_after = done.get("run_after")
    await conn.execute(
        "UPDATE jobs SET status = 'done', done_at = %s, attempts = %s, lease_token = NULL,"
        " lease_until = NULL, last_error = NULL, run_after = COALESCE(%s, run_after) WHERE dedupe_key = %s",
        (
            parse_ts(done.get("done_at") or fmt_ts(at), field="done_at"),
            int(done.get("attempts", 0)),
            parse_ts(run_after, field="run_after") if run_after else None,
            done["dedupe_key"],
        ),
    )


async def restore_deferred(conn: AsyncConnection, deferred: dict[str, Any]) -> None:
    """Replay: a job the librarian handed back, backed off or failed (``resolved.deferred``) gets
    exactly the recorded status, attempts, run_after and last_error (Sol 38 #6)."""
    await conn.execute(
        "UPDATE jobs SET status = %s, attempts = %s, run_after = %s, last_error = %s,"
        " lease_token = NULL, lease_until = NULL WHERE dedupe_key = %s",
        (
            deferred["status"],
            int(deferred["attempts"]),
            parse_ts(deferred["run_after"], field="run_after"),
            deferred.get("last_error"),
            deferred["dedupe_key"],
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
        expires = r.get("expires_at")
        await conn.execute(
            """
            INSERT INTO librarian_questions (question_id, job_key, batch_id, project_id, project_ids, kind,
                                             subject_clues, subject_version_ids, proposal, status,
                                             created_at, source_event_id, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                r["question_id"],
                str(r["job_key"]),
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
                parse_ts(expires, field="expires_at") if expires else None,
            ),
        )


async def set_question_status(conn: AsyncConnection, changes: list[dict[str, Any]], at: datetime) -> None:
    """Apply recorded status changes ``{question_id, status, decided_by?, answer?}`` (live and
    replay)."""
    for c in changes:
        await conn.execute(
            "UPDATE librarian_questions SET status = %s, decided_at = %s,"
            " decided_by = COALESCE(%s, decided_by), answer = COALESCE(%s, answer) WHERE question_id = %s",
            (
                c["status"],
                at,
                c.get("decided_by"),
                Jsonb(c["answer"]) if c.get("answer") is not None else None,
                c["question_id"],
            ),
        )


async def pending_question_exists(conn: AsyncConnection, kind: str, subject_version_ids: list[int]) -> bool:
    """An open or approved question of ``kind`` over exactly these subject versions exists."""
    cur = await conn.execute(
        "SELECT 1 FROM librarian_questions WHERE kind = %s AND status IN ('open', 'approved')"
        " AND subject_version_ids = %s::bigint[] LIMIT 1",
        (kind, sorted(set(subject_version_ids))),
    )
    return await cur.fetchone() is not None


# --------------------------------------------------------------------------- approval batches (§4b)
BATCH_MAX = 25


async def apply_batch_changes(
    conn: AsyncConnection, changes: list[dict[str, Any]], event_id: int, at: datetime
) -> None:
    """Recorded ``librarian_batches`` changes (live and replay): ``{batch_id, project_id,
    status:"open", created:true}`` inserts a batch; otherwise the batch moves to ``status``
    (``ready`` = full, ``decided`` = owner decision recorded, ``applied``)."""
    for c in changes:
        if c.get("created"):
            await conn.execute(
                "INSERT INTO librarian_batches (batch_id, project_id, status, created_at, source_event_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                (c["batch_id"], int(c["project_id"]), c.get("status", "open"), at, event_id),
            )
            continue
        status = c["status"]
        await conn.execute(
            """
            UPDATE librarian_batches SET status = %(s)s,
                   decided_at = CASE WHEN %(s)s = 'decided' THEN %(at)s ELSE decided_at END,
                   decided_by = CASE WHEN %(s)s = 'decided' THEN %(by)s ELSE decided_by END,
                   applied_at = CASE WHEN %(s)s = 'applied' THEN %(at)s ELSE applied_at END
             WHERE batch_id = %(b)s
            """,
            {"s": status, "at": at, "by": c.get("decided_by"), "b": c["batch_id"]},
        )


async def open_batch(conn: AsyncConnection, project_id: int) -> tuple[str | None, int]:
    """The project's accepting batch (row-locked) and how many open questions it holds."""
    cur = await conn.execute(
        "SELECT batch_id::text FROM librarian_batches WHERE project_id = %s AND status = 'open' FOR UPDATE",
        (project_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None, 0
    cur = await conn.execute(
        "SELECT count(*) FROM librarian_questions WHERE batch_id = %s AND status = 'open'", (row[0],)
    )
    return row[0], int((await cur.fetchone())[0])


def ts(dt: datetime) -> str:
    out = fmt_ts(dt)
    assert out is not None
    return out


__all__ = [
    "ANNOTATE_RELS",
    "BATCH_MAX",
    "QUESTION_TTL",
    "Endpoint",
    "allowed",
    "apply_batch_changes",
    "apply_mutations",
    "check_correct",
    "check_link",
    "close_embed_jobs",
    "head_endpoint",
    "insert_questions",
    "is_stale",
    "link_exists",
    "mark_done_by_key",
    "materialize",
    "materialize_links",
    "open_batch",
    "pending_question_exists",
    "readable",
    "recheck",
    "restore_deferred",
    "set_question_status",
    "signal_record",
    "ts",
    "upsert_signal",
]
