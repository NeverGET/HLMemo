"""SQL for the librarian tasks (PHASE2-4-ROADMAP W2b/W2c).

Candidate retrieval is scope-PARAMETERIZED: every list takes the explicit set of projects the
triggering device may read NOW (``allowed``, already intersected with the job's enqueue-time
``question`` capability and with ``policy.librarian != off``) and the device's visible scopes. An
item qualifies only if EVERY project in its ``project_ids`` is in ``allowed`` (the privacy gate's
rule, so a candidate is never selected that the gate would then deny) and its ``device_scope`` is
``all`` or the device's class — ``device:*`` items are never candidates (never sent to an LLM).
Only current rows (``superseded_at = valid_to = 'infinity'``, ``status = 'active'``) qualify.

Two lists per subject version: the vector list (the subject's stored chunk embeddings against the
candidates' — the librarian loads no ONNX model) and the lexical list (``tsquery`` OR of the
subject's terms). Fusion and the drop rule live in ``librarian/candidates.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.core import MODEL_ID, MODEL_REVISION

PREPROC_VERSION = 1
_CURRENT = "mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity' AND mv.status = 'active'"
_SCOPE = (
    "mv.project_ids <@ %(allowed)s::bigint[] AND mv.device_scope = ANY(%(scopes)s)"
    " AND mv.kind = ANY(%(kinds)s) AND mv.logical_id <> %(self_lid)s"
)
_SAME = "%(home)s = ANY(mv.project_ids)"
_CROSS = "NOT (%(home)s = ANY(mv.project_ids))"


@dataclass(slots=True)
class SubjectRow:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    title: str
    body: str
    tags: list[str]
    pinned: bool
    importance: int | None
    stability: str
    valid_from: datetime
    current: bool
    recorded_at: datetime


@dataclass(slots=True)
class CandRow:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    title: str
    body: str
    pinned: bool
    valid_from: datetime
    recorded_at: datetime | None = None  # v2 refine direction (valid_from tie -> recorded_at)


async def load_subjects(conn: AsyncConnection, version_ids: list[int]) -> dict[int, SubjectRow]:
    if not version_ids:
        return {}
    cur = await conn.execute(
        """
        SELECT version_id, logical_id, project_id, project_ids, device_scope, kind, title, body, tags,
               pinned, importance, stability, valid_from,
               superseded_at = 'infinity' AND valid_to = 'infinity' AND status = 'active', recorded_at
          FROM memory_versions WHERE version_id = ANY(%s)
        """,
        (list(version_ids),),
    )
    return {int(r[0]): SubjectRow(*r) for r in await cur.fetchall()}


async def load_candidates(conn: AsyncConnection, version_ids: list[int]) -> dict[int, CandRow]:
    if not version_ids:
        return {}
    cur = await conn.execute(
        """
        SELECT version_id, logical_id, project_id, project_ids, device_scope, kind, title, body, pinned,
               valid_from, recorded_at
          FROM memory_versions WHERE version_id = ANY(%s)
        """,
        (list(version_ids),),
    )
    return {int(r[0]): CandRow(*r) for r in await cur.fetchall()}


async def subject_vectors(conn: AsyncConnection, version_id: int, limit: int = 4) -> list[str]:
    """The stored vectors of the subject's first ``limit`` chunks (pgvector text form)."""
    cur = await conn.execute(
        """
        SELECT e.vec::text FROM embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id
         WHERE c.version_id = %s AND e.model = %s AND e.model_revision = %s AND e.preproc_version = %s
         ORDER BY c.ordinal LIMIT %s
        """,
        (version_id, MODEL_ID, MODEL_REVISION, PREPROC_VERSION, limit),
    )
    return [r[0] for r in await cur.fetchall()]


async def embedding_state(conn: AsyncConnection, version_id: int) -> str:
    """``ready`` (every chunk embedded, or no chunks), ``pending`` (an embed job is queued or
    running) or ``missing`` (no job in flight: failed, or never enqueued)."""
    cur = await conn.execute(
        """
        SELECT (SELECT count(*) FROM chunks WHERE version_id = %(v)s),
               (SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id = c.chunk_id
                 WHERE c.version_id = %(v)s AND e.model = %(m)s AND e.model_revision = %(r)s),
               EXISTS (SELECT 1 FROM jobs WHERE kind IN ('embed', 'reembed')
                        AND (payload->>'version_id')::bigint = %(v)s AND status IN ('queued', 'running'))
        """,
        {"v": version_id, "m": MODEL_ID, "r": MODEL_REVISION},
    )
    n_chunks, n_emb, in_flight = await cur.fetchone()
    if n_emb >= n_chunks:
        return "ready"
    return "pending" if in_flight else "missing"


