"""Retrieval core (PHASE0-SPEC §4 steps 3, 8-12): term split, RRF fusion, dedupe, ordering, card
assembly and budget packing. Pure and DB-free; ``core/read_service.py`` feeds it candidate lists.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from hlmemo.core.budget import BudgetTooSmall, Meter
from hlmemo.core.clues import encode_clue
from hlmemo.core.normalize import extract_terms, identifier_terms
from hlmemo.core.temporal import fmt_ts
from hlmemo.db.read_queries import Candidate, HitRow

# §4 constants (D-024: equal weights, no kind multipliers, no decay in Phase 0)
K_RRF = 60
L_MAX = 100
T_MAX = 20
V_MAX = 100
W_L = 1.0
W_T = 1.0
W_V = 1.0
PREVIEW_TOK = 48
PREVIEW_EXT = 120
EXT_MIN_REMAINING = 96
EXT_TOP = 3
CARD_ALLOW_MIN = 64
CARD_ALLOW_MAX = 512
TERM_MAX = 24
# §4 names 0.1; raised to 0.9 (a spec-permitted knob) after G4 profiling: pg_trgm's GIN check keeps a
# row when matched/total trigrams of the term >= threshold, and every kept row pays a word_similarity
# recheck over the ~2 KB chunk (~0.3 ms). At 0.1 that is every chunk (3-7 s/term); at 0.9 only chunks
# containing the identifier almost verbatim (tens of ms). G3 recall is identical at 0.7/0.8/0.9.
TRGM_WORD_SIMILARITY_THRESHOLD = 0.9
SCORE_DIGITS = 6

_SCOPE_RANK = {"device": 0, "class": 1, "all": 2}


def card_allow(budget: int) -> int:
    """``clamp(floor(0.25·budget), 64, 512)``."""
    return max(CARD_ALLOW_MIN, min(CARD_ALLOW_MAX, budget // 4))


# --------------------------------------------------------------------------- step 3: terms
@dataclass(frozen=True, slots=True)
class QueryTerms:
    terms: list[str]
    identifiers: list[str]

    @property
    def lexical_text(self) -> str:
        return " ".join(self.terms)


def split_terms(query: str) -> QueryTerms:
    terms = extract_terms(query, max_terms=TERM_MAX)
    return QueryTerms(terms=terms, identifiers=identifier_terms(terms))


# --------------------------------------------------------------------------- steps 8-9: fusion
@dataclass(slots=True)
class Fused:
    chunk_id: int
    version_id: int
    logical_id: int
    score: float
    lexical_rank: int | None = None
    trigram_rank: int | None = None
    vector_rank: int | None = None
    device_scope: str = "all"
    row: HitRow | None = None


def rrf_fuse(
    lexical: Sequence[Candidate], trigram: Sequence[Candidate], vector: Sequence[Candidate]
) -> list[Fused]:
    """``S(c) = Σ_s w_s / (K_RRF + rank_s(c))``; ranks start at 1; absent → no contribution."""
    fused: dict[int, Fused] = {}

    def add(lst: Sequence[Candidate], weight: float, attr: str) -> None:
        for rank, c in enumerate(lst, start=1):
            f = fused.get(c.chunk_id)
            if f is None:
                f = fused[c.chunk_id] = Fused(
                    c.chunk_id, c.version_id, c.logical_id, 0.0, device_scope=c.device_scope
                )
            f.score += weight / (K_RRF + rank)
            setattr(f, attr, rank)

    add(lexical, W_L, "lexical_rank")
    add(trigram, W_T, "trigram_rank")
    add(vector, W_V, "vector_rank")
    return list(fused.values())


def _order_key(f: Fused) -> tuple[float, int, int, int]:
    # S' desc, device specificity (device: > class: > all), lexical rank asc (absent last), chunk_id asc
    return (
        -f.score,
        _SCOPE_RANK.get(f.device_scope.split(":", 1)[0], 3),
        f.lexical_rank if f.lexical_rank is not None else 1 << 30,
        f.chunk_id,
    )


def dedupe_and_order(fused: list[Fused]) -> list[Fused]:
    """§4.9: one chunk per ``logical_id`` (the best by the §4.9 order), then the final order."""
    ordered = sorted(fused, key=_order_key)
    best: dict[int, Fused] = {}
    for f in ordered:
        if f.logical_id not in best:
            best[f.logical_id] = f
    return list(best.values())  # already in order (insertion follows `ordered`)


# --------------------------------------------------------------------------- step 10: card
@dataclass(slots=True)
class CardInput:
    version_id: int
    body: str
    sources: list[tuple[int, bool]] = field(default_factory=list)  # (dst_version_id, stale)


def render_card(meter: Meter, card: CardInput, allow: int) -> dict[str, Any]:
    text, truncated = meter.truncate(card.body, allow)
    stale_clues = [encode_clue(vid) for vid, stale in card.sources if stale]
    return {
        "clue": encode_clue(card.version_id),
        "text": text,
        "stale": bool(stale_clues),
        "stale_clues": stale_clues,
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- steps 11-12: hits + packing
def render_hit(meter: Meter, f: Fused, preview_tok: int) -> dict[str, Any]:
    row = f.row
    assert row is not None
    preview, _ = meter.truncate(row.text, preview_tok)
    return {
        "clue": encode_clue(row.version_id, row.ordinal),
        "kind": row.kind,
        "title": row.title,
        "preview": preview,
        "score": round(f.score, SCORE_DIGITS),
        "valid_from": fmt_ts(row.valid_from),
        "tags": list(row.tags),
        "device_scope": row.device_scope,
    }


def pack_prefix(
    meter: Meter,
    envelope: dict[str, Any],
    budget: int,
    n_total: int,
    apply: Callable[[int], None],
    estimate: Callable[[int], int],
) -> tuple[int, int]:
    """Largest prefix ``n ≤ n_total`` such that the exact measure of ``envelope`` after ``apply(n)``
    is ``≤ budget`` (measure-after-each-append, §4.12, with the O(n²) full re-measure replaced by an
    additive estimate that is then verified exactly).

    ``apply(n)`` renders the first ``n`` units into ``envelope``; ``estimate(n)`` is a cheap additive
    token estimate of that state. Returns ``(n, used)``; ``envelope`` is left in state ``n`` with its
    ``budget`` block settled, and ``used ≤ budget`` always holds for ``n ≥ 0``. If even ``n = 0``
    does not fit, ``BudgetTooSmall(min=<needed>)`` is raised.
    """
    n = 0
    while n < n_total and estimate(n + 1) <= budget:
        n += 1
    apply(n)
    used = meter.settle(envelope, budget)
    while used > budget and n > 0:  # the estimate is not a strict BPE bound: verify and trim
        n -= 1
        apply(n)
        used = meter.settle(envelope, budget)
    if used > budget:
        raise BudgetTooSmall(budget, used)
    while n < n_total:  # fill any slack the estimate left, exactly
        apply(n + 1)
        trial = meter.settle(envelope, budget)
        if trial > budget:
            apply(n)
            used = meter.settle(envelope, budget)
            break
        n, used = n + 1, trial
    return n, used


def pack_query(
    meter: Meter,
    envelope: dict[str, Any],
    budget: int,
    card: CardInput | None,
    hits: list[Fused],
    *,
    total: int | None = None,
) -> dict[str, Any]:
    """§4.12: (a) envelope with empty lists, (b) card (cut at ``CARD_ALLOW``), (c) hits in order with
    ``PREVIEW_TOK`` previews, stop at the first non-fit, (d) extend the top-3 previews to
    ``PREVIEW_EXT`` if ≥ 96 tokens remain. ``used`` is the exact measure of the returned object and
    never exceeds ``budget``. ``hits`` may be a prefix of the deduped set (rows are fetched lazily);
    ``total`` is the full deduped count for ``omitted``.
    """
    total = len(hits) if total is None else total
    envelope["card"] = None
    envelope["hits"] = []
    envelope["omitted"] = total
    used = meter.settle(envelope, budget)
    if used > budget:
        raise BudgetTooSmall(budget, used)

    if card is not None:
        allow = card_allow(budget)
        envelope["card"] = render_card(meter, card, allow)
        used = meter.settle(envelope, budget)
        while used > budget and allow > 8:  # cannot happen for budget >= 256; belt and braces
            allow //= 2
            envelope["card"] = render_card(meter, card, allow)
            used = meter.settle(envelope, budget)
        if used > budget:
            envelope["card"] = None
            used = meter.settle(envelope, budget)

    rendered = [render_hit(meter, f, PREVIEW_TOK) for f in hits]
    counts = [meter.count(h) + 1 for h in rendered]
    prefix = [0]
    for c in counts:
        prefix.append(prefix[-1] + c)
    base = used

    def apply(n: int) -> None:
        envelope["hits"] = rendered[:n]
        envelope["omitted"] = total - n

    n, used = pack_prefix(meter, envelope, budget, len(rendered), apply, lambda k: base + prefix[k])
    packed = envelope["hits"]

    if budget - used >= EXT_MIN_REMAINING:
        for i in range(min(EXT_TOP, n)):
            previous = packed[i]
            packed[i] = render_hit(meter, hits[i], PREVIEW_EXT)
            trial = meter.settle(envelope, budget)
            if trial > budget:
                packed[i] = previous
                used = meter.settle(envelope, budget)
                break
            used = trial

    assert used <= budget and envelope["budget"]["used"] == used
    return envelope


__all__ = [
    "CARD_ALLOW_MAX",
    "CARD_ALLOW_MIN",
    "K_RRF",
    "L_MAX",
    "PREVIEW_EXT",
    "PREVIEW_TOK",
    "T_MAX",
    "TRGM_WORD_SIMILARITY_THRESHOLD",
    "V_MAX",
    "CardInput",
    "Fused",
    "QueryTerms",
    "card_allow",
    "dedupe_and_order",
    "pack_prefix",
    "pack_query",
    "render_card",
    "render_hit",
    "rrf_fuse",
    "split_terms",
]
