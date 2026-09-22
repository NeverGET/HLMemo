"""SQL for the write path and projection replay (PHASE0-SPEC §1, §1.1, §3).

Every insert takes explicit ids (``OVERRIDING SYSTEM VALUE``): the write service pre-allocates
them from the identity sequences so ``payload.resolved`` is complete *before* the event row is
written, and ``db/replay.py`` re-inserts the recorded ids without touching any sequence.
Open ends travel as ``None`` (→ ``'infinity'``) because psycopg cannot load an infinite
``timestamptz`` into ``datetime``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from hlmemo.config import get_settings

INF = "infinity"


# --------------------------------------------------------------------------- row types
@dataclass(slots=True)
class VersionRow:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    status: str
    title: str
    body: str
    tags: list[str]
    pinned: bool
    stability: str
    importance: int | None
    token_count: int
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    source_event_id: int
    supersedes_version_id: int | None = None
    last_access_at: datetime | None = None


@dataclass(slots=True)
class ChunkRow:
    chunk_id: int
    version_id: int
    project_ids: list[int]
    device_scope: str
    ordinal: int
    char_start: int
    char_end: int
    text: str
    text_norm: str
    e5_tokens: int


@dataclass(slots=True)
class LinkRow:
    link_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    src_logical_id: int
    dst_logical_id: int
    dst_version_id: int | None
    rel: str
    props: dict[str, Any]
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    source_event_id: int
    supersedes_link_id: int | None = None


@dataclass(slots=True)
class ProjectRef:
    project_id: int
    slug: str
    name: str
    card_logical_id: int
    archived: bool = False


@dataclass(slots=True)
class EventRef:
    event_id: int
    kind: str
    payload_sha256: str
    result: dict[str, Any] | None
    payload: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- locks & lookups
async def _advisory_lock(conn: AsyncConnection, query: str, params: tuple[Any, ...]) -> None:
    """Let serialization waits use the request budget, then restore ordinary SQL limits.

    The middleware supplies the remaining request deadline; direct service callers use the
    configured request budget. Its enclosing deadline still bounds a sequence of lock waits.
    ``set_config(..., true)`` is parameterized SET LOCAL and never changes pooled defaults.
    """
    cur = await conn.execute(
        "SELECT current_setting('lock_timeout'), current_setting('statement_timeout'),"
        " current_setting('hlmemo.request_db_timeout_ms', true)"
    )
    lock_timeout, statement_timeout, request_ms = await cur.fetchone()
    wait_ms = max(1, int(float(request_ms or get_settings().request_db_timeout_s * 1000)))
    await conn.execute(
        "SELECT set_config('lock_timeout', %s, true), set_config('statement_timeout', %s, true)",
        (f"{wait_ms}ms", f"{wait_ms}ms"),
    )
    try:
        await conn.execute(query, params)
    finally:
        # An aborted transaction is rolled back by the caller; issuing SQL there masks the
        # original timeout/cancellation and cannot restore anything before the rollback.
        if not conn.closed and conn.info.transaction_status == TransactionStatus.INTRANS:
            await conn.execute(
                "SELECT set_config('lock_timeout', %s, true), set_config('statement_timeout', %s, true)",
                (lock_timeout, statement_timeout),
            )


async def lock_request_key(conn: AsyncConnection, project_id: int, device_id: int, request_id: str) -> None:
    """Serialise concurrent requests with the same (project, device, request_id) for this tx."""
    await _advisory_lock(
        conn, "SELECT pg_advisory_xact_lock(1, hashtext(%s))", (f"{project_id}:{device_id}:{request_id}",)
    )


async def lock_session_key(conn: AsyncConnection, project_id: int, session_id: str) -> None:
    await _advisory_lock(
        conn, "SELECT pg_advisory_xact_lock(2, hashtext(%s))", (f"{project_id}:{session_id}",)
    )


async def lock_logical_ids(conn: AsyncConnection, logical_ids: list[int]) -> None:
    """Per-logical-item transaction lock, taken in sorted order (deadlock-free), so that a
    concurrent revision of the same item waits and then sees the new head (§1.1, G6)."""
    for lid in sorted(set(logical_ids)):
        await _advisory_lock(conn, "SELECT pg_advisory_xact_lock(%s::bigint)", (lid,))


async def resolve_projects(conn: AsyncConnection, slugs: list[str]) -> dict[str, ProjectRef]:
    if not slugs:
        return {}
    cur = await conn.execute(
        "SELECT project_id, slug, name, card_logical_id, archived_at IS NOT NULL"
        " FROM projects WHERE slug = ANY(%s)",
        (list(set(slugs)),),
    )
    return {r[1]: ProjectRef(r[0], r[1], r[2], r[3], r[4]) for r in await cur.fetchall()}


async def find_event(
    conn: AsyncConnection, project_id: int, device_id: int, request_id: str
) -> EventRef | None:
    cur = await conn.execute(
        "SELECT event_id, kind, payload_sha256, result, payload FROM events"
        " WHERE project_id = %s AND device_id = %s AND request_id = %s",
        (project_id, device_id, request_id),
    )
    row = await cur.fetchone()
    return None if row is None else EventRef(*row)


async def session_closed(conn: AsyncConnection, project_id: int, session_id: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM events WHERE project_id = %s AND session_id = %s AND kind = 'call_the_day'",
        (project_id, session_id),
    )
    return await cur.fetchone() is not None


_VERSION_COLS = """
    version_id, logical_id, project_id, project_ids, device_scope, kind, status, title, body, tags,
    pinned, stability, importance, token_count, valid_from, nullif(valid_to, 'infinity'), recorded_at,
    source_event_id, supersedes_version_id, last_access_at