def _params(
    *, home: int, allowed: list[int], scopes: list[str], kinds: list[str], self_lid: int, **kw: Any
) -> dict[str, Any]:
    return {
        "home": home,
        "allowed": sorted(set(allowed)),
        "scopes": list(scopes),
        "kinds": list(kinds),
        "self_lid": self_lid,
        **kw,
    }


async def vector_list(
    conn: AsyncConnection,
    vec: str,
    *,
    cross: bool,
    home: int,
    allowed: list[int],
    scopes: list[str],
    kinds: list[str],
    self_lid: int,
    limit: int,
) -> list[tuple[int, float]]:
    """``[(version_id, cosine)]`` best first: the max cosine of any chunk of the candidate version
    to ``vec`` (exact scan, like §4.7; ties by version id)."""
    where = f"{_CURRENT} AND {_SCOPE} AND {_CROSS if cross else _SAME}"
    cur = await conn.execute(
        f"""
        SELECT mv.version_id, max(1 - (e.vec <=> %(q)s::vector)) AS cos
          FROM embeddings e
          JOIN chunks c ON c.chunk_id = e.chunk_id
          JOIN memory_versions mv ON mv.version_id = c.version_id
         WHERE e.model = %(model)s AND e.model_revision = %(rev)s AND e.preproc_version = %(pp)s
           AND {where}
         GROUP BY mv.version_id
         ORDER BY cos DESC, mv.version_id ASC
         LIMIT %(limit)s
        """,  # noqa: S608 - fixed fragments
        _params(
            home=home,
            allowed=allowed,
            scopes=scopes,
            kinds=kinds,
            self_lid=self_lid,
            q=vec,
            model=MODEL_ID,
            rev=MODEL_REVISION,
            pp=PREPROC_VERSION,
            limit=limit,
        ),
        prepare=False,
    )
    return [(int(r[0]), float(r[1])) for r in await cur.fetchall()]


async def lexical_list(
    conn: AsyncConnection,
    terms: str,
    *,
    cross: bool,
    home: int,
    allowed: list[int],
    scopes: list[str],
    kinds: list[str],
    self_lid: int,
    limit: int,
) -> list[tuple[int, float]]:
    """``[(version_id, rank)]``: OR of ``term:*`` over the ``simple`` lexemes (the §4.5 query
    construction), best chunk per version, ties by version id."""
    if not terms.strip():
        return []
    where = f"{_CURRENT} AND {_SCOPE} AND {_CROSS if cross else _SAME}"
    cur = await conn.execute(
        f"""
        WITH q AS (
            SELECT string_agg(format('%%L:*', lex), ' | ')::tsquery AS tsq
            FROM unnest(tsvector_to_array(to_tsvector('simple', %(terms)s))) AS lex
        )
        SELECT mv.version_id, max(ts_rank_cd(c.tsv, q.tsq, 32)) AS r
          FROM q, chunks c JOIN memory_versions mv ON mv.version_id = c.version_id
         WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq AND {where}
         GROUP BY mv.version_id
         ORDER BY r DESC, mv.version_id ASC
         LIMIT %(limit)s
        """,  # noqa: S608 - fixed fragments
        _params(
            home=home,
            allowed=allowed,
            scopes=scopes,
            kinds=kinds,
            self_lid=self_lid,
            terms=terms,
            limit=limit,
        ),
        prepare=False,
    )
    return [(int(r[0]), float(r[1])) for r in await cur.fetchall()]


async def cosines(conn: AsyncConnection, vecs: list[str], version_ids: list[int]) -> dict[int, float]:
    """Max cosine between any of ``vecs`` and any chunk of each of ``version_ids``."""
    if not vecs or not version_ids:
        return {}
    out: dict[int, float] = {}
    for vec in vecs:
        cur = await conn.execute(
            """
            SELECT c.version_id, max(1 - (e.vec <=> %(q)s::vector))
              FROM embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id
             WHERE c.version_id = ANY(%(v)s) AND e.model = %(m)s AND e.model_revision = %(r)s
               AND e.preproc_version = %(pp)s
             GROUP BY c.version_id
            """,
            {"q": vec, "v": list(version_ids), "m": MODEL_ID, "r": MODEL_REVISION, "pp": PREPROC_VERSION},
            prepare=False,
        )
        for vid, cos in await cur.fetchall():
            out[int(vid)] = max(out.get(int(vid), -1.0), float(cos))
    return out


