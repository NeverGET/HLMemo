"""Retrieval core (PHASE0-SPEC §4 steps 3, 8-12): term split, RRF fusion, dedupe, ordering, card
assembly and budget packing. Pure and DB-free; ``core/read_service.py`` feeds it candidate lists.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from hlmemo.core.budget import BudgetTooSmall, Meter
from hlmemo.core.clues import encode_clue
from hlmemo.core.normalize import extract_terms, identifier_terms, is_identifier, normalize
from hlmemo.core.temporal import fmt_ts
from hlmemo.core.term_stats import ProjectStats
from hlmemo.db.read_queries import Candidate, HitRow

# §4 constants (D-024: equal weights, no kind multipliers, no decay in Phase 0)
K_RRF = 60
L_MAX = 100
T_MAX = 20
V_MAX = 100
W_L = 1.0
W_T = 1.0
W_V = 1.0
W_TI = 1.0  # D-055 title list
TI_MAX = 20
PREVIEW_TOK = 48
PREVIEW_EXT = 120
EXT_MIN_REMAINING = 96
EXT_TOP = 3
# D-055: room kept free while packing hits so step (d) can actually extend the top-3 previews
# (without it the greedy hit packing always left < EXT_MIN_REMAINING tokens). Capped at a quarter
# of the space left after the card, so small budgets keep their hits.
EXT_RESERVE = EXT_TOP * (PREVIEW_EXT - PREVIEW_TOK)
REFILL_SLACK = 8  # a refill candidate whose additive estimate overshoots by more is not re-measured
PREVIEW_LEAD_DIV = 4  # a centred preview starts ~1/4 window before its first query-term match
ELLIPSIS = "…"
# D-055 query-term filtering: a term whose prefix DF exceeds DF_MAX_FRAC of the project's shared
# chunks is dropped from the lexical (and, against title DF, the title) list. Identifiers always
# survive; below DF_MIN_DOCS documents the statistic is too noisy and nothing is dropped; when
# every term is common the DF_FALLBACK_KEEP rarest are kept (never an empty lexical list).
DF_MAX_FRAC = 0.20
DF_MIN_DOCS = 100
DF_FALLBACK_KEEP = 2
_RAW_TERM_RE = re.compile(r"[\w][\w./-]*")
TERM_SCAN_MAX = 96  # terms read from the query before filtering (then capped at TERM_MAX)
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
    terms: list[str]  # §4.3 terms (first TERM_MAX)
    identifiers: list[str]
    lexical: list[str] = field(default_factory=list)  # D-055: DF-filtered terms for the lexical list
    title: list[str] = field(default_factory=list)  # D-055: raw query tokens of the title-DF survivors

    @property
    def lexical_text(self) -> str:
        return " ".join(self.lexical)

    @property
    def preview_terms(self) -> list[str]:
        """Terms a preview window is centred on: the discriminative ones, else all."""
        return self.lexical or self.terms


def select_terms(
    terms: Sequence[str],
    df: Callable[[str], int],
    n_docs: int,
    *,
    max_frac: float = DF_MAX_FRAC,
    min_docs: int = DF_MIN_DOCS,
    fallback: int = DF_FALLBACK_KEEP,
) -> list[str]:
    """D-055: drop terms whose document frequency ``df(term)`` exceeds ``max_frac · n_docs``.

    Language-agnostic (no stop lists). Identifier terms (§4.3) always survive, and so do rare
    tokens by construction. Below ``min_docs`` nothing is dropped. If every term is common, the
    ``fallback`` rarest are kept (ties by query order), so a non-empty input never yields an empty
    list unless ``fallback == 0``. Order follows the query. Deterministic.
    """
    terms = list(terms)
    if n_docs < min_docs or not terms:
        return terms
    cutoff = max(1, int(max_frac * n_docs))
    dfs = [df(t) for t in terms]
    kept = [t for t, d in zip(terms, dfs, strict=True) if is_identifier(t) or d <= cutoff]
    if kept or fallback <= 0:
        return kept
    rarest = sorted(range(len(terms)), key=lambda i: (dfs[i], i))[:fallback]
    return [terms[i] for i in sorted(rarest)]


def raw_terms(query: str, keep: Sequence[str]) -> list[str]:
    """The query's *unnormalised* tokens whose normalised terms are in ``keep`` (D-055 title list).

    The title list applies the SQL ``hlm_title_norm()`` to these raw tokens, exactly as the index
    applies it to raw titles, so query and title are folded by the same function by construction.
    Tokens come from the ``[\\w][\\w./-]*`` split of the NFC query; order and first occurrence kept.
    """
    wanted = set(keep)
    out: list[str] = []
    seen: set[str] = set()
    for m in _RAW_TERM_RE.finditer(unicodedata.normalize("NFC", query)):
        tok = m.group(0).rstrip("./-")
        if tok and tok not in seen and wanted.intersection(extract_terms(tok, max_terms=TERM_SCAN_MAX)):
            seen.add(tok)
            out.append(tok)
    return out


def split_terms(query: str, stats: ProjectStats | None = None) -> QueryTerms:
    """§4.3 terms, then the D-055 DF filter against ``stats`` (``None`` = no filtering)."""
    if stats is None:
        terms = extract_terms(query, max_terms=TERM_MAX)
        return QueryTerms(terms, identifier_terms(terms), list(terms), raw_terms(query, terms))
    scanned = extract_terms(query, max_terms=TERM_SCAN_MAX)
    lexical = select_terms(scanned, stats.chunks.prefix_df, stats.chunks.n)[:TERM_MAX]
    title = select_terms(lexical, stats.titles.prefix_df, stats.titles.n, fallback=0)
    return QueryTerms(scanned[:TERM_MAX], identifier_terms(lexical), lexical, raw_terms(query, title))


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
    title_rank: int | None = None
    device_scope: str = "all"
    row: HitRow | None = None


def rrf_fuse(
    lexical: Sequence[Candidate],
    trigram: Sequence[Candidate],
    vector: Sequence[Candidate],
    title: Sequence[Candidate] = (),
) -> list[Fused]:
    """``S(c) = Σ_s w_s / (K_RRF + rank_s(c))``; ranks start at 1; absent → no contribution.

    D-055: the title list ranks *items*; its term is added to every fused chunk of the title's
    version (so the chunk the other lists prefer still wins the §4.9 dedupe), or, when no chunk of
    that version was retrieved otherwise, to the version's first chunk."""
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
    if title:
        by_version: dict[int, list[Fused]] = {}
        for f in fused.values():
            by_version.setdefault(f.version_id, []).append(f)
        for rank, c in enumerate(title, start=1):
            targets = by_version.get(c.version_id)
            if not targets:
                f = Fused(c.chunk_id, c.version_id, c.logical_id, 0.0, device_scope=c.device_scope)
                fused[c.chunk_id] = f
                targets = by_version[c.version_id] = [f]
            for f in targets:
                f.score += W_TI / (K_RRF + rank)
                f.title_rank = rank
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
_WORD_BOUNDARY = r"(?:(?<![\w])|(?<=[_./-]))"