"""


def _version_row(r: tuple[Any, ...]) -> VersionRow:
    return VersionRow(*r)


async def current_versions(conn: AsyncConnection, logical_id: int) -> list[VersionRow]:
    """All current segments (``superseded_at = 'infinity'``) of a logical item, by version_id."""
    cur = await conn.execute(
        f"SELECT {_VERSION_COLS} FROM memory_versions"
        " WHERE logical_id = %s AND superseded_at = 'infinity' ORDER BY version_id",
        (logical_id,),
    )
    return [_version_row(r) for r in await cur.fetchall()]


async def get_version(conn: AsyncConnection, version_id: int) -> VersionRow | None:
    cur = await conn.execute(
        f"SELECT {_VERSION_COLS} FROM memory_versions WHERE version_id = %s", (version_id,)
    )
    row = await cur.fetchone()
    return None if row is None else _version_row(row)


async def current_links_from(conn: AsyncConnection, src_logical_id: int) -> list[LinkRow]:
    cur = await conn.execute(
        """
        SELECT link_id, project_id, project_ids, device_scope, src_logical_id, dst_logical_id,
               dst_version_id, rel, props, valid_from, nullif(valid_to, 'infinity'), recorded_at,
               source_event_id, supersedes_link_id
        FROM links WHERE src_logical_id = %s AND superseded_at = 'infinity' ORDER BY link_id
        """,
        (src_logical_id,),
    )
    return [LinkRow(*r) for r in await cur.fetchall()]


async def get_link(conn: AsyncConnection, link_id: int) -> LinkRow | None:
    cur = await conn.execute(
        "SELECT link_id, project_id, project_ids, device_scope, src_logical_id, dst_logical_id,"
        " dst_version_id, rel, props, valid_from, nullif(valid_to, 'infinity'), recorded_at,"
        " source_event_id, supersedes_link_id FROM links WHERE link_id = %s",
        (link_id,),
    )
    row = await cur.fetchone()
    return None if row is None else LinkRow(*row)


async def chunks_of_version(conn: AsyncConnection, version_id: int) -> list[ChunkRow]:
    cur = await conn.execute(
        "SELECT chunk_id, version_id, project_ids, device_scope, ordinal, char_start, char_end, text,"
        " text_norm, e5_tokens FROM chunks WHERE version_id = %s ORDER BY ordinal",
        (version_id,),
    )
    return [ChunkRow(*r) for r in await cur.fetchall()]


async def device_scope_target_ok(conn: AsyncConnection, device_id: int, user_id: str = "owner") -> bool:
    """§1 integrity: ``device:<id>`` must name an existing, non-revoked device of the same user."""
    cur = await conn.execute(
        "SELECT 1 FROM devices WHERE device_id = %s AND user_id = %s AND status <> 'revoked'",
        (device_id, user_id),
    )
    return await cur.fetchone() is not None


async def clock_now(conn: AsyncConnection) -> datetime:
    cur = await conn.execute("SELECT clock_timestamp()")
    return (await cur.fetchone())[0]


# --------------------------------------------------------------------------- id allocation
_IDENTITY = {
    "memory_versions": "version_id",
    "chunks": "chunk_id",
    "links": "link_id",
    "jobs": "job_id",
    "events": "event_id",
}


async def allocate_ids(conn: AsyncConnection, table: str, n: int) -> list[int]:
    if n <= 0:
        return []
    col = _IDENTITY[table]
    cur = await conn.execute(
        "SELECT nextval(pg_get_serial_sequence(%s, %s)) FROM generate_series(1, %s)", (table, col, n)
    )
    return [r[0] for r in await cur.fetchall()]


async def allocate_logical_ids(conn: AsyncConnection, n: int) -> list[int]:
    if n <= 0:
        return []
    cur = await conn.execute("SELECT nextval('logical_id_seq') FROM generate_series(1, %s)", (n,))
    return [r[0] for r in await cur.fetchall()]


# --------------------------------------------------------------------------- inserts / supersede
async def insert_event(
    conn: AsyncConnection,
    *,
    event_id: int,
    project_id: int,
    device_id: int,
    client: str,
    request_id: str,
    session_id: str | None,
    kind: str,
    projection_version: int,
    payload: dict[str, Any],
    payload_sha256: str,
    occurred_at: datetime,
    result: dict[str, Any] | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO events (event_id, project_id, device_id, client, request_id, session_id, kind,
                            projection_version, payload, payload_sha256, occurred_at, result)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            project_id,
            device_id,
            client,
            request_id,
            session_id,
            kind,
            projection_version,
            Jsonb(payload),
            payload_sha256,
            occurred_at,
            Jsonb(result) if result is not None else None,
        ),
    )


async def insert_version(conn: AsyncConnection, v: VersionRow) -> None:
    await conn.execute(
        """
        INSERT INTO memory_versions
            (version_id, logical_id, project_id, project_ids, device_scope, kind, status, title, body,
             tags, pinned, stability, importance, token_count, valid_from, valid_to, recorded_at,
             source_event_id, supersedes_version_id, last_access_at)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                COALESCE(%s, 'infinity'::timestamptz), %s, %s, %s, %s)
        """,
        (
            v.version_id,
            v.logical_id,
            v.project_id,
            v.project_ids,
            v.device_scope,
            v.kind,
            v.status,
            v.title,
            v.body,
            v.tags,
            v.pinned,
            v.stability,
            v.importance,
            v.token_count,
            v.valid_from,
            v.valid_to,
            v.recorded_at,
            v.source_event_id,
            v.supersedes_version_id,
            v.last_access_at,
        ),
    )


async def insert_chunks(conn: AsyncConnection, rows: list[ChunkRow]) -> None:
    if not rows:
        return
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO chunks (chunk_id, version_id, project_ids, device_scope, ordinal, char_start,
                                char_end, text, text_norm, e5_tokens)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    c.chunk_id,
                    c.version_id,
                    c.project_ids,
                    c.device_scope,
                    c.ordinal,
                    c.char_start,
                    c.char_end,
                    c.text,
                    c.text_norm,
                    c.e5_tokens,
                )
                for c in rows
            ],
        )


async def insert_link(conn: AsyncConnection, ln: LinkRow) -> None:
    await conn.execute(
        """
        INSERT INTO links (link_id, project_id, project_ids, device_scope, src_logical_id, dst_logical_id,
                           dst_version_id, rel, props, valid_from, valid_to, recorded_at, source_event_id,
                           supersedes_link_id)
        OVERRIDING SYSTEM VALUE
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, 'infinity'::timestamptz), %s, %s, %s)
        """,
        (
            ln.link_id,
            ln.project_id,
            ln.project_ids,
            ln.device_scope,
            ln.src_logical_id,
            ln.dst_logical_id,
            ln.dst_version_id,
            ln.rel,
            Jsonb(ln.props),
            ln.valid_from,
            ln.valid_to,
            ln.recorded_at,
            ln.source_event_id,
            ln.supersedes_link_id,
        ),
    )


async def insert_job(
    conn: AsyncConnection,
    *,
    kind: str,
    dedupe_key: str,
    source_event_id: int,
    payload: dict[str, Any],
    run_after: datetime,
    created_at: datetime | None = None,
) -> None:
    """Outbox row (§6). ``run_after`` is the event's T (never a fresh clock on replay);
    ``created_at`` defaults to ``now()`` on the live path and is set to T on replay."""
    if created_at is None:
        await conn.execute(
            "INSERT INTO jobs (kind, dedupe_key, source_event_id, payload, run_after)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING",
            (kind, dedupe_key, source_event_id, Jsonb(payload), run_after),
        )
    else:
        await conn.execute(
            "INSERT INTO jobs (kind, dedupe_key, source_event_id, payload, run_after, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING",
            (kind, dedupe_key, source_event_id, Jsonb(payload), run_after, created_at),
        )


async def supersede_versions(conn: AsyncConnection, version_ids: list[int], at: datetime) -> int:
    if not version_ids:
        return 0
    cur = await conn.execute(
        "UPDATE memory_versions SET superseded_at = %s"
        " WHERE version_id = ANY(%s) AND superseded_at = 'infinity'",
        (at, version_ids),
    )
    return cur.rowcount


async def supersede_links(conn: AsyncConnection, link_ids: list[int], at: datetime) -> int:
    if not link_ids:
        return 0
    cur = await conn.execute(
        "UPDATE links SET superseded_at = %s WHERE link_id = ANY(%s) AND superseded_at = 'infinity'",
        (at, link_ids),
    )
    return cur.rowcount


async def touch_last_access(conn: AsyncConnection, version_ids: list[int], at: datetime) -> None:
    if not version_ids:
        return
    await conn.execute(
        "UPDATE memory_versions SET last_access_at = GREATEST(COALESCE(last_access_at, %s), %s)"
        " WHERE version_id = ANY(%s)",
        (at, at, version_ids),
    )


__all__ = [
    "INF",
    "ChunkRow",
    "EventRef",
    "LinkRow",
    "ProjectRef",
    "VersionRow",
    "allocate_ids",
    "allocate_logical_ids",
    "chunks_of_version",
    "clock_now",
    "current_links_from",
    "current_versions",
    "device_scope_target_ok",
    "find_event",
    "get_link",
    "get_version",
    "insert_chunks",
    "insert_event",
    "insert_job",
    "insert_link",
    "insert_version",
    "lock_logical_ids",
    "lock_request_key",
    "lock_session_key",
    "resolve_projects",
    "session_closed",
    "supersede_links",
    "supersede_versions",
    "touch_last_access",
]
