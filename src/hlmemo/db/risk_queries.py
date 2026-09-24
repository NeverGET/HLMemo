"""SQL for ``memory.risk_check``'s deterministic stage (PHASE2-4-ROADMAP W2d).

The candidate universe is every CURRENT, active ``lesson``/``experience`` version the caller may
read right now: ``project_ids`` overlaps the projects the device holds a live grant on (the home
project, other projects and ``hlm-global`` alike: the union of the per-project §4.4 (a) rules) and
``device_scope`` is one of the caller's scope values. Grants and scope come from the request's
``AuthContext`` (resolved under ``FOR SHARE`` in the request transaction), never from the payload.
Isolation (``policy.librarian_cross_project = exclude``; ``candidates.relation_allowed``, Sol 54 #1)
is part of the universe itself, read in the same statement (no extra round trip): for a home that is
excluded only items lying entirely in it qualify; for any other home no item touching an excluded
project qualifies, whether or not the caller holds a grant on that project.

The universe is materialized first (it is small: lessons are a thin slice of a project) and the
four RRF lists of the query path are computed over it: lexical (``tsv @@ term:*``), title (D-055),
trigram for identifier terms (GIN first, then joined: the pattern of ``read_queries``) and an
exact vector scan over the universe's chunks. Every query runs with ``prepare=False`` (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.resolve import lock_device_access
from hlmemo.db.librarian_queries import CROSS_PROJECT_POLICY
from hlmemo.db.read_queries import TITLE_TSV, Candidate, vector_literal

LESSON_KINDS = ("lesson", "experience")

_UNIVERSE = """
iso AS MATERIALIZED (
    SELECT COALESCE(array_agg(p.project_id), '{}')::bigint[] AS excluded,
           COALESCE(bool_or(p.project_id = %(home)s::bigint), false) AS home_excluded
      FROM projects p WHERE p.policy->>%(xkey)s = 'exclude'
),
lv AS MATERIALIZED (
    SELECT mv.version_id, mv.logical_id, mv.device_scope
      FROM memory_versions mv, iso
     WHERE mv.project_ids && %(pids)s::bigint[]
       AND CASE WHEN iso.home_excluded THEN mv.project_ids <@ ARRAY[%(home)s::bigint]
                ELSE NOT (mv.project_ids && iso.excluded) END
       AND mv.device_scope = ANY(%(scopes)s)
       AND mv.kind = ANY(%(kinds)s)
       AND mv.status = 'active'
       AND mv.valid_from <= %(at)s AND mv.valid_to > %(at)s
       AND mv.recorded_at <= %(at)s AND mv.superseded_at > %(at)s
)
"""


@dataclass(slots=True)
class RiskFilter:
    pids: list[int]
    scopes: list[str]
    at: datetime
    kinds: tuple[str, ...] = LESSON_KINDS
    home: int | None = None  # the checking project (the isolation rule is relative to it)

    def params(self) -> dict[str, Any]:
        return {
            "pids": self.pids,
            "scopes": self.scopes,
            "at": self.at,
            "kinds": list(self.kinds),
            "home": self.home,
            "xkey": CROSS_PROJECT_POLICY,
        }


@dataclass(slots=True)
class RiskRow:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    title: str
    body: str


async def universe_size(conn: AsyncConnection, f: RiskFilter) -> int:
    cur = await conn.execute(f"WITH {_UNIVERSE} SELECT count(*) FROM lv", f.params(), prepare=False)
    return int((await cur.fetchone())[0])


async def lexical(conn: AsyncConnection, f: RiskFilter, terms_text: str, limit: int) -> list[Candidate]:
    if not terms_text:
        return []
    cur = await conn.execute(
        f"""
        WITH q AS (
            SELECT string_agg(format('%%L:*', lex), ' | ')::tsquery AS tsq
            FROM unnest(tsvector_to_array(to_tsvector('simple', %(terms)s))) AS lex
        ), {_UNIVERSE}
        SELECT c.chunk_id, c.version_id, lv.logical_id, lv.device_scope, ts_rank_cd(c.tsv, q.tsq, 32) AS r
          FROM q, lv JOIN chunks c ON c.version_id = lv.version_id
         WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq
         ORDER BY r DESC, c.chunk_id ASC
         LIMIT %(limit)s
        """,
        {**f.params(), "terms": terms_text, "limit": limit},
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


async def title(conn: AsyncConnection, f: RiskFilter, terms: list[str], limit: int) -> list[Candidate]:
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
        ), {_UNIVERSE}, m AS MATERIALIZED (
            SELECT mv.version_id, lv.logical_id, lv.device_scope, ts_rank_cd({TITLE_TSV}, q.tsq, 32) AS r
              FROM q, lv JOIN memory_versions mv ON mv.version_id = lv.version_id
             WHERE q.tsq IS NOT NULL AND {TITLE_TSV} @@ q.tsq
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


async def trigram(
    conn: AsyncConnection, f: RiskFilter, ident_terms: list[str], limit: int
) -> list[Candidate]:
    """GIN first (``term <% text_norm`` at the caller's ``SET LOCAL`` threshold), then the universe."""
    if not ident_terms:
        return []
    cur = await conn.execute(
        f"""
        WITH {_UNIVERSE}, matches AS MATERIALIZED (
            SELECT c.chunk_id, c.version_id, word_similarity(t.term, c.text_norm) AS sim
              FROM unnest(%(terms)s::text[]) AS t(term)
              JOIN chunks c ON t.term <%% c.text_norm
        )
        SELECT m.chunk_id, m.version_id, lv.logical_id, lv.device_scope, max(m.sim) AS sim
          FROM matches m JOIN lv ON lv.version_id = m.version_id
         GROUP BY m.chunk_id, m.version_id, lv.logical_id, lv.device_scope
         ORDER BY sim DESC, m.chunk_id ASC
         LIMIT %(limit)s
        """,
        {**f.params(), "terms": ident_terms, "limit": limit},
        prepare=False,
    )
    return [Candidate(*r) for r in await cur.fetchall()]


async def vector(
    conn: AsyncConnection,
    f: RiskFilter,
    qvec: Any,
    *,
    model: str,
    revision: str,
    preproc_version: int,
    limit: int,
) -> list[Candidate]:
    """Exact cosine distance over the universe's chunks (``score`` = distance, ascending)."""
    cur = await conn.execute(
        f"""
        WITH {_UNIVERSE}
        SELECT c.chunk_id, c.version_id, lv.logical_id, lv.device_scope, (e.vec <=> %(q)s::vector) AS d
          FROM lv
          JOIN chunks c ON c.version_id = lv.version_id
          JOIN embeddings e ON e.chunk_id = c.chunk_id
         WHERE e.model = %(model)s AND e.model_revision = %(rev)s AND e.preproc_version = %(preproc)s
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


async def rows(conn: AsyncConnection, version_ids: list[int]) -> dict[int, RiskRow]:
    if not version_ids:
        return {}
    cur = await conn.execute(
        "SELECT version_id, logical_id, project_id, project_ids, device_scope, kind, title, body"
        " FROM memory_versions WHERE version_id = ANY(%s)",
        (version_ids,),
    )
    out: dict[int, RiskRow] = {}
    for r in await cur.fetchall():
        out[int(r[0])] = RiskRow(int(r[0]), int(r[1]), int(r[2]), [int(x) for x in r[3]], *r[4:])
    return out


async def chunk_offsets(conn: AsyncConnection, chunk_ids: list[int]) -> dict[int, tuple[int, int]]:
    """``chunk_id → (char_start, char_end)`` in its version's body (the judge's text window)."""
    if not chunk_ids:
        return {}
    cur = await conn.execute(
        "SELECT chunk_id, char_start, char_end FROM chunks WHERE chunk_id = ANY(%s)",
        (sorted(set(chunk_ids)),),
    )
    return {int(c): (int(a), int(b)) for c, a, b in await cur.fetchall()}


async def project_slugs(conn: AsyncConnection, pids: list[int]) -> dict[int, str]:
    if not pids:
        return {}
    cur = await conn.execute("SELECT project_id, slug FROM projects WHERE project_id = ANY(%s)", (pids,))
    return {int(p): str(s) for p, s in await cur.fetchall()}


@dataclass(slots=True)
class DeviceNow:
    status: str
    token_generation: int
    device_class: str
    is_admin: bool
    expired: bool


async def device_now(conn: AsyncConnection, device_id: int) -> DeviceNow | None:
    """The device under the same locks as ``auth.resolve`` (shared device-access advisory lock,
    then the row FOR SHARE): the post-judge authority re-check (D-062)."""
    await lock_device_access(conn, device_id)
    cur = await conn.execute(
        """
        SELECT d.status, d.token_generation, d.class, d.is_admin,
               COALESCE((to_jsonb(d)->>'expires_at')::timestamptz <= now(), false)
          FROM devices d WHERE d.device_id = %s FOR SHARE
        """,
        (device_id,),
    )
    row = await cur.fetchone()
    return (
        None if row is None else DeviceNow(str(row[0]), int(row[1]), str(row[2]), bool(row[3]), bool(row[4]))
    )


async def visible_versions(conn: AsyncConnection, f: RiskFilter, version_ids: list[int]) -> set[int]:
    """Which of ``version_ids`` are still current, active lessons/experiences visible under ``f``."""
    if not version_ids:
        return set()
    cur = await conn.execute(
        f"WITH {_UNIVERSE} SELECT version_id FROM lv WHERE version_id = ANY(%(vids)s)",
        {**f.params(), "vids": version_ids},
        prepare=False,
    )
    return {int(r[0]) for r in await cur.fetchall()}


async def all_projects(conn: AsyncConnection) -> list[int]:
    """Every project id (the admin device reads all projects, §2)."""
    cur = await conn.execute("SELECT project_id FROM projects ORDER BY 1")
    return [int(r[0]) for r in await cur.fetchall()]


__all__ = [
    "LESSON_KINDS",
    "DeviceNow",
    "RiskFilter",
    "RiskRow",
    "all_projects",
    "chunk_offsets",
    "lexical",
    "project_slugs",
    "rows",
    "title",
    "trigram",
    "device_now",
    "universe_size",
    "vector",
    "visible_versions",
]
