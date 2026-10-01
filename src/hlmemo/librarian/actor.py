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
  deleted): item home ∈ P and all its ``project_ids ⊆ P`` (the W2b auto-resolve rule). D-118: the
  writer's own updates (``core/write_updates.py``) record a ``version_close`` or a span revision
  (``version_revise``) through the same record/apply path; their reversal compensates with
  ``version_reopen`` (``librarian/reversal.py``);
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
from hlmemo.core.temporal import fmt_ts, overlaps, parse_opt_ts, parse_ts, surviving_segments
from hlmemo.db import auth_queries as aq
from hlmemo.db import librarian_queries as lq
from hlmemo.db import write_queries as q
from hlmemo.librarian.errors import AuthorityLost, RevisionRefused

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


async def action_projects(conn: AsyncConnection, actions: list[dict[str, Any]]) -> set[int]:
    """Every project an action set touches, from the CURRENT rows (links: both endpoints'
    projects; close: the item's projects; widen: the item's projects + the added ones). The role
    of EACH is checked before anything is applied (D-074, Sol 49)."""
    out: set[int] = set()
    lids: set[int] = set()
    for a in actions:
        out.update(int(p) for p in a.get("project_ids") or [])
        out.update(int(p) for p in a.get("dst_project_ids") or [])
        out.update(int(p) for p in a.get("add_project_ids") or [])
        for key in ("src_logical_id", "dst_logical_id", "logical_id"):
            if a.get(key) is not None:
                lids.add(int(a[key]))
    if lids:
        cur = await conn.execute(
            "SELECT DISTINCT unnest(project_ids) FROM memory_versions"
            " WHERE logical_id = ANY(%s) AND superseded_at = 'infinity'",
            (sorted(lids),),
        )
        out.update(int(r[0]) for r in await cur.fetchall())
    return out


def action_logical_ids(actions: list[dict[str, Any]]) -> list[int]:
    """Every logical item an action set touches (assessed subjects, link endpoints, closes, the
    widened item), sorted: the per-item locks every apply path takes before its rechecks."""
    out: set[int] = set()
    for a in actions:
        out.update(int(k) for k in a.get("assessed") or {})
        out.update(int(a[f]) for f in ("src_logical_id", "dst_logical_id", "logical_id") if a.get(f))
    return sorted(out)


#: the question status reason of a proposal the CURRENT cross-project policy forbids (Sol 54 #2)
POLICY_EXCLUDED = "policy_excluded"


async def policy_blocked(conn: AsyncConnection, actions: list[dict[str, Any]], projects: Any = ()) -> bool:
    """Apply-time recheck of ``policy.librarian_cross_project`` (e2e 2026-09-24 #2, Sol 54 #2): True
    if the projects the actions touch on the CURRENT rows (``action_projects``) plus ``projects``
    (the question's own) relate an excluded project to anything else (``relation_allowed``). The
    project rows are read ``FOR SHARE``: a policy change commits before this read or waits for the
    caller's transaction. Every apply path calls it after its own locks, before materializing."""
    from hlmemo.librarian.candidates import relation_allowed

    touched = await action_projects(conn, actions) | {int(p) for p in projects}
    if len(touched) < 2:
        return False
    return not relation_allowed(touched, await lq.cross_project_excluded(conn, touched, lock=True))


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


#: D-118 write-time updates reuse the close unchanged (a concrete ``valid_to`` cut)
close_record = _close_record


# --------------------------------------------------------------------------- span revision (D-118)
def _revise_cut(m: dict[str, Any], head: Any, now: datetime) -> tuple[datetime, str]:
    """D-110 cut of a span revision: the trusted effective date when it lies inside the head's
    validity and is not in the future (``valid_from < date ≤ now``), else ``now`` (the write's
    clock). D-118 uses it for both modes (review 76 #1: the cut is chosen against the TARGETED
    head only)."""
    if m.get("cut") == "effective_date" and m.get("valid_from"):
        eff = parse_ts(m["valid_from"], field="valid_from")
        if head.valid_from < eff <= now:
            return eff, "effective_date"
    return now, "approval"


