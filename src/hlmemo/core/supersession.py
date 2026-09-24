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
3. **Fact-level supersession** (``demote_partially_superseded``, D-076, Sol 56 #3): a live
   ``supersedes`` link with ``props.scope = part`` says that ONE statement of the older item (the
   link's quoted span) is outdated while its other statements stay valid, so the item is never
   hidden. It is demoted below the item that replaced the statement ONLY when the query matched
   that outdated statement: the hit's matched chunk contains the quoted span AND the chunk's
   statement(s) that share the most query terms overlap the span (with no shared term at all, only
   when the span is most of the chunk). A query that matches a still-valid statement of the same
   item leaves it in place. Runs on the fetched head only (after ``n_fetch``), so no hit leaves the
   fetched set; several constraints (multi-hop chains A←B←C) are resolved by ONE stable
   topological order (smallest original rank first; a cycle keeps its original order). A demoted
   hit's displayed score is capped at its predecessor's (scores stay non-increasing). Without
   such links (every database before the librarian applies one) the order is unchanged.
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


def matched_in_span(chunk_text: str, quote: str, query_terms: set[str]) -> bool:
    """Did the query match the OUTDATED statement (``quote``) of this chunk? See rule 3."""
    span = _flat(quote)
    chunk = _flat(chunk_text)
    if len(span.split()) < 2 or span not in chunk:
        return False  # the matched chunk does not hold the outdated statement
    scored = [(len(set(extract_terms(st)) & query_terms), _flat(st)) for st in _statements(chunk_text)]
    best = max(score for score, _st in scored)
    if best == 0:  # a semantic match with no shared term: only when the span is most of the chunk
        return len(span.split()) * 2 >= len(chunk.split())
    return all(span in st or st in span for score, st in scored if score == best and st)


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
        if src not in pos or dst not in pos or src == dst:
            continue
        row = hits[pos[dst]].row
        if row is None or not matched_in_span(row.text, quote, terms):
            continue
        if dst in succ.get(src, []):
            continue
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
    seen = set(order)
    order += [i for i in range(len(hits)) if i not in seen]  # a cycle keeps its original order
    out = [hits[i] for i in order]
    for k in range(1, len(out)):
        if out[k].score > out[k - 1].score:
            out[k].score = out[k - 1].score
    return out


__all__ = ["demote_partially_superseded", "matched_in_span", "newer_first_on_ties"]