async def readable_projects(
    conn: AsyncConnection, device_id: int, capable: list[int]
) -> tuple[bool, str | None, list[int]]:
    """The triggering device NOW: ``(trusted, class, allowed)`` where ``allowed`` = projects it
    holds a current grant on (read or better) ∩ ``capable`` ∩ ``policy.librarian != off``. The
    admin device reads every project, still capped by ``capable``."""
    cur = await conn.execute(
        """
        SELECT d.status = 'trusted' AND NOT COALESCE(d.expires_at <= now(), false), d.class, d.is_admin
          FROM devices d WHERE d.device_id = %s
        """,
        (device_id,),
    )
    row = await cur.fetchone()
    if row is None or not row[0]:
        return False, None, []
    _ok, device_class, is_admin = row
    cur = await conn.execute(
        """
        SELECT p.project_id FROM projects p
         WHERE p.project_id = ANY(%(cap)s) AND COALESCE(p.policy->>'librarian', 'on') <> 'off'
           AND p.archived_at IS NULL
           AND (%(admin)s OR EXISTS (SELECT 1 FROM device_project_grants g
                                      WHERE g.device_id = %(d)s AND g.project_id = p.project_id
                                        AND g.revoked_at IS NULL))
         ORDER BY p.project_id
        """,
        {"cap": sorted(set(capable)), "admin": bool(is_admin), "d": device_id},
    )
    return True, str(device_class), [int(r[0]) for r in await cur.fetchall()]


async def supersession_among(
    conn: AsyncConnection,
    logical_ids: list[int],
    *,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
) -> tuple[set[int], list[tuple[int, int]]]:
    """D-057 read side, ONE query: ``(hidden, partial)`` over the live ``supersedes`` links between
    two ids of ``logical_ids`` (the link row passes authz (a) and is live at ``(valid_at,
    known_at)``; links exist only once applied — a proposal is a question row).

    ``hidden``: the superseded ids of whole-item links (no ``props.scope``, or ``whole``).
    ``partial``: ``(superseding, superseded)`` pairs of fact-level links (``props.scope = part``,
    D-076): the superseded item still holds valid statements, so it is never hidden, only ranked
    after the item that replaced one of its statements (``core/supersession``)."""
    from hlmemo.db.read_queries import AUTHZ_L, TEMPORAL_L

    if len(logical_ids) < 2:
        return set(), []
    cur = await conn.execute(
        f"""
        SELECT DISTINCT l.src_logical_id, l.dst_logical_id, COALESCE(l.props->>'scope', 'whole') = 'part'
          FROM links l
         WHERE l.rel = 'supersedes' AND l.src_logical_id = ANY(%(lids)s) AND l.dst_logical_id = ANY(%(lids)s)
           AND l.src_logical_id <> l.dst_logical_id AND {AUTHZ_L} AND {TEMPORAL_L}
        """,  # noqa: S608 - fixed fragments
        {
            "lids": sorted(set(logical_ids)),
            "pid": pid,
            "scopes": scopes,
            "valid_at": valid_at,
            "known_at": known_at,
        },
    )
    hidden: set[int] = set()
    partial: set[tuple[int, int]] = set()
    for src, dst, is_part in await cur.fetchall():
        if is_part:
            partial.add((int(src), int(dst)))
        else:
            hidden.add(int(dst))
    return hidden, sorted(partial)


async def superseded_among(
    conn: AsyncConnection,
    logical_ids: list[int],
    *,
    pid: int,
    scopes: list[str],
    valid_at: datetime,
    known_at: datetime,
) -> set[int]:
    """The hidden ids of ``supersession_among`` (whole-item supersession only)."""
    hidden, _partial = await supersession_among(
        conn, logical_ids, pid=pid, scopes=scopes, valid_at=valid_at, known_at=known_at
    )
    return hidden


async def project_slugs(conn: AsyncConnection, project_ids: list[int]) -> dict[int, str]:
    if not project_ids:
        return {}
    cur = await conn.execute(
        "SELECT project_id, slug FROM projects WHERE project_id = ANY(%s)", (sorted(set(project_ids)),)
    )
    return {int(r[0]): r[1] for r in await cur.fetchall()}


__all__ = [
    "PREPROC_VERSION",
    "CandRow",
    "SubjectRow",
    "cosines",
    "embedding_state",
    "lexical_list",
    "load_candidates",
    "load_subjects",
    "project_slugs",
    "readable_projects",
    "subject_vectors",
    "superseded_among",
    "supersession_among",
    "vector_list",
]