revise_cut = _revise_cut


def _chunk_json(chunks: list[Any], ids: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": cid,
            "ordinal": c.ordinal,
            "char_start": c.char_start,
            "char_end": c.char_end,
            "e5_tokens": c.e5_tokens,
        }
        for c, cid in zip(chunks, ids, strict=True)
    ]


async def revise_build(
    conn: AsyncConnection,
    head: Any,
    chk: Any,
    *,
    cut: datetime,
    rule: str,
    from_logical_id: int | None,
    from_version_id: int | None,
    capability: str = "correct",
    assessed: dict[str, int] | None = None,
    by: str = "writer",
) -> tuple[dict[str, Any], list[datetime]]:
    """The ``version_revise`` record of a checked span revision of ``head`` (the caller holds the
    item lock and has run the guards: ``chk``, ``librarian/revise.check``) at ``cut``: ids
    allocated, the new body chunked and metered by the write path. The record holds:

    * ``superseded`` = the old head; ``survivor`` = its copy for ``[valid_from, cut)`` (same text,
      chunks, provenance); ``version`` = the new head for ``[cut, ∞)`` whose body is the old one with
      ONLY ``span`` replaced by ``replacement`` (``body_sha256`` recorded; chunk offsets recorded so
      replay never re-chunks);
    * the item's pinned self-``supersedes`` link (new version → old version, ``dst_version_id``,
      ``props.scope = part``); an earlier self-link segment reaching past the cut is superseded and
      keeps its part before the cut (``links_superseded`` / ``link_survivors``).

    D-118 (``by=writer``): the replacing item is written in the same event, so ``from_*`` are
    filled in by the write path once its ids exist. Returns ``(record, recorded_at of every
    row/link it supersedes)``; ``RevisionRefused`` when the cut is not inside the head's validity."""
    from hlmemo.core.write_service import default_deps
    from hlmemo.librarian import revise as rv

    assert chk.start is not None and chk.end is not None
    lid = int(head.logical_id)
    if not head.valid_from < cut:
        raise RevisionRefused("cut_outside_validity")
    body = rv.apply_span(head.body, chk.start, chk.end, chk.replacement)
    deps = default_deps()
    new_chunks = deps.chunker.chunk(body)
    token_count = deps.meter.count_text(body)
    old_chunks = await q.chunks_of_version(conn, head.version_id)
    selfs = [
        ln
        for ln in await q.current_links_from(conn, lid, valid_from=cut, valid_to=None)
        if ln.dst_logical_id == lid and ln.rel == "supersedes"
    ]
    link_survivors = [
        (ln, seg) for ln in selfs for seg in surviving_segments(ln.valid_from, ln.valid_to, cut, None)
    ]
    svid, nvid = await q.allocate_ids(conn, "memory_versions", 2)  # the new version is the head
    cids = await q.allocate_ids(conn, "chunks", len(old_chunks) + len(new_chunks))
    link_ids = await q.allocate_ids(conn, "links", len(link_survivors) + 1)
    record: dict[str, Any] = {
        "op": rv.OP,
        "capability": capability,
        "logical_id": lid,
        "base_version_id": head.version_id,
        "superseded": [head.version_id],
        "span": [chk.start, chk.end],
        "old_span": head.body[chk.start : chk.end],
        "replacement": chk.replacement,
        "body_sha256": rv.sha256_text(body),
        "from_logical_id": from_logical_id,
        "from_version_id": from_version_id,
        "valid_from": fmt_ts(cut),
        "cut_rule": rule,
        "chunker": deps.chunker_descriptor(),
        "survivor": {
            "version_id": svid,
            "valid_from": fmt_ts(head.valid_from),
            "valid_to": fmt_ts(cut),
            "chunks": _chunk_json(old_chunks, cids[: len(old_chunks)]),
        },
        "version": {
            "version_id": nvid,
            "valid_from": fmt_ts(cut),
            "valid_to": None,
            "token_count": token_count,
            "chunks": _chunk_json(new_chunks, cids[len(old_chunks) :]),
        },
        "links_superseded": [ln.link_id for ln in selfs],
        "link_survivors": [
            {"link_id": link_ids[k], "from_link_id": ln.link_id, **seg.as_json()}
            for k, (ln, seg) in enumerate(link_survivors)
        ],
        "link": {
            "link_id": link_ids[-1],
            "project_id": head.project_id,
            "project_ids": list(head.project_ids),
            "device_scope": head.device_scope,
            "dst_version_id": head.version_id,
            "props": {
                "by": by,
                "relation": "revises",
                "scope": "part",
                "quote": head.body[chk.start : chk.end],
                "replacement": chk.replacement,
                "from_logical_id": from_logical_id,
                "from_version_id": from_version_id,
            },
            "valid_from": fmt_ts(cut),
            "valid_to": None,
        },
        "assessed": dict(assessed or {}),
    }
    return record, [head.recorded_at, *(ln.recorded_at for ln in selfs)]


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
    skipped, so two proposals can never plan conflicting rows (Sol 44 #3)."""
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
    """The ``embed`` job descriptors for the survivor versions of ``version_close`` records, the
    restored rows of ``version_reopen`` records and both new rows of a ``version_revise`` (the
    worker copies the predecessor's vectors where a chunk's text is unchanged). Recorded in
    ``resolved.jobs`` with their ids so a replay re-creates the exact rows."""
    from hlmemo.core.write_service import embed_dedupe_key, embed_job_payload, embedder_descriptor

    embedder = embedder_descriptor()
    jobs = []
    for rec in records:
        if rec["op"] == "version_close":
            rows = rec["survivors"]
        elif rec["op"] == "version_reopen":
            rows = rec["restored"]
        elif rec["op"] == "version_revise":
            rows = [rec["survivor"], rec["version"]]
        else:
            continue
        for sv in rows:
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
                    # D-118: a link a reversal restores names the one it restores (else None)
                    supersedes_link_id=m.get("supersedes_link_id"),
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
        elif m["op"] == "version_revise":  # D-118: the writer's span revision
            n += await _apply_revise(conn, m, event_id, at)
        elif m["op"] == "version_reopen":  # D-118: the compensation of a close or a revision
            n += await _apply_reopen(conn, m, event_id, at)
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
    """Apply a recorded ``version_close``. A D-118 writer close (``keep_source``) keeps the
    survivor's ``source`` and ``code_refs`` like a write-path survivor; a librarian close record
    (no flag) is applied exactly as before, so older events replay byte-identically."""
    keep = bool(m.get("keep_source"))
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
                source=base.source if keep else None,
            ),
        )
        if keep:
            await _copy_code_refs(conn, int(sv["version_id"]), base.version_id)
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


