"""Per-project term document frequencies for query-term filtering (PHASE0-SPEC §4 step 3, D-055).

A query term whose *prefix* document frequency exceeds ``DF_MAX_FRAC`` of the project's chunks is
a function word for this corpus (``the``, ``bir``, ``und`` …) whatever the language: it matches most
chunks through the ``term:*`` OR query and only adds length noise to the lexical list. The DF is
language-agnostic (no stop lists) and is computed over the project's *shared* corpus — current,
non-tombstoned versions with ``device_scope = 'all'`` — so the statistic never depends on, and
can never reveal, rows another device may not read.

The vocabulary comes from ``ts_stat`` over the same ``tsv`` the lexical list searches (and over
the ``0003_title_lexical`` title vectors for the title list), reading at most the newest
``SAMPLE_MAX`` chunks/versions under a ``REFRESH_TIMEOUT_MS`` statement timeout. It is cached per
process and per ``(database, project, project created_at)`` and validated on every query against
the project's corpus revision (newest ``version_id`` of the project, one index probe): any write,
revision, correction or archive in the project invalidates it; ``REFRESH_TTL_S`` is only a
fallback. The cache is an LRU bounded by ``MAX_PROJECTS`` entries and ``MAX_TERMS`` lexemes in
total. DF is a corpus statistic: it never affects authorization.

Stale-while-revalidate (D-063): once a project has a DF, no query ever blocks on a rebuild. An
invalidation (a newer corpus revision, or the TTL) returns the cached DF and starts ONE background
refresh for that project (single flight, on its own connection, still capped by
``REFRESH_TIMEOUT_MS``); a refresh that fails or times out leaves the old DF in place and may be
retried after ``STALE_RETRY_S``. Staleness is bounded by ``MAX_STALENESS_S`` (5 s): a query at least
that long after a write (or after the refresh finished) sees the new DF. Only a cold start (no DF
yet) blocks, on the single-flight load under the 2 s cap; a cold load that fails yields *no
filtering* (Phase-0 behaviour) and is not retried for ``RETRY_AFTER_S``. This relaxes Sol 33 #1
("the next query sees the write") to "a query ≥ 5 s after the write sees it" by decision D-063.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import accumulate

import psycopg
from psycopg import AsyncConnection

from hlmemo.db import read_queries as q

REFRESH_TTL_S = 600.0
REFRESH_TIMEOUT_MS = 2000
RETRY_AFTER_S = 60.0
MAX_STALENESS_S = 5.0
STALE_RETRY_S = 1.0  # a failed background refresh may be retried after this
SAMPLE_MAX = 50_000
MAX_PROJECTS = 64
MAX_TERMS = 2_000_000

log = logging.getLogger(__name__)


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
    stats: ProjectStats | None  # None: the refresh failed; no filtering until ``at + RETRY_AFTER_S``
    revision: int
    at: float

    @property
    def size(self) -> int:
        return 0 if self.stats is None else len(self.stats.chunks.lexemes) + len(self.stats.titles.lexemes)


Connector = Callable[[], Awaitable[AsyncConnection]]


@dataclass(slots=True)
class StatsCache:
    ttl_s: float = REFRESH_TTL_S
    max_projects: int = MAX_PROJECTS
    max_terms: int = MAX_TERMS
    refresh_timeout_ms: int = REFRESH_TIMEOUT_MS
    sample_max: int = SAMPLE_MAX
    refreshes: int = 0  # number of ts_stat refreshes run (observability/tests)
    #: opens the background refresh's own connection; default: the caller's parameters
    connect: Connector | None = None
    _entries: OrderedDict[tuple[str, int, str], _Entry] = field(default_factory=OrderedDict)
    _locks: dict[tuple[str, int, str], asyncio.Lock] = field(default_factory=dict)
    _tasks: dict[tuple[str, int, str], asyncio.Task] = field(default_factory=dict)
    _failed_at: dict[tuple[str, int, str], float] = field(default_factory=dict)
    _terms: int = 0

    def _fresh(self, e: _Entry, revision: int, now: float) -> bool:
        if e.stats is None:
            return now - e.at <= RETRY_AFTER_S
        return e.revision == revision and now - e.at <= self.ttl_s

    def _put(self, key: tuple[str, int, str], e: _Entry) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._terms -= old.size
        self._entries[key] = e
        self._terms += e.size
        while len(self._entries) > 1 and (
            len(self._entries) > self.max_projects or self._terms > self.max_terms
        ):
            gone, evicted = self._entries.popitem(last=False)  # least recently used
            self._terms -= evicted.size
            self._locks.pop(gone, None)

    def _hit(self, key: tuple[str, int, str], revision: int) -> _Entry | None:
        e = self._entries.get(key)
        if e is not None and self._fresh(e, revision, time.monotonic()):
            self._entries.move_to_end(key)
            return e
        return None

    async def _load(self, conn: AsyncConnection, pid: int) -> ProjectStats:
        self.refreshes += 1
        n_chunks, chunk_rows, n_titles, title_rows = await q.term_stats(
            conn, pid, sample_max=self.sample_max, timeout_ms=self.refresh_timeout_ms
        )
        return ProjectStats(Vocabulary.build(n_chunks, chunk_rows), Vocabulary.build(n_titles, title_rows))

    def _connector(self, conn: AsyncConnection) -> Connector:
        if self.connect is not None:
            return self.connect
        params = conn.info.get_parameters()
        password = conn.info.password

        async def _open() -> AsyncConnection:
            return await AsyncConnection.connect(**params, password=password, autocommit=True)

        return _open

    def _revalidate(self, conn: AsyncConnection, key: tuple[str, int, str], pid: int, revision: int) -> None:
        """Start the single background refresh of ``key`` unless one runs or one just failed."""
        task = self._tasks.get(key)
        if task is not None and not task.done():
            return
        failed = self._failed_at.get(key)
        if failed is not None and time.monotonic() - failed < STALE_RETRY_S:
            return
        connect = self._connector(conn)

        async def refresh() -> None:
            try:
                bg = await connect()
                try:
                    stats = await self._load(bg, pid)
                finally:
                    with contextlib.suppress(Exception):
                        await bg.close()
            except Exception as exc:  # noqa: BLE001 - keep the old DF; retry after STALE_RETRY_S
                self._failed_at[key] = time.monotonic()
                log.warning(
                    "term statistics refresh for project %s failed (%s); old DF kept", pid, type(exc).__name__
                )
                return
            self._failed_at.pop(key, None)
            self._put(key, _Entry(stats, revision, time.monotonic()))

        self._tasks[key] = asyncio.get_running_loop().create_task(refresh(), name=f"df-refresh-{pid}")

    async def get(self, conn: AsyncConnection, pid: int) -> ProjectStats | None:
        """The project's statistics, or ``None`` = do not filter (cold load failed/timed out)."""
        key_db, created, revision = await q.term_stats_key(conn, pid)
        if created is None:
            return EMPTY
        key = (key_db, pid, created)
        e = self._entries.get(key)
        if e is not None and e.stats is not None:  # a DF exists: never block (D-063)
            if not self._fresh(e, revision, time.monotonic()):
                self._revalidate(conn, key, pid, revision)
            self._entries.move_to_end(key)
            return e.stats
        e = self._hit(key, revision)
        if e is not None:
            return e.stats
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # cold start, single flight: waiters reuse the winner's load
            e = self._entries.get(key)
            if e is not None and (e.stats is not None or self._fresh(e, revision, time.monotonic())):
                return e.stats
            try:
                stats: ProjectStats | None = await self._load(conn, pid)
            except psycopg.errors.QueryCanceled:
                log.warning("term statistics for project %s timed out; query terms unfiltered", pid)
                stats = None
            self._put(key, _Entry(stats, revision, time.monotonic()))
            return stats


__all__ = ["EMPTY", "ProjectStats", "StatsCache", "Vocabulary"]
