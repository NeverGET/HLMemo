"""D-057 read-side temporal supersession for ``memory.query`` (PHASE2-4-ROADMAP W2b, query/2).

Two deterministic rules, applied after RRF fusion and the §4.9 dedupe:

1. **Linked supersession** (``db/librarian_queries.superseded_among``): when two hits are joined
   by a live ``supersedes`` link (applied — a proposal is only a question row), the superseded one
   is removed from the hits. It stays reachable through drilldown/raw and at a ``valid_at`` before
   the link's ``valid_from``.
2. **Newer first on an exact tie** (``newer_first_on_ties``): among hits with exactly the same
   fused score that are near-duplicates (same normalized title) and have different
   ``valid_from``, the newer one is ranked first. No other order changes, so G3 is untouched
   unless a fixture has exact-score, same-title pairs.
3. **Fact-level supersession** (``demote_partially_superseded``, D-076, Sol 56 #3, review 57): a
   live ``supersedes`` link with ``props.scope = part`` says that ONE statement of the older item
   (the link's quoted span) is outdated while its other statements stay valid, so the item is never
   hidden. It is demoted below the item that replaced the statement ONLY when BOTH hold (D-087:
   the read side must never be worse than the measured-neutral 6a96ba1 rule, so this rule can
   only demote LESS than it): (i) the 6a96ba1 statement rule — the matched chunk contains the
   quoted span and the chunk statement(s) sharing the most query terms overlap the span; (ii) the
   review-57 clause rule — every query term found in the chunk lies INSIDE the span (a mixed or
   same-sentence-but-other-clause match is not demoted). With no query term in the chunk at all
   (a semantic match), only when the span is most of the chunk (as in 6a96ba1). Runs on the
   fetched head only (after ``n_fetch``), so no hit leaves the fetched set. Several constraints
   (chains A←B←C) form ONE stable topological order (smallest original rank first); an edge that
   would close a cycle is ignored (edges taken in a deterministic order), so a cycle never pushes
   its members below unrelated hits. A demoted hit's displayed score is capped at its
   predecessor's (scores stay non-increasing). Without such links (every database before the
   librarian applies one) the order is unchanged.
"""

from __future__ import annotations

import heapq
import re
from typing import Any

from hlmemo.core.normalize import extract_terms, normalize


def _title_key(f: Any) -> str:
    return " ".join(normalize(f.row.title).split()) if f.row is not None else ""


def newer_first_on_ties(hits: list[Any]) -> list[Any]:
    """Stable re-order of runs of equal score: within a run, hits sharing a normalized title are
    ordered by ``valid_from`` descending, keeping the positions the run's group occupied."""
    out = list(hits)
    i = 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and out[j + 1].score == out[i].score:
            j += 1
        if j > i:
            run = out[i : j + 1]
            groups: dict[str, list[int]] = {}
            for k, f in enumerate(run):
                groups.setdefault(_title_key(f), []).append(k)
            for idx in groups.values():
                if len(idx) < 2 or any(run[k].row is None for k in idx):
                    continue
                ordered = sorted((run[k] for k in idx), key=lambda f: f.row.valid_from, reverse=True)
                for k, f in zip(idx, ordered, strict=True):
                    run[k] = f
            out[i : j + 1] = run
        i = j + 1
    return out


_EDGE = " \t\n\"'`“”„‚‘’«».,;:!?()[]{}…-–—"
_LINE_MARK = re.compile(r"^\s*(?:[-*+•>|]+|\d+[.)]|#+)\s*")
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _flat(text: str) -> str:
    return " ".join(normalize(text or "").split()).strip(_EDGE)


def _statements(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").splitlines():
        line = _LINE_MARK.sub("", line).strip()
        out.extend(part for part in _SENT_SPLIT.split(line) if part.strip())
    return out or [text or ""]


def _statement_rule(chunk_text: str, span: str, chunk: str, query_terms: set[str]) -> bool:
    """The 6a96ba1 rule (measured neutral on the hold-out, D-087), unchanged."""
    scored = [(len(set(extract_terms(st)) & query_terms), _flat(st)) for st in _statements(chunk_text)]
    best = max(score for score, _st in scored)
    if best == 0:  # a semantic match with no shared term: only when the span is most of the chunk
        return len(span.split()) * 2 >= len(chunk.split())
    return all(span in st or st in span for score, st in scored if score == best and st)


def matched_in_span(chunk_text: str, quote: str, query_terms: set[str]) -> bool:
    """Did the query match the OUTDATED span (``quote``) of this chunk, and nothing else of it?
    Rule 3: the 6a96ba1 statement rule AND the review-57 clause rule, so it never demotes a hit
    the 6a96ba1 rule would keep. ``query_terms``: the query's normalized terms (``extract_terms``)."""
    span = _flat(quote)
    chunk = _flat(chunk_text)
    if len(span.split()) < 2 or span not in chunk:
        return False  # the matched chunk does not hold the outdated statement
    if not _statement_rule(chunk_text, span, chunk, query_terms):
        return False
    span_terms = set(extract_terms(span))
    rest_terms = set(extract_terms(chunk.replace(span, " | "))) - span_terms
    inside = query_terms & span_terms
    outside = query_terms & rest_terms
    if not inside and not outside:  # a semantic match: the statement rule decided (span = most of it)
        return True
    return bool(inside) and not outside  # all the evidence inside the span; mixed = ambiguous


def _reaches(succ: dict[int, list[int]], start: int, goal: int) -> bool:
    todo, seen = [start], {start}
    while todo:
        node = todo.pop()
        if node == goal:
            return True
        for nxt in succ.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return False


def demote_partially_superseded(
    hits: list[Any], links: list[tuple[int, int, str]], query_terms: list[str] | set[str]
) -> list[Any]:
    """Rule 3 on the fetched head: ``links`` = ``(superseding, partly superseded, quoted span)``
    logical-id pairs of live scope=part ``supersedes`` links."""
    pos = {f.logical_id: i for i, f in enumerate(hits)}
    terms = set(query_terms)
    succ: dict[int, list[int]] = {}
    indeg = dict.fromkeys(pos, 0)
    for src, dst, quote in sorted(links):
        if src not in pos or dst not in pos or src == dst or dst in succ.get(src, []):
            continue
        row = hits[pos[dst]].row
        if row is None or not matched_in_span(row.text, quote, terms):
            continue
        if _reaches(succ, dst, src):
            continue  # this edge would close a cycle: ignored (the earlier edges stand)
        succ.setdefault(src, []).append(dst)
        indeg[dst] += 1
    if not succ:
        return list(hits)
    ready = [pos[lid] for lid, n in indeg.items() if n == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for dst in succ.get(hits[i].logical_id, []):
            indeg[dst] -= 1
            if indeg[dst] == 0:
                heapq.heappush(ready, pos[dst])
    out = [hits[i] for i in order]  # a DAG: every hit is emitted
    for k in range(1, len(out)):
        if out[k].score > out[k - 1].score:
            out[k].score = out[k - 1].score
    return out


__all__ = ["demote_partially_superseded", "matched_in_span", "newer_first_on_ties"]