async def _copy_code_refs(conn: AsyncConnection, version_id: int, base_version_id: int) -> None:
    """A copied row keeps its base's ``code_refs`` (the W1.5 describes projection), exactly as a
    write-path survivor does (``survivor_code_refs``)."""
    from hlmemo.core.import_contract import survivor_code_refs
    from hlmemo.db import import_queries as iq

    await iq.insert_code_refs(conn, await survivor_code_refs(conn, [(version_id, base_version_id)]))


async def _apply_revise(conn: AsyncConnection, m: dict[str, Any], event_id: int, at: datetime) -> int:
    """Apply a recorded ``version_revise`` (live and replay): supersede the old head, insert its
    survivor and the revised version (body rebuilt from the base row + the recorded span and
    replacement, checked against ``body_sha256``; chunks sliced at the recorded offsets; source and
    code_refs kept), then the self-link segments."""
    from dataclasses import replace

    from hlmemo.librarian import revise as rv

    sup = [int(v) for v in m["superseded"]]
    if await q.supersede_versions(conn, sup, at) != len(sup):
        raise AuthorityLost("a version to revise changed concurrently")
    base = await q.get_version(conn, int(m["base_version_id"]))
    if base is None:  # pragma: no cover - the record names an existing row
        raise ValueError(f"revision base {m['base_version_id']} missing")
    start, end = int(m["span"][0]), int(m["span"][1])
    if base.body[start:end] != m["old_span"]:
        raise ValueError(f"revision of v{base.version_id}: the recorded span does not match")
    body = rv.apply_span(base.body, start, end, m["replacement"])
    if rv.sha256_text(body) != m["body_sha256"]:
        raise ValueError(f"revision of v{base.version_id}: body_sha256 mismatch")
    sv, nv = m["survivor"], m["version"]
    for rec, text, vf, vt, tokens, access in (
        (sv, base.body, sv["valid_from"], sv["valid_to"], base.token_count, base.last_access_at),
        (nv, body, nv["valid_from"], nv.get("valid_to"), int(nv["token_count"]), None),
    ):
        vid = int(rec["version_id"])
        await q.insert_version(
            conn,
            q.VersionRow(
                version_id=vid,
                logical_id=base.logical_id,
                project_id=base.project_id,
                project_ids=list(base.project_ids),
                device_scope=base.device_scope,
                kind=base.kind,
                status=base.status,
                title=base.title,
                body=text,
                tags=list(base.tags),
                pinned=base.pinned,
                stability=base.stability,
                importance=base.importance,
                token_count=tokens,
                valid_from=parse_ts(vf, field="valid_from"),
                valid_to=parse_opt_ts(vt, field="valid_to"),
                recorded_at=at,
                source_event_id=event_id,
                supersedes_version_id=base.version_id,
                last_access_at=access,
                source=base.source,  # the provenance is kept (byte-identical)
            ),
        )
        await _copy_code_refs(conn, vid, base.version_id)
        await q.insert_chunks(
            conn,
            [
                q.ChunkRow(
                    chunk_id=int(c["chunk_id"]),
                    version_id=vid,
                    project_ids=list(base.project_ids),
                    device_scope=base.device_scope,
                    ordinal=int(c["ordinal"]),
                    char_start=int(c["char_start"]),
                    char_end=int(c["char_end"]),
                    text=text[int(c["char_start"]) : int(c["char_end"])],
                    text_norm=normalize(text[int(c["char_start"]) : int(c["char_end"])]),
                    e5_tokens=int(c["e5_tokens"]),
                )
                for c in rec["chunks"]
            ],
        )
    links_sup = [int(x) for x in m.get("links_superseded") or []]
    if await q.supersede_links(conn, links_sup, at) != len(links_sup):
        raise AuthorityLost("a self-link of the revised item changed concurrently")
    for ls in m.get("link_survivors") or []:
        old = await q.get_link(conn, int(ls["from_link_id"]))
        if old is None:  # pragma: no cover - the record names an existing link
            raise ValueError(f"link survivor base {ls['from_link_id']} missing")
        await q.insert_link(
            conn,
            replace(
                old,
                link_id=int(ls["link_id"]),
                valid_from=parse_ts(ls["valid_from"], field="valid_from"),
                valid_to=parse_opt_ts(ls.get("valid_to"), field="valid_to"),
                recorded_at=at,
                source_event_id=event_id,
                supersedes_link_id=old.link_id,
            ),
        )
    ln = m["link"]
    await q.insert_link(
        conn,
        q.LinkRow(
            link_id=int(ln["link_id"]),
            project_id=int(ln["project_id"]),
            project_ids=[int(p) for p in ln["project_ids"]],
            device_scope=ln["device_scope"],
            src_logical_id=base.logical_id,
            dst_logical_id=base.logical_id,
            dst_version_id=int(ln["dst_version_id"]),
            rel="supersedes",
            props=ln.get("props") or {},
            valid_from=parse_ts(ln["valid_from"], field="valid_from"),
            valid_to=parse_opt_ts(ln.get("valid_to"), field="valid_to"),
            recorded_at=at,
            source_event_id=event_id,
        ),
    )
    return 3 + len(links_sup) + len(m.get("link_survivors") or [])


