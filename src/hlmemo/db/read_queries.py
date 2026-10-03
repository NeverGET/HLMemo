"""SQL for the read path (PHASE0-SPEC §4 candidate lists, §3 drilldown/raw, §4.4 predicates).

Two predicate layers, never merged (§4.4):

* ``authz`` — (a) project membership + device scope on ``memory_versions``/``links``. The only
  authorization rule for every read; it has no temporal or kind term.
* ``temporal_live`` — (b) ``valid_from <= valid_at < valid_to AND recorded_at <= known_at < superseded_at``.

``chunks.project_ids``/``device_scope`` are denormalised copies and are NOT consulted by any read
predicate: the version row decides (§4.4 "(a) on memory_versions is authoritative", both
directions). A copied-scope prefilter could only narrow the candidate set for performance, and a
stale restrictive copy would then hide a version (a) allows — C4 of the Codex review. The candidate
plans use the tsv/trigram GIN indexes and join ``memory_versions`` by primary key. Trigram matches
are materialized before that join so scope selectivity cannot induce a full chunk scan for
multi-identifier queries (G4). Open ends come back as
``None`` (``nullif(..., 'infinity')``) because psycopg cannot load an infinite ``timestamptz``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from hlmemo.db.write_queries import ProjectRef

# --------------------------------------------------------------------------- predicate fragments
AUTHZ_MV = "(%(pid)s = ANY(mv.project_ids) AND mv.device_scope = ANY(%(scopes)s))"
AUTHZ_L = "(%(pid)s = ANY(l.project_ids) AND l.device_scope = ANY(%(scopes)s))"
TEMPORAL_MV = (
    "(mv.valid_from <= %(valid_at)s AND mv.valid_to > %(valid_at)s"
    " AND mv.recorded_at <= %(known_at)s AND mv.superseded_at > %(known_at)s)"
)
TEMPORAL_L = (
    "(l.valid_from <= %(valid_at)s AND l.valid_to > %(valid_at)s"
    " AND l.recorded_at <= %(known_at)s AND l.superseded_at > %(known_at)s)"
)


def _authz(alias: str) -> str:
    return f"(%(pid)s = ANY({alias}.project_ids) AND {alias}.device_scope = ANY(%(scopes)s))"


def _temporal(alias: str) -> str:
    return (
        f"({alias}.valid_from <= %(valid_at)s AND {alias}.valid_to > %(valid_at)s"
        f" AND {alias}.recorded_at <= %(known_at)s AND {alias}.superseded_at > %(known_at)s)"
    )


# --------------------------------------------------------------------------- row types
@dataclass(slots=True)
class Candidate:
    chunk_id: int
    version_id: int
    logical_id: int
    device_scope: str
    score: float  # list-specific raw score (rank / similarity / distance); RRF uses positions only


@dataclass(slots=True)
class HitRow:
    chunk_id: int
    version_id: int
    ordinal: int
    text: str
    logical_id: int
    kind: str
    title: str
    tags: list[str]
    device_scope: str
    valid_from: datetime


@dataclass(slots=True)
class ReadVersion:
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
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    superseded_at: datetime | None
    source_event_id: int
    supersedes_version_id: int | None


@dataclass(slots=True)
class ChunkSpan:
    ordinal: int
    char_start: int
    char_end: int
    text: str


@dataclass(slots=True)
class DrillLink:
    rel: str
    clue_version_id: int
    stale: bool


@dataclass(slots=True)
class RawLink:
    rel: str
    dst_logical_id: int
    dst_version_id: int | None
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    superseded_at: datetime | None


@dataclass(slots=True)
class SourceEvent:
    event_id: int
    request_id: str
    device_id: int
    device_name: str
    device_class: str
    client: str
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, Any]


@dataclass(slots=True)
class QueryFilters:
    """Operation filters of §4.4 (b) for query/drilldown hits."""

    pid: int
    scopes: list[str]
    valid_at: datetime
    known_at: datetime
    statuses: list[str]
    kinds: list[str] | None = None  # None → all kinds except project_card

    def params(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "scopes": self.scopes,
            "valid_at": self.valid_at,
            "known_at": self.known_at,
            "statuses": self.statuses,
            "kinds": self.kinds,
        }

    def hit_where(self) -> str:
        # (a) on the version row only — never on the chunk's copied scope (C4: a stale restrictive
        # copy must not hide what version scope allows; a stale permissive copy must not leak).
        where = f"{AUTHZ_MV} AND {TEMPORAL_MV} AND mv.status = ANY(%(statuses)s)"
        where += " AND mv.kind <> 'project_card'"
        if self.kinds is not None:
            where += " AND mv.kind = ANY(%(kinds)s)"
        return where


# --------------------------------------------------------------------------- lookups
async def resolve_project(conn: AsyncConnection, slug: str) -> ProjectRef | None:
    cur = await conn.execute(
        "SELECT project_id, slug, name, card_logical_id, archived_at IS NOT NULL"
        " FROM projects WHERE slug = %s",
        (slug,),
    )
    row = await cur.fetchone()
    return None if row is None else ProjectRef(*row)


async def clock_now(conn: AsyncConnection) -> datetime:
    cur = await conn.execute("SELECT clock_timestamp()")
    return (await cur.fetchone())[0]


async def set_trigram_threshold(conn: AsyncConnection, threshold: float) -> None:
    """``SET LOCAL`` — must run inside the read transaction."""
    await conn.execute(f"SET LOCAL pg_trgm.word_similarity_threshold = {float(threshold)!r}")


# --------------------------------------------------------------------------- candidate lists
# The three candidate queries run with ``prepare=False``: psycopg would server-prepare them after
# five executions and Postgres may then switch to a generic plan that ignores the parameters —
# for the trigram query that plan drops the GIN index and rechecks ``<%`` over every chunk
# (3-7 s instead of tens of ms). A custom plan per execution is what the spec's latency needs.
async def lexical_candidates(
    conn: AsyncConnection, f: QueryFilters, terms_text: str, limit: int
) -> list[Candidate]:
    """§4.5: ``tsquery = OR of term:*`` over the ``simple`` lexemes of the normalised terms,
    ``ORDER BY ts_rank_cd(tsv, q, 32) DESC, chunk_id ASC``.

    The lexemes are produced by the same ``to_tsvector('simple', …)`` that built the index, then
    assembled with the ``tsquery`` *input* syntax (``'lex':* | …``) — ``to_tsquery`` would re-parse
    ``'svc-xg6'`` into a phrase.
    """
    if not terms_text:
        return []
    cur = await conn.execute(
        f"""
        WITH q AS (
            SELECT string_agg(format('%%L:*', lex), ' | ')::tsquery AS tsq
            FROM unnest(tsvector_to_array(to_tsvector('simple', %(terms)s))) AS lex
        )
        SELECT c.chunk_id, c.version_id, mv.logical_id, mv.device_scope, ts_rank_cd(c.tsv, q.tsq, 32) AS r
        FROM q, chunks c
        JOIN memory_versions mv ON mv.version_id = c.version_id
        WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq AND {f.hit_where()}
        ORDER BY r DESC, c.chunk_id ASC
        LIMIT %(limit)s
        """,
        {**f.params(), "terms": terms_text, "limit": limit},
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


async def trigram_candidates(
    conn: AsyncConnection, f: QueryFilters, ident_terms: list[str], limit: int
) -> list[Candidate]:
    """§4.6: per identifier term ``term <% text_norm``, merged by best ``word_similarity``.

    Materialize trigram matches before the version join: otherwise scope selectivity can make
    PostgreSQL scan authorized chunks once per identifier instead of using the trigram GIN.
    Authorization still precedes ranking/limit; copied chunk scopes are never consulted.
    """
    if not ident_terms:
        return []
    cur = await conn.execute(
        f"""
        WITH matches AS MATERIALIZED (
            SELECT c.chunk_id, c.version_id,
                   word_similarity(t.term, c.text_norm) AS sim
            FROM unnest(%(terms)s::text[]) AS t(term)
            JOIN chunks c ON t.term <%% c.text_norm
        ), s AS MATERIALIZED (
            SELECT c.chunk_id, c.version_id, mv.logical_id, mv.device_scope, c.sim
            FROM matches c
            JOIN memory_versions mv ON mv.version_id = c.version_id
            WHERE {f.hit_where()}
        )
        SELECT chunk_id, version_id, logical_id, device_scope, max(sim) AS sim
        FROM s
        GROUP BY chunk_id, version_id, logical_id, device_scope
        ORDER BY sim DESC, chunk_id ASC
        LIMIT %(limit)s
        """,
        {**f.params(), "terms": ident_terms, "limit": limit},
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


# D-055 title list. ``TITLE_TSV`` must stay byte-identical to the ``mv_title_tsv`` expression index
# of migration ``0003_title_lexical`` so the planner uses it.
TITLE_TSV = "to_tsvector('simple'::regconfig, hlm_title_norm(mv.title))"


async def title_candidates(
    conn: AsyncConnection, f: QueryFilters, terms: list[str], limit: int
) -> list[Candidate]:
    """D-055: item titles (the importer's relative paths, ``path § heading``) as one RRF list.

    Each term becomes the AND of its title lexemes (``read_service.py`` → ``read:* & service:* &
    py:*``); terms are OR-ed; ``ORDER BY ts_rank_cd DESC, version_id ASC``. Title evidence is
    item-level: the candidate carries the version's first chunk, and fusion credits every fused
    chunk of that version (``core/retrieval.py::rrf_fuse``).
    """
    if not terms:
        return []
    cur = await conn.execute(
        f"""
        WITH t AS (
            SELECT ord, term FROM unnest(%(terms)s::text[]) WITH ORDINALITY AS u(term, ord)
        ), parts AS (
            SELECT t.ord, string_agg(format('%%L:*', lex), ' & ') AS conj
            FROM t, unnest(tsvector_to_array(to_tsvector('simple'::regconfig, hlm_title_norm(t.term)))) AS lex
            GROUP BY t.ord
        ), q AS (
            SELECT string_agg('(' || conj || ')', ' | ')::tsquery AS tsq FROM parts
        ), m AS MATERIALIZED (
            SELECT mv.version_id, mv.logical_id, mv.device_scope, ts_rank_cd({TITLE_TSV}, q.tsq, 32) AS r
            FROM q, memory_versions mv
            WHERE q.tsq IS NOT NULL AND {TITLE_TSV} @@ q.tsq AND {f.hit_where()}
        )
        SELECT c.chunk_id, m.version_id, m.logical_id, m.device_scope, m.r
        FROM m
        JOIN LATERAL (
            SELECT c.chunk_id FROM chunks c WHERE c.version_id = m.version_id ORDER BY c.ordinal LIMIT 1
        ) c ON true
        ORDER BY m.r DESC, m.version_id ASC
        LIMIT %(limit)s
        """,
        {**f.params(), "terms": terms, "limit": limit},
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


# --------------------------------------------------------------------------- term statistics (D-055)
_STATS_SCOPE = (
    "%s = ANY(mv.project_ids) AND mv.device_scope = 'all' AND mv.superseded_at = 'infinity'"
    " AND mv.status <> 'tombstone' AND mv.kind <> 'project_card'"
)


async def term_stats_key(conn: AsyncConnection, pid: int) -> tuple[str, str | None, int]:
    """``(database, project created_at, project corpus revision)`` — the cheap cache key/validator.

    The revision is the newest ``version_id`` whose ``project_ids`` contain the project. Version
    rows are append-only (a revision, correction, archive or tombstone always inserts a row), so
    any change to the project's corpus moves it; other projects' writes do not."""
    cur = await conn.execute(
        "SELECT current_database(), (SELECT created_at::text FROM projects WHERE project_id = %(pid)s),"
        " (SELECT coalesce(max(version_id), 0) FROM memory_versions WHERE %(pid)s = ANY(project_ids))",
        {"pid": pid},
    )
    db, created, rev = await cur.fetchone()
    return db, created, int(rev)


async def unseen_write_age(conn: AsyncConnection, pid: int, since_revision: int) -> float | None:
    """Seconds since the OLDEST write the cached DF (``since_revision``) has not seen, by the
    database clock (``recorded_at`` is the write's transaction time), or ``None`` if there is none.
    One primary-key range probe; runs once per invalidation, never per query (D-064)."""
    cur = await conn.execute(
        "SELECT extract(epoch FROM clock_timestamp() - min(recorded_at))::float8 FROM memory_versions"
        " WHERE version_id > %(rev)s AND %(pid)s = ANY(project_ids)",
        {"rev": since_revision, "pid": pid},
    )
    row = await cur.fetchone()
    return None if row is None or row[0] is None else max(0.0, float(row[0]))


async def term_stats(
    conn: AsyncConnection, pid: int, *, sample_max: int, timeout_ms: int
) -> tuple[int, list[tuple[str, int]], int, list[tuple[str, int]]]:
    """``ts_stat`` of the shared corpus (``device_scope = 'all'`` current versions) of the project:
    ``(n_chunks, [(lexeme, ndoc)], n_titles, [(title lexeme, ndoc)])``.

    Bounded work: at most the newest ``sample_max`` chunks/versions are read inside a savepoint
    (the caller's setting is restored) under ONE total budget of ``timeout_ms`` for all four
    statements (D-064): each statement gets ``statement_timeout`` = the budget left. A timeout or
    an exhausted budget raises ``psycopg.errors.QueryCanceled`` after the savepoint rolled back, so
    the caller's transaction stays usable."""
    scope = _STATS_SCOPE % int(pid)
    limit = int(sample_max)
    chunk_sql = (
        "SELECT c.tsv FROM chunks c JOIN memory_versions mv ON mv.version_id = c.version_id"
        f" WHERE {scope} ORDER BY c.chunk_id DESC LIMIT {limit}"
    )
    title_sql = (
        f"SELECT {TITLE_TSV} FROM memory_versions mv WHERE {scope} ORDER BY mv.version_id DESC LIMIT {limit}"
    )
    cur = await conn.execute("SELECT current_setting('statement_timeout')")
    previous = (await cur.fetchone())[0]
    deadline = time.monotonic() + timeout_ms / 1000

    async def budgeted(sql: str, params: tuple = ()) -> list[tuple]:
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            raise psycopg.errors.QueryCanceled("term statistics budget exhausted")
        await conn.execute(f"SET LOCAL statement_timeout = {left_ms}")
        cur = await conn.execute(sql, params)
        return await cur.fetchall()

    async with conn.transaction():
        n_chunks = int((await budgeted(f"SELECT count(*) FROM ({chunk_sql}) s"))[0][0])
        n_titles = int((await budgeted(f"SELECT count(*) FROM ({title_sql}) s"))[0][0])
        chunk_rows = [
            (w, int(n)) for w, n in await budgeted("SELECT word, ndoc FROM ts_stat(%s)", (chunk_sql,))
        ]
        title_rows = [
            (w, int(n)) for w, n in await budgeted("SELECT word, ndoc FROM ts_stat(%s)", (title_sql,))
        ]
        await conn.execute("SELECT set_config('statement_timeout', %s, true)", (previous,))
    return n_chunks, chunk_rows, n_titles, title_rows


def vector_literal(vec: Any) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def vector_candidates(
    conn: AsyncConnection,
    f: QueryFilters,
    qvec: Any,
    *,
    model: str,
    revision: str,
    preproc_version: int,
    limit: int,
) -> list[Candidate]:
    """§4.7: exact scan ``ORDER BY vec <=> q ASC, chunk_id ASC`` scoped by ``model@revision/preproc``."""
    cur = await conn.execute(
        f"""
        SELECT c.chunk_id, c.version_id, mv.logical_id, mv.device_scope, (e.vec <=> %(q)s::vector) AS d
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        JOIN memory_versions mv ON mv.version_id = c.version_id
        WHERE e.model = %(model)s AND e.model_revision = %(rev)s AND e.preproc_version = %(preproc)s
          AND {f.hit_where()}
        ORDER BY d ASC, c.chunk_id ASC
        LIMIT %(limit)s
        """,
        {
            **f.params(),
            "q": vector_literal(qvec),
            "model": model,
            "rev": revision,
            "preproc": preproc_version,
            "limit": limit,
        },
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


async def indexing_pending(conn: AsyncConnection, pid: int) -> bool:
    """§4.7: queued (or leased, not yet done) ``embed`` jobs exist for a version of the project."""
    cur = await conn.execute(
        """
        SELECT 1 FROM jobs j
        JOIN memory_versions mv ON mv.version_id = (j.payload->>'version_id')::bigint
        WHERE j.kind IN ('embed', 'reembed') AND j.status IN ('queued', 'running')
          AND %s = ANY(mv.project_ids)
        LIMIT 1
        """,
        (pid,),
    )
    return await cur.fetchone() is not None


async def hit_rows(
    conn: AsyncConnection, chunk_ids: list[int], *, max_chars: int = 16000
) -> dict[int, HitRow]:
    """Rows behind the hits; ``text`` is cut at ``max_chars`` — above any ``CHUNK_TOK`` chunk, since
    the query-centred preview (D-055) may come from anywhere in the chunk."""
    if not chunk_ids:
        return {}
    cur = await conn.execute(
        """
        SELECT c.chunk_id, c.version_id, c.ordinal, left(c.text, %s), mv.logical_id, mv.kind, mv.title,
               mv.tags, mv.device_scope, mv.valid_from
        FROM chunks c JOIN memory_versions mv ON mv.version_id = c.version_id
        WHERE c.chunk_id = ANY(%s)
        """,
        (max_chars, chunk_ids),
    )
    return {r[0]: HitRow(*r) for r in await cur.fetchall()}


# --------------------------------------------------------------------------- card (§4.10)
async def card_version(
    conn: AsyncConnection, pid: int, scopes: list[str], card_lid: int, valid_at: datetime, known_at: datetime
) -> tuple[int, str] | None:
    """The project card live at ``(valid_at, known_at)`` under (a) + the card filter of (b)."""
    cur = await conn.execute(
        f"""
        SELECT mv.version_id, mv.body FROM memory_versions mv
        WHERE mv.logical_id = %(lid)s AND mv.kind = 'project_card' AND {AUTHZ_MV} AND {TEMPORAL_MV}
        ORDER BY mv.version_id DESC LIMIT 1
        """,
        {"lid": card_lid, "pid": pid, "scopes": scopes, "valid_at": valid_at, "known_at": known_at},
    )
    row = await cur.fetchone()
    return None if row is None else (row[0], row[1])


async def pinned_sources(
    conn: AsyncConnection,
    src_logical_id: int,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
) -> list[tuple[int, bool]]:
    """``derived_from`` edges of the item live at ``(valid_at, known_at)`` (link row passes (a) +
    ``temporal_live``); each pinned ``dst_version_id`` loaded under (a) ONLY, with
    ``stale = dst.superseded_at <= known_at OR dst.valid_to <= valid_at``.
    Sources failing (a) are absent (never named, §4.10)."""
    cur = await conn.execute(
        f"""
        SELECT d.version_id, (d.superseded_at <= %(known_at)s OR d.valid_to <= %(valid_at)s)
        FROM links l JOIN memory_versions d ON d.version_id = l.dst_version_id
        WHERE l.src_logical_id = %(lid)s AND l.rel = 'derived_from' AND l.dst_version_id IS NOT NULL
          AND {AUTHZ_L} AND {TEMPORAL_L} AND {_authz("d")}
        ORDER BY d.version_id
        """,
        {"lid": src_logical_id, "pid": pid, "scopes": scopes, "valid_at": valid_at, "known_at": known_at},
    )
    return [(r[0], bool(r[1])) for r in await cur.fetchall()]


# --------------------------------------------------------------------------- versions (drilldown / raw)
_READ_VERSION_COLS = """
    mv.version_id, mv.logical_id, mv.project_id, mv.project_ids, mv.device_scope, mv.kind, mv.status,
    mv.title, mv.body, mv.tags, mv.valid_from, nullif(mv.valid_to, 'infinity'), mv.recorded_at,
    nullif(mv.superseded_at, 'infinity'), mv.source_event_id, mv.supersedes_version_id
"""


async def version_authz(
    conn: AsyncConnection, version_id: int, pid: int, scopes: list[str]
) -> ReadVersion | None:
    """The addressed row under (a) only — historical rows allowed (``memory.raw``)."""
    cur = await conn.execute(
        f"SELECT {_READ_VERSION_COLS} FROM memory_versions mv WHERE mv.version_id = %(vid)s AND {AUTHZ_MV}",
        {"vid": version_id, "pid": pid, "scopes": scopes},
    )
    row = await cur.fetchone()
    return None if row is None else ReadVersion(*row)


async def version_live(
    conn: AsyncConnection,
    version_id: int,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
    statuses: list[str],
) -> ReadVersion | None:
    """(a) + ``temporal_live`` + status (``memory.drilldown``; cards allowed)."""
    cur = await conn.execute(
        f"""
        SELECT {_READ_VERSION_COLS} FROM memory_versions mv
        WHERE mv.version_id = %(vid)s AND {AUTHZ_MV} AND {TEMPORAL_MV} AND mv.status = ANY(%(statuses)s)
        """,
        {
            "vid": version_id,
            "pid": pid,
            "scopes": scopes,
            "valid_at": valid_at,
            "known_at": known_at,
            "statuses": statuses,
        },
    )
    row = await cur.fetchone()
    return None if row is None else ReadVersion(*row)


async def chunk_spans(
    conn: AsyncConnection, version_id: int, lo: int | None = None, hi: int | None = None
) -> list[ChunkSpan]:
    sql = "SELECT ordinal, char_start, char_end, text FROM chunks WHERE version_id = %(vid)s"
    params: dict[str, Any] = {"vid": version_id}
    if lo is not None:
        sql += " AND ordinal >= %(lo)s"
        params["lo"] = lo
    if hi is not None:
        sql += " AND ordinal <= %(hi)s"
        params["hi"] = hi
    cur = await conn.execute(sql + " ORDER BY ordinal", params)
    return [ChunkSpan(*r) for r in await cur.fetchall()]


async def drilldown_links(
    conn: AsyncConnection,
    src_logical_id: int,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
) -> list[DrillLink]:
    """§1.1/§3 one-hop edges: link row passes (a) + ``temporal_live``; pinned endpoints are
    authorized by (a) only and report ``stale``; unpinned endpoints resolve to the live version
    of ``dst_logical_id`` under (a) + ``temporal_live`` or the edge is dropped."""
    cur = await conn.execute(
        f"""
        SELECT l.rel, l.dst_version_id,
               (SELECT (d.superseded_at <= %(known_at)s OR d.valid_to <= %(valid_at)s)
                  FROM memory_versions d
                 WHERE d.version_id = l.dst_version_id AND {_authz("d")}) AS pinned_stale,
               (SELECT d.version_id FROM memory_versions d
                 WHERE l.dst_version_id IS NULL AND d.logical_id = l.dst_logical_id
                   AND {_authz("d")} AND {_temporal("d")}
                 ORDER BY d.version_id DESC LIMIT 1) AS live_dst
        FROM links l
        WHERE l.src_logical_id = %(lid)s AND {AUTHZ_L} AND {TEMPORAL_L}
        ORDER BY l.link_id
        """,
        {"lid": src_logical_id, "pid": pid, "scopes": scopes, "valid_at": valid_at, "known_at": known_at},
    )
    out: list[DrillLink] = []
    for rel, dst_version_id, pinned_stale, live_dst in await cur.fetchall():
        if dst_version_id is not None:
            if pinned_stale is None:  # pinned endpoint fails (a): edge does not exist for the caller
                continue
            out.append(DrillLink(rel, dst_version_id, bool(pinned_stale)))
        elif live_dst is not None:
            out.append(DrillLink(rel, live_dst, False))
    return out


async def raw_links(
    conn: AsyncConnection, version: ReadVersion, pid: int, scopes: list[str], known_at: datetime
) -> list[RawLink]:
    """Edges overlapping the addressed version on both temporal axes (D-037).

    Historical versions retain their historical edges. Endpoint authorization remains (a)
    only: a superseded pinned target is still evidence. The cursor's knowledge cutoff excludes
    later insertions so continuation pages retain a stable order.
    """
    cur = await conn.execute(
        f"""
        SELECT l.rel, l.dst_logical_id, l.dst_version_id, l.valid_from, nullif(l.valid_to, 'infinity'),
               l.recorded_at, nullif(l.superseded_at, 'infinity')
        FROM links l
        WHERE l.src_logical_id = %(lid)s AND {AUTHZ_L}
          AND l.valid_from < coalesce(%(valid_to)s::timestamptz, 'infinity')
          AND l.valid_to > %(valid_from)s
          AND l.recorded_at < coalesce(%(superseded_at)s::timestamptz, 'infinity')
          AND l.superseded_at > %(recorded_at)s
          AND l.recorded_at <= %(known_at)s
          AND CASE WHEN l.dst_version_id IS NOT NULL THEN EXISTS (
                    SELECT 1 FROM memory_versions d WHERE d.version_id = l.dst_version_id AND {_authz("d")})
               ELSE EXISTS (
                    SELECT 1 FROM memory_versions d WHERE d.logical_id = l.dst_logical_id AND {_authz("d")})
              END
        ORDER BY l.link_id
        """,
        {
            "lid": version.logical_id,
            "pid": pid,
            "scopes": scopes,
            "valid_from": version.valid_from,
            "valid_to": version.valid_to,
            "recorded_at": version.recorded_at,
            "superseded_at": version.superseded_at,
            "known_at": known_at,
        },
    )
    return [RawLink(*r) for r in await cur.fetchall()]


#: B3 (D-207 #5): at most this many incoming ``superseded_by`` entries on one ``memory.raw`` envelope
#: (newest first; the envelope repeats on every page, so the list is bounded)
SUPERSEDED_BY_MAX = 5
#: a part-scope entry carries its quote (the outdated statement), cut to this many characters
SUPERSEDED_QUOTE_CHARS = 160
#: B3: at most this many ``superseded_by`` entries on one ``memory.query`` hit
HIT_SUPERSEDED_BY_MAX = 3


@dataclass(slots=True)
class SupersededBy:
    """One incoming live ``supersedes`` link of a version (``memory.raw``): the superseding item
    and the version of it that stands for the link (the one valid at the link's start, else its
    newest visible row), whole or part scope, the link's validity and quote."""

    logical_id: int
    version_id: int
    part: bool
    quote: str
    valid_from: datetime
    valid_to: datetime | None


def pinned_copy(link: str, version: str) -> str:
    """SQL predicate: ``version`` is the version the link ``link`` pins or a BODY-IDENTICAL copy of
    it: its ``supersedes_version_id`` chain inside the item, every hop with the same body (a close or
    revision cut survivor, a write-path survivor, a reversal's restored copy). The chain walks
    strictly older ids, so it ends. No authorization: ``pinned_applies`` adds it."""
    return f"""{link}.dst_version_id IN (
            WITH RECURSIVE pc(version_id, sup) AS (
                SELECT ph.version_id, ph.supersedes_version_id FROM memory_versions ph
                 WHERE ph.version_id = {version}
              UNION ALL
                SELECT pp.version_id, pp.supersedes_version_id
                  FROM pc
                  JOIN memory_versions pp ON pp.version_id = pc.sup
                  JOIN memory_versions ph ON ph.version_id = {version}
                 WHERE pp.version_id < pc.version_id AND pp.logical_id = ph.logical_id
                   AND pp.body = ph.body)
            SELECT pc.version_id FROM pc)"""


def pinned_part_carry(link: str, version: str) -> str:
    """SQL predicate (review 98 #1): a pinned PART link from ANOTHER item still speaks about a LATER
    version of its target item that still quotes the link's quote ("TTL 60. Owner Alice." → "TTL
    60. Owner Bob."): the pair ``(version, link)`` is one ``part_carries`` approved, passed as
    ``%(carry)s`` (``"<version id>:<link id>"``). No authorization: ``pinned_applies`` adds it (the
    quote is the pinned version's text)."""
    return f"(({version})::text || ':' || {link}.link_id::text) = ANY(%(carry)s::text[])"


async def part_carries(
    conn: AsyncConnection, version_ids: list[int | None], pid: int, scopes: list[str]
) -> list[str]:
    """Review 98 #1: the ``"<version id>:<link id>"`` pairs for ``pinned_part_carry``. A candidate is
    a ``supersedes`` link passing §4.4 (a) as a row, of part scope with a non-empty quote, from
    ANOTHER item (a span revision's self-link never carries; a whole link never does), pinned to an
    OLDER version of the same item as ``version``; SQL only prefilters on the quote being in the
    NFC body. The decision is the write path's own rule for a link-only target
    (``write_updates.span_quoted_once``, the ``SPAN_GUARDS``): the quote occurs EXACTLY ONCE in the
    NFC body, on word boundaries — "TTL 60" never carries to "TTL 600", nor to a body quoting it
    twice."""
    from hlmemo.core.write_updates import span_quoted_once

    vids = sorted({int(v) for v in version_ids if v is not None})
    if not vids:
        return []
    cur = await conn.execute(
        f"""
        SELECT pv.version_id, l.link_id, pv.body, l.props->>'quote'
          FROM memory_versions pv
          JOIN links l ON l.dst_logical_id = pv.logical_id
         WHERE pv.version_id = ANY(%(vids)s)
           AND l.rel = 'supersedes' AND {AUTHZ_L}
           AND l.dst_version_id < pv.version_id
           AND l.src_logical_id <> l.dst_logical_id
           AND COALESCE(l.props->>'scope', 'whole') = 'part'
           AND length(COALESCE(l.props->>'quote', '')) > 0
           AND strpos(normalize(pv.body, NFC), l.props->>'quote') > 0
        """,  # noqa: S608 - fixed fragments
        {"vids": vids, "pid": pid, "scopes": scopes},
    )
    return [f"{v}:{lk}" for v, lk, body, quote in await cur.fetchall() if span_quoted_once(body, quote)]


def pinned_applies(link: str, version: str) -> str:
    """SQL predicate (review 96 Astra #1): does the link ``link`` (an alias) speak about the version
    ``version`` (an SQL expression; NULL never matches a pinned link)? Needs ``%(pid)s``,
    ``%(scopes)s`` and ``%(carry)s`` (``part_carries`` of the versions read).

    * An UNPINNED link (no ``dst_version_id``) is about its item: true (the caller checks time).
    * A PINNED link (a D-118 update, a span revision's self-link, any link pinned to a version) is
      true only when the caller may read the pinned version (§4.4 (a): its quote is that version's
      text) AND ``version`` is that version or a body-identical copy of it (``pinned_copy``), or, a
      part link from another item, a later version of the item that still holds its quote verbatim
      (``pinned_part_carry``, review 98 #1). Never a later revision that does not quote it exactly
      once on word boundaries, nor
      any later revision for a whole link (it may be public where the pinned one was private)."""
    return f"""({link}.dst_version_id IS NULL OR (
        EXISTS (SELECT 1 FROM memory_versions pd
                 WHERE pd.version_id = {link}.dst_version_id AND {_authz("pd")})
        AND ({pinned_copy(link, version)}
             OR {pinned_part_carry(link, version)})))"""


async def incoming_supersedes(
    conn: AsyncConnection, version: ReadVersion, pid: int, scopes: list[str], known_at: datetime
) -> list[SupersededBy]:
    """B3 (D-207 #5): the INCOMING ``supersedes`` links of ``version``'s item that are live in
    transaction time at ``known_at`` (a reverted link is gone; an as-of cursor keeps its view). The
    link row and the superseding item must pass §4.4 (a): a superseder the caller cannot see is
    never named, and its link is not reported at all.

    Which links speak about THIS version (review 96 Astra #1 / Sol #4):

    * an UNPINNED link (no ``dst_version_id``: about the item over its validity) from another item:
      its valid time must overlap the version's (``link.valid_from < version.valid_to`` and
      ``link.valid_to > version.valid_from``; touching intervals do not overlap);
    * a PINNED link (a D-118 update, a span revision's self-link, any link pinned to a version): only
      for a caller who may read the PINNED version (§4.4 (a); its quote is that version's text), and
      only on that version or a body-identical copy of it, or (a part link from another item) a
      later version that still holds its quote verbatim (``pinned_applies``) — never on a later
      revision that lost the quote (it may be public where the pinned one was private). Its valid time
      overlaps the version's, or the version is a body-identical copy that ends exactly where the
      link starts (the cut survivor of the very close or revision the link records).

    Newest first, at most ``SUPERSEDED_BY_MAX``."""
    carry = await part_carries(conn, [version.version_id], pid, scopes)
    cur = await conn.execute(
        f"""
        SELECT l.src_logical_id, s.version_id, COALESCE(l.props->>'scope', 'whole') = 'part',
               COALESCE(l.props->>'quote', ''), l.valid_from, nullif(l.valid_to, 'infinity')
          FROM links l
          JOIN LATERAL (
                SELECT s.version_id FROM memory_versions s
                 WHERE s.logical_id = l.src_logical_id AND {_authz("s")}
                   AND s.recorded_at <= %(known_at)s AND s.superseded_at > %(known_at)s
                 ORDER BY (s.valid_from <= l.valid_from AND s.valid_to > l.valid_from) DESC,
                          s.version_id DESC
                 LIMIT 1) s ON true
         WHERE l.rel = 'supersedes' AND l.dst_logical_id = %(lid)s AND {AUTHZ_L}
           AND l.recorded_at <= %(known_at)s AND l.superseded_at > %(known_at)s
           AND l.valid_to > %(valid_from)s
           AND CASE WHEN l.dst_version_id IS NULL THEN
                    l.src_logical_id <> l.dst_logical_id
                    AND l.valid_from < coalesce(%(valid_to)s::timestamptz, 'infinity')
               ELSE
                    {pinned_applies("l", "%(vid)s::bigint")}
                    AND (l.valid_from < coalesce(%(valid_to)s::timestamptz, 'infinity')
                         OR (l.dst_version_id <> %(vid)s AND l.valid_from = %(valid_to)s::timestamptz
                             AND {pinned_copy("l", "%(vid)s::bigint")}))
               END
         ORDER BY l.valid_from DESC, l.link_id DESC
         LIMIT %(limit)s
        """,  # noqa: S608 - fixed fragments
        {
            "lid": version.logical_id,
            "vid": version.version_id,
            "pid": pid,
            "scopes": scopes,
            "valid_from": version.valid_from,
            "valid_to": version.valid_to,
            "known_at": known_at,
            "limit": SUPERSEDED_BY_MAX,
            "carry": carry,
        },
    )
    return [SupersededBy(int(a), int(b), bool(c), str(d), e, f) for a, b, c, d, e, f in await cur.fetchall()]


async def superseded_hits(
    conn: AsyncConnection,
    version_ids: list[int],
    *,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
    statuses: list[str],
) -> dict[int, list[tuple[int, bool]]]:
    """B3 (D-207 #5), ONE query: ``{hit version id: [(superseder's version id, part)]}`` over the
    live ``supersedes`` links (valid and known at ``(valid_at, known_at)``, the link row passing
    §4.4 (a)) that target the item of one of ``version_ids`` from ANOTHER item. The superseder must
    be a live item of the caller's view (§4.4 (a) + the same temporal point and statuses as the
    query): a hidden one is never named and does not flag the hit.

    Review 96 Astra #1: never by logical id alone. An unpinned link flags any live version of its
    item; a PINNED link flags a hit only when the caller may read the pinned version (§4.4 (a)) and
    the hit is that version or a body-identical copy of it, or a later version still quoting a part
    link verbatim (``pinned_applies``, the rule of ``incoming_supersedes``).
    One entry per superseding item (a whole link wins over a part link of the same item); whole
    entries first, then newest; at most ``HIT_SUPERSEDED_BY_MAX`` per hit."""
    if not version_ids:
        return {}
    carry = await part_carries(conn, version_ids, pid, scopes)
    cur = await conn.execute(
        f"""
        SELECT DISTINCT ON (h.version_id, l.src_logical_id)
               h.version_id, s.version_id, COALESCE(l.props->>'scope', 'whole') = 'part'
          FROM memory_versions h
          JOIN links l ON l.dst_logical_id = h.logical_id
          JOIN LATERAL (
                SELECT s.version_id FROM memory_versions s
                 WHERE s.logical_id = l.src_logical_id AND {_authz("s")} AND {_temporal("s")}
                   AND s.status = ANY(%(statuses)s)
                 ORDER BY s.version_id DESC LIMIT 1) s ON true
         WHERE h.version_id = ANY(%(vids)s)
           AND l.rel = 'supersedes' AND l.src_logical_id <> l.dst_logical_id AND {AUTHZ_L} AND {TEMPORAL_L}
           AND {pinned_applies("l", "h.version_id")}
         ORDER BY h.version_id, l.src_logical_id,
                  COALESCE(l.props->>'scope', 'whole') = 'part', l.link_id DESC
        """,  # noqa: S608 - fixed fragments
        {
            "vids": sorted(set(version_ids)),
            "pid": pid,
            "scopes": scopes,
            "valid_at": valid_at,
            "known_at": known_at,
            "statuses": statuses,
            "carry": carry,
        },
    )
    out: dict[int, list[tuple[int, bool]]] = {}
    for hit, vid, part in await cur.fetchall():
        out.setdefault(int(hit), []).append((int(vid), bool(part)))
    return {
        hit: sorted(entries, key=lambda e: (e[1], -e[0]))[:HIT_SUPERSEDED_BY_MAX]
        for hit, entries in out.items()
    }


async def endpoint_authz(
    conn: AsyncConnection, pid: int, scopes: list[str], *, logical_ids: list[int], version_ids: list[int]
) -> tuple[set[int], set[int]]:
    """§4.4 (a) on far endpoints, the predicate ``raw_links``/``drilldown_links`` apply in SQL,
    for references embedded elsewhere (the verbatim ``payload_item.links`` of ``memory.raw``):
    returns the logical ids with at least one version passing (a) and the version ids passing
    (a). A reference to anything else is omitted, never named."""
    lids: set[int] = set()
    vids: set[int] = set()
    if logical_ids:
        cur = await conn.execute(
            f"SELECT DISTINCT mv.logical_id FROM memory_versions mv"
            f" WHERE mv.logical_id = ANY(%(lids)s) AND {AUTHZ_MV}",
            {"lids": sorted(set(logical_ids)), "pid": pid, "scopes": scopes},
        )
        lids = {r[0] for r in await cur.fetchall()}
    if version_ids:
        cur = await conn.execute(
            f"SELECT mv.version_id FROM memory_versions mv"
            f" WHERE mv.version_id = ANY(%(vids)s) AND {AUTHZ_MV}",
            {"vids": sorted(set(version_ids)), "pid": pid, "scopes": scopes},
        )
        vids = {r[0] for r in await cur.fetchall()}
    return lids, vids


async def project_slugs(conn: AsyncConnection, project_ids: list[int]) -> dict[int, str]:
    if not project_ids:
        return {}
    cur = await conn.execute(
        "SELECT project_id, slug FROM projects WHERE project_id = ANY(%s)", (project_ids,)
    )
    return {r[0]: r[1] for r in await cur.fetchall()}


async def source_event(conn: AsyncConnection, event_id: int) -> SourceEvent | None:
    cur = await conn.execute(
        """
        SELECT e.event_id, e.request_id::text, d.device_id, d.name, d.class, e.client, e.occurred_at,
               COALESCE((e.payload->'resolved'->>'recorded_at')::timestamptz, e.received_at), e.payload
        FROM events e JOIN devices d ON d.device_id = e.device_id
        WHERE e.event_id = %s
        """,
        (event_id,),
    )
    row = await cur.fetchone()
    return None if row is None else SourceEvent(*row)


# --------------------------------------------------------------------------- access event (D-012)
async def record_access(
    conn: AsyncConnection,
    *,
    pid: int,
    device_id: int,
    client: str,
    tool: str,
    request: dict[str, Any],
    version_ids: list[int],
    at: datetime,
    payload_sha256: str,
) -> None:
    """``access`` event + ``last_access_at`` touch (replayed by ``db/replay.py::_replay_access``)."""
    from hlmemo.core.temporal import fmt_ts

    payload = {
        "request": {"tool": tool, **request},
        "resolved": {"recorded_at": fmt_ts(at), "version_ids": sorted(set(version_ids))},
    }
    await conn.execute(
        """
        INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,
                            occurred_at)
        VALUES (%s, %s, %s, %s, 'access', %s, %s, %s)
        """,
        (pid, device_id, client, str(uuid.uuid4()), Jsonb(payload), payload_sha256, at),
    )
    if version_ids:
        await conn.execute(
            "UPDATE memory_versions SET last_access_at = GREATEST(COALESCE(last_access_at, %s), %s)"
            " WHERE version_id = ANY(%s)",
            (at, at, sorted(set(version_ids))),
        )


__all__ = [
    "AUTHZ_MV",
    "HIT_SUPERSEDED_BY_MAX",
    "SUPERSEDED_BY_MAX",
    "SUPERSEDED_QUOTE_CHARS",
    "TEMPORAL_MV",
    "TITLE_TSV",
    "Candidate",
    "ChunkSpan",
    "DrillLink",
    "HitRow",
    "QueryFilters",
    "RawLink",
    "ReadVersion",
    "SourceEvent",
    "SupersededBy",
    "card_version",
    "chunk_spans",
    "clock_now",
    "drilldown_links",
    "endpoint_authz",
    "hit_rows",
    "incoming_supersedes",
    "indexing_pending",
    "lexical_candidates",
    "part_carries",
    "pinned_applies",
    "pinned_copy",
    "pinned_part_carry",
    "pinned_sources",
    "project_slugs",
    "raw_links",
    "record_access",
    "resolve_project",
    "set_trigram_threshold",
    "source_event",
    "superseded_hits",
    "term_stats",
    "term_stats_key",
    "unseen_write_age",
    "title_candidates",
    "trigram_candidates",
    "vector_candidates",
    "vector_literal",
    "version_authz",
    "version_live",
]
