"""Rebuild the projections from ``events`` (PHASE0-SPEC §1.1 *Replay*, G6).

Truncates ``memory_versions``, ``chunks``, ``embeddings``, ``links``, ``jobs`` (``events``,
``devices``, ``projects``, ``device_project_grants`` are kept) and re-applies every event in
``event_id`` order using **only** ``payload.resolved`` — recorded ids, ``recorded_at`` = T,
chunk offsets, resolved link targets — plus validated items in ``payload.resolved.write.items``
(falling back to ``payload.request.items`` for older write events), with bodies sliced by the
recorded ``char_start/char_end``. Never calls ``clock_timestamp()``, ``nextval()`` or the
chunker; identity sequences and ``logical_id_seq`` are ``setval``'d to their maxima at the end.

Each event is applied in two passes, exactly like the live path: every version and chunk row of
the batch first, then every link. A batch may pin ``derived_from "$1"`` from item 0 (forward
reference) or link items 0 ↔ 1 cyclically; inserting item 0's links before item 1's version would
violate ``links.dst_version_id → memory_versions`` (codex C2).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from psycopg import AsyncConnection

from hlmemo.core import NORMALIZER_VERSION
from hlmemo.core.import_contract import code_ref_rows
from hlmemo.core.normalize import normalize
from hlmemo.core.temporal import parse_opt_ts, parse_ts
from hlmemo.db import import_queries as iq
from hlmemo.db import write_queries as q

PROJECTION_TABLES = (
    "librarian_questions",
    "librarian_batches",
    "version_signals",
    "jobs",
    "links",
    "embeddings",
    "chunks",
    "code_refs",  # W1.5 (0007): the describes projection, rebuilt with its versions
    "memory_versions",
)


@dataclass(slots=True)
class RebuildStats:
    events: int = 0
    versions: int = 0
    chunks: int = 0
    links: int = 0
    jobs: int = 0


class ReplayError(RuntimeError):
    pass


def _embed_payload(version_id: int, embedder: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "model": embedder["model"],
        "model_revision": embedder["revision"],
        "preproc_version": embedder["preproc_version"],
        "dims": embedder["dims"],
    }


async def rebuild_projections(conn: AsyncConnection) -> RebuildStats:
    stats = RebuildStats()
    async with conn.transaction():
        await conn.execute(f"TRUNCATE TABLE {', '.join(PROJECTION_TABLES)}")
        cur = await conn.execute(
            "SELECT event_id, project_id, kind, payload, projection_version FROM events ORDER BY event_id"
        )
        events = await cur.fetchall()
        for event_id, project_id, kind, payload, projection_version in events:
            stats.events += 1
            if kind in ("write", "call_the_day"):
                if projection_version != 1:
                    raise ReplayError(
                        f"event {event_id}: unsupported projection_version {projection_version}"
                    )
                await _replay_write(conn, stats, event_id, project_id, payload)
            elif kind == "access":
                await _replay_access(conn, payload)
            elif kind in ("import", "ingest") and "items" in (payload.get("resolved") or {}):
                # write-shaped batches (W1.5 / W3d record the write resolution)
                await _replay_write(conn, stats, event_id, project_id, payload)
            elif kind in SYSTEM_EVENT_KINDS:
                await _replay_system(conn, stats, event_id, payload)
            # device/project/grant/device_minted events have no projection rows
        await _reset_sequences(conn)
    return stats


async def _replay_write(
    conn: AsyncConnection, stats: RebuildStats, event_id: int, project_id: int, payload: dict[str, Any]
) -> None:
    res = payload["resolved"]
    if res.get("normalizer_version", 1) != NORMALIZER_VERSION:
        raise ReplayError(f"event {event_id}: normalizer_version {res.get('normalizer_version')} not in code")
    T = parse_ts(res["recorded_at"], field="resolved.recorded_at")
    src_items = res.get("write", {}).get("items") or payload["request"]["items"]
    embedder = res["embedder"]

    superseded = [vid for it in res["items"] for vid in it.get("supersedes", [])]
    if await q.supersede_versions(conn, superseded, T) != len(superseded):
        raise ReplayError(f"event {event_id}: superseded versions {superseded} not all current")
    sup_links = list(res.get("superseded_links", []))
    if await q.supersede_links(conn, sup_links, T) != len(sup_links):
        raise ReplayError(f"event {event_id}: superseded links {sup_links} not all current")

    for it in res["items"]:
        src = src_items[it["index"]]
        # survivors first: they copy the row they were split from
        for sv in it.get("survivors", []):
            base = await q.get_version(conn, sv["from_version_id"])
            if base is None:
                raise ReplayError(f"event {event_id}: survivor base {sv['from_version_id']} missing")
            await q.insert_version(
                conn,
                q.VersionRow(
                    version_id=sv["version_id"],
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
                    valid_from=parse_ts(sv["valid_from"]),
                    valid_to=parse_opt_ts(sv["valid_to"], field="valid_to"),
                    recorded_at=T,
                    source_event_id=event_id,
                    supersedes_version_id=base.version_id,
                    last_access_at=(
                        parse_opt_ts(sv["last_access_at"], field="last_access_at")
                        if "last_access_at" in sv
                        else base.last_access_at
                    ),
                    source=base.source,
                ),
            )
            await iq.insert_code_refs(
                conn,
                [
                    (sv["version_id"], path, commit)
                    for path, commit in (await iq.code_refs_of(conn, [base.version_id]))[base.version_id]
                ],
            )
            stats.versions += 1
            await q.insert_chunks(
                conn,
                _chunk_rows(
                    sv["chunks"], sv["version_id"], base.body, list(base.project_ids), base.device_scope
                ),
            )
            stats.chunks += len(sv["chunks"])

        body: str = src["body"]
        await q.insert_version(
            conn,
            q.VersionRow(
                version_id=it["version_id"],
                logical_id=it["logical_id"],
                project_id=project_id,
                project_ids=list(it["project_ids"]),
                device_scope=it["device_scope"],
                kind=src["kind"],
                status="active",
                title=src["title"],
                body=body,
                tags=list(src.get("tags", [])),
                pinned=bool(src.get("pinned", False)),
                stability=src.get("stability", "volatile"),
                importance=src.get("importance"),
                token_count=it["token_count"],
                valid_from=parse_ts(it["valid_from"]),
                valid_to=parse_opt_ts(it["valid_to"], field="valid_to"),
                recorded_at=T,
                source_event_id=event_id,
                supersedes_version_id=it.get("supersedes_version_id"),
                source=src.get("source"),
            ),
        )
        await iq.insert_code_refs(conn, code_ref_rows(it["version_id"], src))  # live-path derivation
        stats.versions += 1
        await q.insert_chunks(
            conn,
            _chunk_rows(it["chunks"], it["version_id"], body, list(it["project_ids"]), it["device_scope"]),
        )
        stats.chunks += len(it["chunks"])

    # pass 2: links — every target version of the batch now exists (forward / cyclic refs)
    for sv in res.get("link_survivors", []):
        base = await q.get_link(conn, sv["from_link_id"])
        if base is None:
            raise ReplayError(f"event {event_id}: link survivor base {sv['from_link_id']} missing")
        await q.insert_link(
            conn,
            replace(
                base,
                link_id=sv["link_id"],
                valid_from=parse_ts(sv["valid_from"]),
                valid_to=parse_opt_ts(sv["valid_to"], field="valid_to"),
                recorded_at=T,
                source_event_id=event_id,
                supersedes_link_id=base.link_id,
            ),
        )
        stats.links += 1
    for it in res["items"]:
        for ln in it.get("links", []):
            await q.insert_link(
                conn,
                q.LinkRow(
                    link_id=ln["link_id"],
                    project_id=project_id,
                    project_ids=list(it["project_ids"]),
                    device_scope=it["device_scope"],
                    src_logical_id=it["logical_id"],
                    dst_logical_id=ln["dst_logical_id"],
                    dst_version_id=ln.get("dst_version_id"),
                    rel=ln["rel"],
                    props=ln.get("props", {}),
                    valid_from=parse_ts(ln["valid_from"]),
                    valid_to=parse_opt_ts(ln["valid_to"], field="valid_to"),
                    recorded_at=T,
                    source_event_id=event_id,
                    supersedes_link_id=ln.get("supersedes_link_id"),
                ),
            )
            stats.links += 1

    for job in res.get("jobs", []):
        await q.insert_job(
            conn,
            kind=job.get("kind", "embed"),
            dedupe_key=job["dedupe_key"],
            source_event_id=event_id,
            payload=_embed_payload(job["version_id"], embedder),
            run_after=T,
            created_at=T,
        )
        stats.jobs += 1
    if res.get("librarian_jobs"):  # W2b: librarian_write:<event_id>, recorded with ids
        from hlmemo.librarian.jobs import insert_recorded_jobs

        stats.jobs += await insert_recorded_jobs(conn, list(res["librarian_jobs"]), event_id, T)


#: CC-2 kinds whose ``payload.resolved`` records applied mutations / enqueued jobs (schema_version 2,
#: proposed D-062). ``import``/``ingest`` without write-shaped items land here too.
SYSTEM_EVENT_KINDS = frozenset(
    {"librarian", "question", "answer", "consolidation", "pack_import", "import", "ingest"}
)


async def _replay_system(
    conn: AsyncConnection, stats: RebuildStats, event_id: int, payload: dict[str, Any]
) -> None:
    """System-actor events: ``resolved.mutations`` (ids recorded), ``resolved.questions`` (question
        rows) and ``resolved.question_status`` (decisions, supersession), ``resolved.jobs`` (full
        descriptors, ``run_after = created_at = T``), ``resolved.done`` (the job this event completed,
        with its completion time and attempts), ``resolved.deferred`` (a job handed back or backed off:
    its status, attempts, run_after and last_error). Never calls a provider: the LLM output lives only in
        the audit ``request``."""
    from hlmemo.librarian.actor import (
        apply_batch_changes,
        apply_mutations,
        insert_questions,
        mark_done_by_key,
        restore_deferred,
        set_question_status,
    )
    from hlmemo.librarian.jobs import insert_recorded_jobs

    res = payload.get("resolved") or {}
    if not res.get("recorded_at"):
        return
    T = parse_ts(res["recorded_at"], field="resolved.recorded_at")
    mutations = list(res.get("mutations") or [])
    stats.links += sum(1 for m in mutations if m.get("op") == "link_insert")
    await apply_mutations(conn, mutations, event_id, T)
    await apply_batch_changes(conn, list(res.get("batches") or []), event_id, T)
    await insert_questions(conn, list(res.get("questions") or []), event_id, T)
    await set_question_status(conn, list(res.get("question_status") or []), T)
    jobs = list(res.get("jobs") or [])
    stats.jobs += await insert_recorded_jobs(conn, jobs, event_id, T)
    if res.get("deferred"):  # a handed-back / backed-off / failed job (Sol 38 #6)
        await restore_deferred(conn, res["deferred"])
    if res.get("done"):
        await mark_done_by_key(conn, res["done"], T)


def _chunk_rows(
    recorded: list[dict[str, Any]], version_id: int, body: str, project_ids: list[int], device_scope: str
) -> list[q.ChunkRow]:
    rows: list[q.ChunkRow] = []
    for c in recorded:
        text = body[c["char_start"] : c["char_end"]]
        rows.append(
            q.ChunkRow(
                chunk_id=c["chunk_id"],
                version_id=version_id,
                project_ids=project_ids,
                device_scope=device_scope,
                ordinal=c["ordinal"],
                char_start=c["char_start"],
                char_end=c["char_end"],
                text=text,
                text_norm=normalize(text),
                e5_tokens=c["e5_tokens"],
            )
        )
    return rows


async def _replay_access(conn: AsyncConnection, payload: dict[str, Any]) -> None:
    """``access`` events (drilldown/raw) replay ``last_access_at`` from their resolved payload."""
    res = payload.get("resolved", {})
    at_raw = res.get("recorded_at")
    version_ids = res.get("version_ids") or []
    if at_raw and version_ids:
        await q.touch_last_access(conn, list(version_ids), parse_ts(at_raw, field="resolved.recorded_at"))


async def _reset_sequences(conn: AsyncConnection) -> None:
    for table, col in (
        ("memory_versions", "version_id"),
        ("chunks", "chunk_id"),
        ("links", "link_id"),
        ("jobs", "job_id"),
    ):
        await conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'),"
            f" COALESCE((SELECT max({col}) FROM {table}), 1),"
            f" (SELECT max({col}) FROM {table}) IS NOT NULL)"
        )
    await conn.execute(
        """
        SELECT setval('logical_id_seq', GREATEST(m.mx, 1), m.mx IS NOT NULL)
        FROM (SELECT max(x) AS mx FROM (SELECT logical_id AS x FROM memory_versions
                                        UNION ALL SELECT card_logical_id FROM projects) u) m
        """
    )


__all__ = ["PROJECTION_TABLES", "SYSTEM_EVENT_KINDS", "RebuildStats", "ReplayError", "rebuild_projections"]