async def _apply_reopen(conn: AsyncConnection, m: dict[str, Any], event_id: int, at: datetime) -> int:
    """Apply a recorded ``version_reopen`` (live and replay): supersede the rows a close or a
    revision left and insert the restored copies of the rows it had superseded (content, chunks,
    source and code_refs of the original)."""
    sup = [int(v) for v in m["superseded"]]
    if await q.supersede_versions(conn, sup, at) != len(sup):
        raise AuthorityLost("a version to reopen changed concurrently")
    for rv in m["restored"]:
        base = await q.get_version(conn, int(rv["from_version_id"]))
        if base is None:  # pragma: no cover - the record names an existing row
            raise ValueError(f"reopen base {rv['from_version_id']} missing")
        await q.insert_version(
            conn,
            q.VersionRow(
                version_id=int(rv["version_id"]),
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
                valid_from=parse_ts(rv["valid_from"], field="valid_from"),
                valid_to=parse_opt_ts(rv.get("valid_to"), field="valid_to"),
                recorded_at=at,
                source_event_id=event_id,
                supersedes_version_id=base.version_id,
                last_access_at=base.last_access_at,
                source=base.source,
            ),
        )
        await _copy_code_refs(conn, int(rv["version_id"]), base.version_id)
        await q.insert_chunks(
            conn,
            [
                q.ChunkRow(
                    chunk_id=int(c["chunk_id"]),
                    version_id=int(rv["version_id"]),
                    project_ids=list(base.project_ids),
                    device_scope=base.device_scope,
                    ordinal=int(c["ordinal"]),
                    char_start=int(c["char_start"]),
                    char_end=int(c["char_end"]),
                    text=base.body[int(c["char_start"]) : int(c["char_end"])],
                    text_norm=normalize(base.body[int(c["char_start"]) : int(c["char_end"])]),
                    e5_tokens=int(c["e5_tokens"]),
                )
                for c in rv["chunks"]
            ],
        )
    return len(sup) + len(m["restored"])


