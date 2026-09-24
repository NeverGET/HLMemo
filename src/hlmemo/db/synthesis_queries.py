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


__all__ = ["ExcerptRow", "excerpt_rows"]
