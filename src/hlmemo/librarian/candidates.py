"""W2b candidate selection: scope-parameterized hybrid retrieval for one subject version.

Per subject V (a new version under review), with the triggering device's CURRENT grants and scope
(``db/librarian_queries.readable_projects``):

* same-project list: current items whose ``project_ids`` contain V's home project, of a kind
  compatible with V (``COMPATIBLE``), top ``SAME_TOP`` = 8;
* cross-project list: current ``lesson``/``experience``/``fact`` items NOT in V's home project,
  from projects the device can read, top ``CROSS_TOP`` = 5.

**Isolation** (e2e 2026-09-24 #2, Sol 54): a project whose ``policy.librarian_cross_project`` is
``exclude`` (a disposable/test project) never takes part in a relation with anything outside it.
ONE rule (``relation_allowed``) covers every pairing — candidates, risk_check, and the apply-time
recheck of every proposal: the union of the projects both sides touch contains no excluded project,
or it is exactly one project. So an item that touches an excluded project T (also a multi-project
item [A, T]) is a candidate only for a subject that lies entirely in T; a subject in T sees only
items that lie entirely in T; a subject that spans T and another project gets no candidate at all.

Each list fuses a vector list (V's stored chunk embeddings, max cosine per candidate version) and
a lexical list (``tsquery`` OR of V's terms) with RRF (``K_RRF`` = 60, equal weights, ties by
version id). **Drop rule** (roadmap W2b): a pair whose cosine is below ``COS_MIN`` = 0.80 AND that
has no lexical hit is dropped, BEFORE the top-N cut. A lexical hit is ≥ 2 shared distinctive
terms or ≥ 1 shared identifier term; "distinctive" is a pool-local document frequency (a term in
more than ``POOL_DF_MAX`` of the retrieved pool is not distinctive): language-agnostic, no stop
lists, like D-055. Every step is deterministic (the prompt, hence the cassette key, depends on it).

``doc_chunk`` (e2e #3, D-076): document chunks take part in the contradiction review. They are
candidates of fact / lesson / doc_chunk subjects in a list of their OWN (same project only, top
``DOC_TOP`` = 3), so they never displace fact/lesson candidates from the top 8; a doc_chunk is a
SUBJECT only when it carries dated or decision content (``reviewable``), otherwise it gets
placement only. Pairs with a doc_chunk raise contradictions only (``guards.pre_verify``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from hlmemo.core.normalize import extract_terms, is_identifier

K_RRF = 60
SAME_TOP = 8
CROSS_TOP = 5
DOC_TOP = 3
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
    "doc_chunk": ("fact", "lesson"),  # only when ``reviewable`` (dated/decision content)
}
#: subject kind -> the kinds of its reserved same-project document list (top ``DOC_TOP``)
DOC_KINDS: dict[str, tuple[str, ...]] = {
    "fact": ("doc_chunk",),
    "lesson": ("doc_chunk",),
    "doc_chunk": ("doc_chunk",),
}
#: subject kinds that never get a cross-project list (a document is project-local)
SAME_PROJECT_ONLY = frozenset({"doc_chunk"})
#: subject kinds that get placement only (no relation check)
PLACEMENT_ONLY = frozenset({"project_card", "session_note"})
#: dated or decision content: an ISO date, a decision id, or a decision/supersession word (EN/TR/DE)
_DATED = re.compile(
    r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\bD-\d{2,4}\b|\b(?:decided|decision|decisions|superseded|"
    r"deprecated|replaced|no longer|karar\w*|entschied\w*|entscheidung\w*|ersetzt)\b",
    re.IGNORECASE,
)
#: valid_from this far from recorded_at = an explicit evidence date (D-072 import rule)
EVIDENCE_DATE_S = 300.0


def reviewable(kind: str, title: str, body: str, valid_from: datetime, recorded_at: datetime) -> bool:
    """Whether a subject gets the relation review: every compatible kind, a ``doc_chunk`` only
    when it carries dated/decision content (text match, or a valid_from that is an explicit
    evidence date rather than the write time)."""
    if kind in PLACEMENT_ONLY or kind not in COMPATIBLE:
        return False
    if kind != "doc_chunk":
        return True
    if abs((recorded_at - valid_from).total_seconds()) > EVIDENCE_DATE_S:
        return True
    return bool(_DATED.search(f"{title}\n{body[:6000]}"))


def relation_allowed(projects: Iterable[int], excluded: set[int]) -> bool:
    """May two sides that together touch ``projects`` be related (candidate, proposal, apply)?
    Yes unless they touch an excluded project AND anything else."""
    touched = set(projects)
    return not (touched & excluded) or len(touched) == 1


def isolated_scope(subject_projects: Iterable[int], allowed: Iterable[int], excluded: set[int]) -> list[int]:
    """The projects a candidate of a subject touching ``subject_projects`` may lie in (candidates
    must satisfy ``project_ids ⊆ result``), under ``relation_allowed``: a subject in exactly one
    excluded project keeps that project; a subject touching an excluded project and another one
    gets none; any other subject loses every excluded project."""
    own = set(subject_projects)
    if own & excluded:
        return [p for p in allowed if p in own] if len(own) == 1 else []
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
    "DOC_KINDS",
    "DOC_TOP",
    "SAME_PROJECT_ONLY",
    "reviewable",
    "K_RRF",
    "LEX_TERMS",
    "LIST_LIMIT",
    "PLACEMENT_ONLY",
    "SAME_TOP",
    "Scored",
    "content_terms",
    "fuse",
    "isolated_scope",
    "relation_allowed",
    "mark_lexical_hits",
    "select",
    "subject_terms",
]