# --------------------------------------------------------------------------- reversal (D-118)
#: the ops whose events RESTORE rows as copies (a reversal): only their recorded copies stand for
#: the rows they restored (never an ordinary write that happens to look the same)
_REVERSAL_OPS = ["revert_write_update"]
#: the reversal ops that restore superseded self-links as copies (``link_insert`` naming the original
#: in ``supersedes_link_id``)
_LINK_RESTORING_OPS = ["revert_write_update"]


async def _live_version(conn: AsyncConnection, logical_id: int, version_id: int) -> int | None:
    """The CURRENT row that stands for ``version_id``: itself while current, else the copy a
    REVERSAL restored of it — the ``restored`` row of a recorded ``version_reopen`` whose
    ``from_version_id`` is it — transitively; ``None`` when neither is current (a later revision,
    close or write replaced it)."""
    vid = int(version_id)
    for _ in range(16):
        cur = await conn.execute(
            "SELECT superseded_at = 'infinity' FROM memory_versions"
            " WHERE version_id = %s AND logical_id = %s",
            (vid, int(logical_id)),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if row[0]:
            return vid
        cur = await conn.execute(
            """
            SELECT (r->>'version_id')::bigint
              FROM events e, jsonb_array_elements(e.payload->'resolved'->'mutations') m,
                   jsonb_array_elements(m->'restored') r
             WHERE e.kind = 'librarian' AND e.payload->'request'->>'op' = ANY(%s)
               AND m->>'op' = 'version_reopen' AND (m->>'logical_id')::bigint = %s
               AND (r->>'from_version_id')::bigint = %s
             ORDER BY 1 DESC LIMIT 1
            """,
            (_REVERSAL_OPS, int(logical_id), vid),
        )
        copy = await cur.fetchone()
        if copy is None:
            return None
        vid = int(copy[0])
    return None  # pragma: no cover - a reversal chain this deep does not exist


async def _live_link(conn: AsyncConnection, link_id: int) -> q.LinkRow | None:
    """The LIVE link row that stands for ``link_id``: itself, else the copy a reversal restored of
    it (its recorded ``link_insert`` names it as ``supersedes_link_id``), transitively."""
    lk = int(link_id)
    for _ in range(16):
        row = await q.get_link(conn, lk)
        if row is None:
            return None
        cur = await conn.execute("SELECT superseded_at = 'infinity' FROM links WHERE link_id = %s", (lk,))
        (live,) = await cur.fetchone()
        if live:
            return row
        cur = await conn.execute(
            """
            SELECT (m->>'link_id')::bigint
              FROM events e, jsonb_array_elements(e.payload->'resolved'->'mutations') m
             WHERE e.kind = 'librarian' AND e.payload->'request'->>'op' = ANY(%s)
               AND m->>'op' = 'link_insert' AND (m->>'supersedes_link_id')::bigint = %s
             ORDER BY 1 DESC LIMIT 1
            """,
            (_LINK_RESTORING_OPS, lk),
        )
        copy = await cur.fetchone()
        if copy is None:
            return None
        lk = int(copy[0])
    return None  # pragma: no cover


async def unrevise_records(
    conn: AsyncConnection, rev: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[datetime]] | str:
    """The COMPENSATION of an applied ``version_revise`` record (nothing deleted), built from
    existing ops so replay needs nothing new: ``version_reopen`` supersedes the revision's survivor
    and revised version (or the copies a later reversal restored of them) and restores a copy of
    the pre-revision head with its ORIGINAL validity, text, chunks, source and code_refs;
    ``link_supersede`` ends the revision's self-link and its link survivors; ``link_insert``
    restores the self-links the revision had superseded (``supersedes_link_id`` names the original,
    so a later reversal finds them). The caller holds the item lock.

    Refused (returns the reason) when a LATER change depends on it: the revised version is no
    longer the item's head (a later revision, close or write), a row of the revision is no longer
    current, a current segment was recorded after it, or a link of the revision is not live."""
    lid = int(rev["logical_id"])
    rows = await q.current_versions(conn, lid)
    current = {r.version_id: r for r in rows}
    sv = await _live_version(conn, lid, int(rev["survivor"]["version_id"]))
    nv = await _live_version(conn, lid, int(rev["version"]["version_id"]))
    if sv is None or nv is None or nv not in current or sv not in current or max(current) != nv:
        return "a later revision or change depends on this revision"
    if any(r.recorded_at > current[nv].recorded_at for v, r in current.items() if v not in (sv, nv)):
        return "a later revision or change depends on this revision"  # pragma: no cover
    base = await q.get_version(conn, int(rev["base_version_id"]))
    if base is None:  # pragma: no cover - the record names a real row
        return "the pre-revision version is missing"
    link_rows: list[q.LinkRow] = []
    for k in [int(rev["link"]["link_id"]), *(int(x["link_id"]) for x in rev.get("link_survivors") or [])]:
        live = await _live_link(conn, k)
        if live is None:
            return "a link of the revision is no longer live"
        link_rows.append(live)
    (vid,) = await q.allocate_ids(conn, "memory_versions", 1)
    chunks = await q.chunks_of_version(conn, base.version_id)
    cids = await q.allocate_ids(conn, "chunks", len(chunks))
    restored_links = [await q.get_link(conn, int(x)) for x in rev.get("links_superseded") or []]
    link_ids = await q.allocate_ids(conn, "links", len(restored_links))
    records: list[dict[str, Any]] = [
        {
            "op": "version_reopen",
            "capability": "correct",
            "logical_id": lid,
            "superseded": sorted({sv, nv}),
            "restored": [
                {
                    "version_id": vid,
                    "from_version_id": base.version_id,
                    "valid_from": fmt_ts(base.valid_from),
                    "valid_to": fmt_ts(base.valid_to),
                    "chunks": _chunk_json(chunks, cids),
                }
            ],
            "reverts_revision": int(rev["version"]["version_id"]),
        },
        *({"op": "link_supersede", "link_id": ln.link_id, "rel": ln.rel} for ln in link_rows),
    ]
    for old, new_id in zip(restored_links, link_ids, strict=True):
        if old is None:  # pragma: no cover - the record names a real link
            return "a superseded self-link is missing"
        records.append(
            {
                "op": "link_insert",
                "capability": "annotate",
                "rel": old.rel,
                "link_id": new_id,
                "src_logical_id": old.src_logical_id,
                "dst_logical_id": old.dst_logical_id,
                "dst_version_id": old.dst_version_id,
                "project_id": old.project_id,
                "project_ids": list(old.project_ids),
                "device_scope": old.device_scope,
                "props": dict(old.props or {}),
                "valid_from": fmt_ts(old.valid_from),
                "valid_to": fmt_ts(old.valid_to),
                "supersedes_link_id": old.link_id,
                "assessed": {},
            }
        )
    recorded = [current[sv].recorded_at, current[nv].recorded_at, *(ln.recorded_at for ln in link_rows)]
    return records, recorded


async def reopen_record(
    conn: AsyncConnection, close_rec: dict[str, Any], closed_at: datetime
) -> tuple[dict[str, Any], list[datetime]] | None:
    """The COMPENSATION of an applied ``version_close`` record (nothing is deleted): the close's
    survivor rows are superseded and every row the close superseded is restored as a new version
    with its ORIGINAL validity, content, chunks and provenance. Version-checked against the state
    the close left (``closed_at`` = its event's recorded_at): every survivor is still current, and
    every OTHER current segment predates the close (a segment the close never touched) — a revision
    since the close makes it ``None`` (nothing can be reverted automatically). Returns ``(record,
    recorded_at of the rows it supersedes)``."""
    lid = int(close_rec["logical_id"])
    rows = await q.current_versions(conn, lid)
    survivors = sorted(int(sv["version_id"]) for sv in close_rec["survivors"])
    current = {r.version_id: r for r in rows}
    if not set(survivors) <= set(current) or any(
        r.recorded_at >= closed_at for v, r in current.items() if v not in survivors
    ):
        return None
    rows = [current[v] for v in survivors]  # only the survivors are superseded
    originals = [await q.get_version(conn, int(v)) for v in close_rec["superseded"]]
    if any(o is None for o in originals):  # pragma: no cover - the close record names real rows
        return None
    vids = await q.allocate_ids(conn, "memory_versions", len(originals))
    restored: list[dict[str, Any]] = []
    for o, vid in zip(originals, vids, strict=True):
        assert o is not None
        chunks = await q.chunks_of_version(conn, o.version_id)
        cids = await q.allocate_ids(conn, "chunks", len(chunks))
        restored.append(
            {
                "version_id": vid,
                "from_version_id": o.version_id,
                "valid_from": fmt_ts(o.valid_from),
                "valid_to": fmt_ts(o.valid_to),  # None = infinity (the usual open item)
                "chunks": _chunk_json(chunks, cids),
            }
        )
    record = {
        "op": "version_reopen",
        "capability": "correct",
        "logical_id": lid,
        "superseded": survivors,
        "restored": restored,
        "reverts_cut": close_rec.get("valid_to"),
    }
    return record, [r.recorded_at for r in rows]


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
    """A pending (open, approved or accepted_pending) question of ``kind`` over exactly these
    subject versions exists."""
    cur = await conn.execute(
        "SELECT 1 FROM librarian_questions WHERE kind = %s"
        " AND status IN ('open', 'approved', 'accepted_pending')"
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
    "close_record",
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
    "reopen_record",
    "restore_deferred",
    "revise_build",
    "revise_cut",
    "set_question_status",
    "signal_record",
    "ts",
    "unrevise_records",
    "upsert_signal",
]
