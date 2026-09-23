"""SQL for the W1.5 additions: source ownership, ``code_refs`` and the ``hlm.export`` pages.

Kept apart from ``write_queries``/``read_queries`` so the shared query modules only gain the
``source`` column. Every export read applies the read path's two predicate layers (§4.4):
``authz`` (project membership + device scope) and ``temporal_live`` at the frozen as-of.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.db.read_queries import AUTHZ_MV, TEMPORAL_MV


@dataclass(slots=True)
class SourceOwner:
    source_key: str
    logical_id: int
    head_version_id: int
    project_ids: list[int]
    device_scope: str


async def source_owners(conn: AsyncConnection, project_id: int, source_keys: list[str]) -> list[SourceOwner]:
    """The current logical items holding each key in the project (EXCLUDE ``mv_one_source_owner``
    allows one logical item per key, possibly with several current valid-time segments)."""
    if not source_keys:
        return []
    cur = await conn.execute(
        """
        SELECT DISTINCT ON (source_key, logical_id) source_key, logical_id, version_id, project_ids,
               device_scope
          FROM memory_versions
         WHERE project_id = %s AND source_key = ANY(%s) AND superseded_at = 'infinity'
         ORDER BY source_key, logical_id, version_id DESC
        """,
        (project_id, list(set(source_keys))),
    )
    return [SourceOwner(r[0], r[1], r[2], list(r[3]), r[4]) for r in await cur.fetchall()]


async def insert_code_refs(conn: AsyncConnection, rows: list[tuple[int, str, str | None]]) -> None:
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany("INSERT INTO code_refs (version_id, path, commit) VALUES (%s, %s, %s)", rows)


async def code_refs_of(
    conn: AsyncConnection, version_ids: list[int]
) -> dict[int, list[tuple[str, str | None]]]:
    out: dict[int, list[tuple[str, str | None]]] = {v: [] for v in version_ids}
    if not version_ids:
        return out
    cur = await conn.execute(
        "SELECT version_id, path, commit FROM code_refs WHERE version_id = ANY(%s) ORDER BY version_id, path",
        (list(version_ids),),
    )
    for vid, path, commit in await cur.fetchall():
        out[vid].append((path, commit))
    return out


async def version_source(conn: AsyncConnection, version_id: int) -> dict[str, Any] | None:
    cur = await conn.execute("SELECT source FROM memory_versions WHERE version_id = %s", (version_id,))
    row = await cur.fetchone()
    return None if row is None else row[0]


# --------------------------------------------------------------------------- hlm.export pages
@dataclass(slots=True)
class ExportRow:
    version_id: int
    logical_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    status: str
    title: str
    tags: list[str]
    pinned: bool
    stability: str
    importance: int | None
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    source: dict[str, Any] | None
    body_chars: int
    body_sha256: str
    body: str | None  # None in the manifest view


async def export_rows(
    conn: AsyncConnection,
    *,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
    statuses: list[str],
    kinds: list[str] | None,
    after: tuple[int, int] | None,
    limit: int,
    with_body: bool,
) -> list[ExportRow]:
    """Versions live at ``(valid_at, known_at)`` that pass (a), keyset-paged by
    ``(logical_id, version_id)`` (the project card included; the caller splits it off)."""
    params: dict[str, Any] = {
        "pid": pid,
        "scopes": scopes,
        "valid_at": valid_at,
        "known_at": known_at,
        "statuses": statuses,
        "kinds": kinds,
        "limit": limit,
    }
    where = f"{AUTHZ_MV} AND {TEMPORAL_MV} AND mv.status = ANY(%(statuses)s)"
    if kinds is not None:
        where += " AND (mv.kind = ANY(%(kinds)s) OR mv.kind = 'project_card')"
    if after is not None:
        where += " AND (mv.logical_id, mv.version_id) > (%(a_lid)s, %(a_vid)s)"
        params["a_lid"], params["a_vid"] = after
    body = "mv.body" if with_body else "NULL::text"
    cur = await conn.execute(
        f"""
        SELECT mv.version_id, mv.logical_id, mv.project_ids, mv.device_scope, mv.kind, mv.status,
               mv.title, mv.tags, mv.pinned, mv.stability, mv.importance, mv.valid_from,
               nullif(mv.valid_to, 'infinity'),
               mv.recorded_at, mv.source, length(mv.body),
               encode(sha256(convert_to(mv.body, 'UTF8')), 'hex'), {body}
          FROM memory_versions mv
         WHERE {where}
         ORDER BY mv.logical_id, mv.version_id
         LIMIT %(limit)s
        """,
        params,
    )
    return [ExportRow(*r) for r in await cur.fetchall()]


@dataclass(slots=True)
class ExportLink:
    src_logical_id: int
    rel: str
    dst_logical_id: int
    dst_version_id: int | None


async def export_links(
    conn: AsyncConnection,
    *,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
    src_logical_ids: list[int],
) -> list[ExportLink]:
    """Outgoing edges live at the as-of whose link row passes (a) and whose far endpoint passes (a)
    (the pinned version, else any version of the target) — the drilldown/raw endpoint rule."""
    if not src_logical_ids:
        return []
    cur = await conn.execute(
        f"""
        SELECT l.src_logical_id, l.rel, l.dst_logical_id, l.dst_version_id
          FROM links l
         WHERE l.src_logical_id = ANY(%(srcs)s)
           AND (%(pid)s = ANY(l.project_ids) AND l.device_scope = ANY(%(scopes)s))
           AND (l.valid_from <= %(valid_at)s AND l.valid_to > %(valid_at)s
                AND l.recorded_at <= %(known_at)s AND l.superseded_at > %(known_at)s)
           AND EXISTS (SELECT 1 FROM memory_versions mv
                        WHERE {AUTHZ_MV}
                          AND (CASE WHEN l.dst_version_id IS NOT NULL THEN mv.version_id = l.dst_version_id
                                    ELSE mv.logical_id = l.dst_logical_id END))
         ORDER BY l.src_logical_id, l.rel, l.dst_logical_id, l.link_id
        """,
        {
            "srcs": src_logical_ids,
            "pid": pid,
            "scopes": scopes,
            "valid_at": valid_at,
            "known_at": known_at,
        },
    )
    return [ExportLink(*r) for r in await cur.fetchall()]


__all__ = [
    "ExportLink",
    "ExportRow",
    "SourceOwner",
    "code_refs_of",
    "export_links",
    "export_rows",
    "insert_code_refs",
    "source_owners",
    "version_source",
]
