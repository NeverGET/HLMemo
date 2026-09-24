"""SQL for ``memory.query`` synthesis (PHASE2-4-ROADMAP W2e): the full text of the hit chunks.

Runs inside the query's request transaction, right after the fast path, over chunks that the fast
path has just authorized and returned (the caller passes only the returned hits' clues); nothing
here decides visibility. ``prepare=False`` like every candidate query (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection


@dataclass(slots=True)
class ExcerptRow:
    version_id: int
    ordinal: int
    text: str
    project_ids: list[int]
    valid_from: datetime


async def excerpt_rows(
    conn: AsyncConnection, pairs: list[tuple[int, int]], *, max_chars: int = 16000
) -> dict[tuple[int, int], ExcerptRow]:
    """``(version_id, ordinal) -> row`` for the given chunk addresses."""
    if not pairs:
        return {}
    cur = await conn.execute(
        """
        SELECT c.version_id, c.ordinal, left(c.text, %s), mv.project_ids, mv.valid_from
          FROM unnest(%s::bigint[], %s::int[]) AS p(version_id, ordinal)
          JOIN chunks c ON c.version_id = p.version_id AND c.ordinal = p.ordinal
          JOIN memory_versions mv ON mv.version_id = c.version_id
        """,
        (max_chars, [v for v, _ in pairs], [o for _, o in pairs]),
        prepare=False,
    )
    out: dict[tuple[int, int], ExcerptRow] = {}
    for r in await cur.fetchall():
        out[(int(r[0]), int(r[1]))] = ExcerptRow(int(r[0]), int(r[1]), r[2], [int(x) for x in r[3]], r[4])
    return out


@dataclass(slots=True)
class VersionAccess:
    version_id: int
    project_ids: list[int]
    device_scope: str
    status: str
    current: bool  # open on both time axes (not superseded, not expired)
    changed: bool  # superseded or closed after ``since`` (the query's snapshot time)


async def version_access(
    conn: AsyncConnection, version_ids: list[int], since: datetime
) -> list[VersionAccess]:
    """The authorization-relevant columns of the given versions and whether they were superseded
    or closed after ``since`` (the post-call re-check, Sol 51/52)."""
    if not version_ids:
        return []
    cur = await conn.execute(
        """
        SELECT version_id, project_ids, device_scope, status,
               superseded_at = 'infinity' AND valid_to = 'infinity',
               (superseded_at <= now() AND superseded_at > %(since)s)
                 OR (valid_to <= now() AND valid_to > %(since)s)
          FROM memory_versions WHERE version_id = ANY(%(vids)s)
        """,
        {"vids": list(version_ids), "since": since},
        prepare=False,
    )
    return [
        VersionAccess(int(r[0]), [int(x) for x in r[1]], str(r[2]), str(r[3]), bool(r[4]), bool(r[5]))
        for r in await cur.fetchall()
    ]


__all__ = ["ExcerptRow", "VersionAccess", "excerpt_rows", "version_access"]