@lru_cache(maxsize=4096)
def _norm_char(ch: str) -> str:
    return normalize(ch)


def _normalized_with_map(text: str) -> tuple[str, list[int] | None]:
    """``normalize(text)`` plus, for each normalised character, its source index in ``text``
    (``None`` = identity).

    ASCII text is only lower-cased. Otherwise, when ``normalize`` keeps the length (ü→u, ş→s, İ→i …)
    positions line up 1:1; else it is applied per character — the same mapping it applies to whole
    strings except for cross-character compositions, which can only nudge a preview window, never
    the matching semantics of the candidate lists."""
    if text.isascii():
        return text.lower(), None
    whole = normalize(text)
    if len(whole) == len(text):
        return whole, None
    out: list[str] = []
    src: list[int] = []
    for i, ch in enumerate(text):
        n = _norm_char(ch)
        out.append(n)
        src.extend([i] * len(n))
    return "".join(out), src


@lru_cache(maxsize=256)
def _term_pattern(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    """One alternative per term: identifiers anywhere, words at a word or ``_./-`` boundary (the
    ``term:*`` prefix semantics of the lexical list); longer terms first."""
    alts = []
    for i, t in sorted(enumerate(terms), key=lambda it: (-len(it[1]), it[0])):
        body = re.escape(t)
        alts.append(f"(?P<t{i}>{body})" if is_identifier(t) else f"(?P<t{i}>{_WORD_BOUNDARY}{body})")
    return re.compile("|".join(alts)) if alts else None


def term_matches(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
    """``(char offset in text, term index)`` of every query-term occurrence, in text order."""
    pat = _term_pattern(tuple(terms))
    if pat is None or not text:
        return []
    norm, src = _normalized_with_map(text)
    out: list[tuple[int, int]] = []
    for m in pat.finditer(norm):
        name = m.lastgroup
        if name is not None:
            out.append((m.start() if src is None else src[m.start()], int(name[1:])))
    return out


def query_preview(meter: Meter, text: str, terms: Sequence[str], max_tokens: int) -> str:
    """D-055 query-centred preview: the ``max_tokens``-token (o200k) window of ``text`` covering the
    most distinct query terms (then the most occurrences, then the earliest start), starting
    ``max_tokens // 4`` tokens before a match; a leading ``…`` marks a window that does not start
    at the chunk start. No match, or a text of at most ``max_tokens`` tokens → the first
    ``max_tokens`` tokens, as in Phase 0. Deterministic in ``(text, terms, max_tokens)``.
    """
    head, n_tok = meter.head(text, max_tokens)
    if n_tok <= max_tokens:
        return text
    matches = term_matches(text, terms)
    if not matches:
        return head
    offs = meter.token_offsets(text)
    n_tok = len(offs)
    width = max_tokens - 1  # room for the ellipsis
    pos = [(bisect_right(offs, c) - 1, t) for c, t in matches]
    lead = max_tokens // PREVIEW_LEAD_DIV
    best: tuple[int, int, int] | None = None
    for p, _ in pos:
        start = max(0, min(p - lead, n_tok - width))
        inside = [t for q, t in pos if start <= q < start + width]
        key = (-len(set(inside)), -len(inside), start)
        if best is None or key < best:
            best = key
    assert best is not None
    start = best[2]
    if start == 0:
        return head
    end = start + width
    window, _ = meter.truncate(text[offs[start] : offs[end] if end < n_tok else len(text)], width)
    return ELLIPSIS + window


def render_hit(meter: Meter, f: Fused, preview_tok: int, terms: Sequence[str] = ()) -> dict[str, Any]:
    row = f.row
    assert row is not None
    return {
        "clue": encode_clue(row.version_id, row.ordinal),
        "kind": row.kind,
        "title": row.title,
        "preview": query_preview(meter, row.text, terms, preview_tok),
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
    *,
    cap: int | None = None,
) -> tuple[int, int]:
    """Largest prefix ``n ≤ n_total`` such that the exact measure of ``envelope`` after ``apply(n)``
    is ``≤ cap`` (default ``budget``; ``budget`` is always the reported ``limit``) — measure-after-
    each-append, §4.12, with the O(n²) full re-measure replaced by an additive estimate that is then
    verified exactly.

    ``apply(n)`` renders the first ``n`` units into ``envelope``; ``estimate(n)`` is a cheap additive
    token estimate of that state. Returns ``(n, used)``; ``envelope`` is left in state ``n`` with its
    ``budget`` block settled, and ``used ≤ budget`` always holds for ``n ≥ 0``. If even ``n = 0``
    does not fit, ``BudgetTooSmall(min=<needed>)`` is raised.
    """
    cap = budget if cap is None else cap
    n = 0
    while n < n_total and estimate(n + 1) <= cap:
        n += 1
    apply(n)
    used = meter.settle(envelope, budget)
    while used > cap and n > 0:  # the estimate is not a strict BPE bound: verify and trim
        n -= 1
        apply(n)
        used = meter.settle(envelope, budget)
    if used > budget:
        raise BudgetTooSmall(budget, used)
    while n < n_total:  # fill any slack the estimate left, exactly
        apply(n + 1)
        trial = meter.settle(envelope, budget)
        if trial > cap:
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
    terms: Sequence[str] = (),
) -> dict[str, Any]:
    """§4.12: (a) envelope with empty lists, (b) card (cut at ``CARD_ALLOW``), (c) hits in order with
    ``PREVIEW_TOK`` query-centred previews (D-055), packed against ``budget - reserve`` and stopping
    at the first non-fit, (d) extend the top-3 previews to ``PREVIEW_EXT`` if >= 96 tokens remain,
    (e) D-055: refill further hits into whatever the extension left, again stopping at the first
    non-fit. ``reserve = min(EXT_RESERVE, (budget - used_after_card) // 4)``. ``used`` is the exact
    measure of the returned object and never exceeds ``budget``. ``hits`` may be a prefix of the
    deduped set (rows are fetched lazily); ``total`` is the full deduped count for ``omitted``.
    Previews are rendered lazily, only for hits the packer reaches.
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

    rendered: list[dict[str, Any]] = []
    prefix = [0]

    def ensure(k: int) -> None:
        while len(rendered) < k:
            h = render_hit(meter, hits[len(rendered)], PREVIEW_TOK, terms)
            rendered.append(h)
            prefix.append(prefix[-1] + meter.count(h) + 1)

    def apply(k: int) -> None:
        ensure(k)
        envelope["hits"] = rendered[:k]
        envelope["omitted"] = total - k

    def estimate(k: int) -> int:
        ensure(k)
        return base + prefix[k]

    base = used
    reserve = min(EXT_RESERVE, max(0, (budget - base) // 4))
    n, used = pack_prefix(meter, envelope, budget, len(hits), apply, estimate, cap=budget - reserve)
    packed = envelope["hits"]

    if budget - used >= EXT_MIN_REMAINING:
        for i in range(min(EXT_TOP, n)):
            previous = packed[i]
            packed[i] = render_hit(meter, hits[i], PREVIEW_EXT, terms)
            trial = meter.settle(envelope, budget)
            if trial > budget:
                packed[i] = previous
                used = meter.settle(envelope, budget)
                break
            used = trial

    while n < len(hits):  # (e) refill what the reserve left unused
        ensure(n + 1)
        if used + (prefix[n + 1] - prefix[n]) > budget + REFILL_SLACK:
            break  # clearly does not fit: skip the exact re-measure
        envelope["hits"] = [*packed, rendered[n]]
        envelope["omitted"] = total - (n + 1)
        trial = meter.settle(envelope, budget)
        if trial > budget:
            envelope["hits"] = packed
            envelope["omitted"] = total - n
            used = meter.settle(envelope, budget)
            break
        packed, used, n = envelope["hits"], trial, n + 1

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
    "DF_MAX_FRAC",
    "DF_MIN_DOCS",
    "EXT_RESERVE",
    "TI_MAX",
    "CardInput",
    "Fused",
    "QueryTerms",
    "card_allow",
    "dedupe_and_order",
    "pack_prefix",
    "pack_query",
    "query_preview",
    "render_card",
    "render_hit",
    "rrf_fuse",
    "select_terms",
    "split_terms",
    "term_matches",
]
