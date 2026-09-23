"""Per-project term document frequencies for query-term filtering (PHASE0-SPEC §4 step 3, D-055).

A query term whose *prefix* document frequency exceeds ``DF_MAX_FRAC`` of the project's chunks is
a function word for this corpus (``the``, ``bir``, ``und`` …) whatever the language: it matches most
chunks through the ``term:*`` OR query and only adds length noise to the lexical list. The DF is
language-agnostic (no stop lists) and is computed over the project's *shared* corpus — current,
non-tombstoned versions with ``device_scope = 'all'`` — so the statistic never depends on, and
can never reveal, rows another device may not read.

The vocabulary comes from ``ts_stat`` over the same ``tsv`` the lexical list searches (and over
the ``0003_title_lexical`` title vectors for the title list). It is cached per process and per
``(database, project, project created_at)``; an entry is recomputed when the chunk id sequence
moved backwards (tables were reset) or advanced by more than ``max(REFRESH_MIN_DELTA,
REFRESH_FRAC·n)``, or after ``REFRESH_TTL_S``. DF is a corpus statistic: a slightly stale value
only shifts which very common words are dropped, never authorization or correctness.
"""

from __future__ import annotations

import time
from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import accumulate

from psycopg import AsyncConnection

from hlmemo.db import read_queries as q

REFRESH_TTL_S = 600.0
REFRESH_MIN_DELTA = 64
REFRESH_FRAC = 0.10


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Sorted lexemes with cumulative document counts; ``n`` = number of documents."""

    n: int
    lexemes: tuple[str, ...] = ()
    cumulative: tuple[int, ...] = (0,)  # cumulative[i] = Σ ndoc of lexemes[:i]

    @classmethod
    def build(cls, n: int, rows: list[tuple[str, int]]) -> Vocabulary:
        rows = sorted(rows)
        return cls(
            n=n,
            lexemes=tuple(w for w, _ in rows),
            cumulative=tuple(accumulate((c for _, c in rows), initial=0)),
        )

    def prefix_df(self, term: str) -> int:
        """Documents matching ``term:*``: the summed ``ndoc`` of every lexeme with prefix ``term``,
        capped at ``n`` (an upper bound — a document holding two such lexemes counts twice)."""
        lo = bisect_left(self.lexemes, term)
        hi = bisect_left(self.lexemes, term + "\U0010ffff", lo)
        return min(self.n, self.cumulative[hi] - self.cumulative[lo])


@dataclass(frozen=True, slots=True)
class ProjectStats:
    chunks: Vocabulary
    titles: Vocabulary


EMPTY = ProjectStats(Vocabulary(0), Vocabulary(0))


@dataclass(slots=True)
class _Entry:
    stats: ProjectStats
    watermark: int
    at: float


@dataclass(slots=True)
class StatsCache:
    ttl_s: float = REFRESH_TTL_S
    _entries: dict[tuple[str, int, str], _Entry] = field(default_factory=dict)

    def _fresh(self, e: _Entry, watermark: int, now: float) -> bool:
        if watermark < e.watermark or now - e.at > self.ttl_s:
            return False
        return watermark - e.watermark <= max(REFRESH_MIN_DELTA, int(REFRESH_FRAC * e.stats.chunks.n))

    async def get(self, conn: AsyncConnection, pid: int) -> ProjectStats:
        key_db, created, watermark = await q.term_stats_key(conn, pid)
        if created is None:
            return EMPTY
        key = (key_db, pid, created)
        now = time.monotonic()
        e = self._entries.get(key)
        if e is not None and self._fresh(e, watermark, now):
            return e.stats
        n_chunks, chunk_rows, n_titles, title_rows = await q.term_stats(conn, pid)
        stats = ProjectStats(Vocabulary.build(n_chunks, chunk_rows), Vocabulary.build(n_titles, title_rows))
        self._entries[key] = _Entry(stats, watermark, now)
        return stats


__all__ = ["EMPTY", "ProjectStats", "StatsCache", "Vocabulary"]
