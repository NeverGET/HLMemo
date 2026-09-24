"""W2b candidate selection: scope-parameterized hybrid retrieval for one subject version.

Per subject V (a new version under review), with the triggering device's CURRENT grants and scope
(``db/librarian_queries.readable_projects``):

* same-project list: current items whose ``project_ids`` contain V's home project, of a kind
  compatible with V (``COMPATIBLE``), top ``SAME_TOP`` = 8;
* cross-project list: current ``lesson``/``experience``/``fact`` items NOT in V's home project,
  from projects the device can read, top ``CROSS_TOP`` = 5.

**Isolation** (``isolated_scope``; e2e 2026-09-24 #2): a project whose
``policy.librarian_cross_project`` is ``exclude`` (a disposable/test project) is never a candidate
source for another project's subject, and its own subjects draw candidates from itself only, so
no proposal (link, contradiction, close, ``widen_scope``) ever pairs it with another project.

Each list fuses a vector list (V's stored chunk embeddings, max cosine per candidate version) and
a lexical list (``tsquery`` OR of V's terms) with RRF (``K_RRF`` = 60, equal weights, ties by
version id). **Drop rule** (roadmap W2b): a pair whose cosine is below ``COS_MIN`` = 0.80 AND that
has no lexical hit is dropped, BEFORE the top-N cut. A lexical hit is ≥ 2 shared distinctive
terms or ≥ 1 shared identifier term; "distinctive" is a pool-local document frequency (a term in
more than ``POOL_DF_MAX`` of the retrieved pool is not distinctive): language-agnostic, no stop
lists, like D-055. Every step is deterministic (the prompt, hence the cassette key, depends on it).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from hlmemo.core.normalize import extract_terms, is_identifier

K_RRF = 60
SAME_TOP = 8
CROSS_TOP = 5
LIST_LIMIT = 40
COS_MIN = 0.80
LEX_TERMS = 24
POOL_DF_MAX = 0.30
CROSS_KINDS = ("lesson", "experience", "fact")
#: subject kind -> candidate kinds it is compared with ("compatible kinds")
COMPATIBLE: dict[str, tuple[str, ...]] = {
    "fact": ("fact", "lesson", "experience", "episode"),
    "lesson": ("lesson", "experience", "fact"),
    "experience": ("experience", "lesson", "fact"),
    "episode": ("episode", "fact"),
}
#: subject kinds that get placement only (no relation check)
PLACEMENT_ONLY = frozenset({"project_card", "session_note", "doc_chunk"})


def isolated_scope(home: int, allowed: Iterable[int], excluded: set[int]) -> list[int]:
    """The projects a subject (or a risk_check) of ``home`` may draw candidates from: an excluded
    home keeps only itself; any other home loses every excluded project (both directions)."""
    if home in excluded:
        return [p for p in allowed if p == home]
    return [p for p in allowed if p not in excluded]


def subject_terms(title: str, body: str, limit: int = 64) -> list[str]:
    return extract_terms(f"{title}\n{body[:6000]}", max_terms=limit)


def content_terms(terms: Iterable[str]) -> set[str]:
    """Terms that can carry a lexical hit: identifiers, or words of 4+ characters."""
    return {t for t in terms if is_identifier(t) or len(t) >= 4}


@dataclass(slots=True)
class Scored:
    version_id: int
    rrf: float = 0.0
    cos: float | None = None
    vector_rank: int | None = None
    lexical_rank: int | None = None
    lexical_hit: bool = False
    shared: list[str] = field(default_factory=list)


def fuse(vector: list[tuple[int, float]], lexical: list[tuple[int, float]]) -> list[Scored]:
    """RRF over the two lists (ranks from 1); ties by version id."""
    out: dict[int, Scored] = {}
    for rank, (vid, cos) in enumerate(vector, start=1):
        s = out.setdefault(vid, Scored(vid))
        s.rrf += 1.0 / (K_RRF + rank)
        s.vector_rank, s.cos = rank, cos
    for rank, (vid, _r) in enumerate(lexical, start=1):
        s = out.setdefault(vid, Scored(vid))
        s.rrf += 1.0 / (K_RRF + rank)
        s.lexical_rank = rank
    return sorted(out.values(), key=lambda s: (-round(s.rrf, 12), s.version_id))


def mark_lexical_hits(subject: set[str], cands: dict[int, set[str]], fused: list[Scored]) -> None:
    """Set ``lexical_hit``/``shared`` on every fused candidate (pool-local DF, see module doc)."""
    pool_n = max(1, len(cands))
    df: dict[str, int] = {}
    for terms in cands.values():
        for t in terms & subject:
            df[t] = df.get(t, 0) + 1
    cutoff = max(2, int(POOL_DF_MAX * pool_n))
    for s in fused:
        shared = sorted(cands.get(s.version_id, set()) & subject)
        distinctive = [t for t in shared if df.get(t, 0) <= cutoff]
        s.shared = distinctive[:8]
        s.lexical_hit = len(distinctive) >= 2 or any(is_identifier(t) and len(t) >= 3 for t in distinctive)


def select(fused: list[Scored], top: int) -> tuple[list[Scored], list[Scored]]:
    """Apply the drop rule, then keep the first ``top``. Returns ``(kept, dropped)``."""
    kept: list[Scored] = []
    dropped: list[Scored] = []
    for s in fused:
        if (s.cos is None or s.cos < COS_MIN) and not s.lexical_hit:
            dropped.append(s)
        elif len(kept) < top:
            kept.append(s)
    return kept, dropped


__all__ = [
    "COMPATIBLE",
    "COS_MIN",
    "CROSS_KINDS",
    "CROSS_TOP",
    "K_RRF",
    "LEX_TERMS",
    "LIST_LIMIT",
    "PLACEMENT_ONLY",
    "SAME_TOP",
    "Scored",
    "content_terms",
    "fuse",
    "isolated_scope",
    "mark_lexical_hits",
    "select",
    "subject_terms",
]
