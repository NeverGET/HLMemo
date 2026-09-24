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
3. **Fact-level supersession** (``demote_partially_superseded``; D-076, D-087, reviews 56/57/60):
   a live ``supersedes`` link with ``props.scope = part`` says that ONE statement of the older item
   (the link's quoted span) is outdated while its other statements stay valid, so the item is never
   hidden. An ordering constraint "after the item that replaced the statement" exists ONLY when
   (i) the 6a96ba1 statement rule holds (the matched chunk contains the span and its statement(s)
   sharing the most query terms overlap it; no shared term at all: only if the span is most of the
   chunk) AND (ii) the span occurs exactly once, on term boundaries, and every query term found in
   the chunk occurs inside it and NOT anywhere outside it, the whole chunk scanned (a term that
   also matches the valid part — "port" in "API uses port 8080 and backups use port 9090" — or a
   second copy of the span means no demotion; review 61). Constraints inside a cyclic component
   are all ignored (the original interleaving stays). The hits are then placed so that every
   constraint holds and EVERY hit ranks no worse than max(its baseline rank, its 6a96ba1 rank)
   (D-087: never worse than the
   measured-neutral read side): each hit gets that deadline and Lawler's backward rule (place last,
   among the hits whose constraints allow it, the one with the latest deadline; ties: the later
   baseline rank) meets every deadline, because the 6a96ba1 order itself does. Runs on the fetched
   head only (after ``n_fetch``). A demoted hit's displayed score is capped at its predecessor's.
   Without such links (every database before the librarian applies one) the order is unchanged.
"""

from __future__ import annotations

import heapq
import re
from typing import Any

from hlmemo.core.normalize import extract_terms, normalize, term_spans


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


def _old_rule(chunk_text: str, quote: str, query_terms: set[str]) -> bool:
    """The complete 6a96ba1 predicate (measured neutral on the hold-out, D-087)."""
    span = _flat(quote)
    chunk = _flat(chunk_text)
    if len(span.split()) < 2 or span not in chunk:
        return False  # the matched chunk does not hold the outdated statement
    return _statement_rule(chunk_text, span, chunk, query_terms)


def _span_starts(chunk: str, span: str) -> list[int]:
    """Every start offset of ``span`` in ``chunk``, overlapping occurrences included."""
    out: list[int] = []
    i = chunk.find(span)
    while i != -1:
        out.append(i)
        i = chunk.find(span, i + 1)
    return out


def matched_in_span(chunk_text: str, quote: str, query_terms: set[str]) -> bool:
    """Did the query match the OUTDATED span (``quote``) of this chunk, and nothing else of it?
    Rule 3 (i) AND (ii), so it never demotes a hit the 6a96ba1 rule would keep. ``query_terms``:
    the query's normalized terms (``extract_terms``).

    (ii) is occurrence-aware and scans the WHOLE rest of the chunk (review 61): the span must
    occur exactly once and on term boundaries (a second copy of the outdated statement, or a
    term the span cuts in two, leaves it open which text the query matched: no demotion), and
    no query term may occur anywhere outside that one occurrence (every term, no ``TERM_MAX``
    cap). A query sharing no term with the chunk was already decided by the statement rule
    (the span is most of the chunk)."""
    if not _old_rule(chunk_text, quote, query_terms):
        return False
    span = _flat(quote)
    chunk = _flat(chunk_text)
    starts = _span_starts(chunk, span)
    if len(starts) != 1:
        return False  # the outdated statement is quoted more than once: ambiguous
    start, end = starts[0], starts[0] + len(span)
    if any(s < start < e or s < end < e for s, e in term_spans(chunk)):
        return False  # a term of the chunk straddles the span boundary: ambiguous
    rest = f"{chunk[:start]} | {chunk[end:]}"  # everything outside the one occurrence
    outside = {rest[s:e] for s, e in term_spans(rest)}
    return not (query_terms & outside)  # any evidence outside the span (mixed) = no demotion


def _edges(
    hits: list[Any], links: list[tuple[int, int, str]], terms: set[str], rule: Any
) -> list[tuple[int, int]]:
    """``(before, after)`` hit-index pairs of the links whose partly outdated hit ``rule`` accepts
    (links taken in sorted order, one per pair)."""
    pos = {f.logical_id: i for i, f in enumerate(hits)}
    out: list[tuple[int, int]] = []
    for src, dst, quote in sorted(links):
        if src not in pos or dst not in pos or src == dst or (pos[src], pos[dst]) in out:
            continue
        row = hits[pos[dst]].row
        if row is not None and rule(row.text, quote, terms):
            out.append((pos[src], pos[dst]))
    return out


def _kahn(n: int, edges: list[tuple[int, int]]) -> tuple[list[int], set[int]]:
    """Smallest-original-rank-first topological order; nodes on or behind a cycle are left out
    (returned as the second value)."""
    succ: dict[int, list[int]] = {}
    indeg = [0] * n
    for u, v in edges:
        succ.setdefault(u, []).append(v)
        indeg[v] += 1
    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for v in succ.get(i, []):
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(ready, v)
    return order, set(range(n)) - set(order)


def order_6a96ba1(hits: list[Any], links: list[tuple[int, int, str]], terms: set[str]) -> list[int]:
    """The hit order the 6a96ba1 read side produced (hit indexes): its predicate, smallest-rank
    Kahn, the members of a cycle appended in their original order. The deadline source of rule 3."""
    order, left = _kahn(len(hits), _edges(hits, links, terms, _old_rule))
    return order + sorted(left)


def _cyclic_components(n: int, edges: list[tuple[int, int]]) -> dict[int, int]:
    """Node -> component id for the nodes of every strongly connected component with a cycle."""
    succ: dict[int, list[int]] = {}
    pred: dict[int, list[int]] = {}
    for u, v in edges:
        succ.setdefault(u, []).append(v)
        pred.setdefault(v, []).append(u)
    seen: set[int] = set()
    finish: list[int] = []
    for start in range(n):  # Kosaraju, iterative
        if start in seen:
            continue
        stack = [(start, iter(succ.get(start, [])))]
        seen.add(start)
        while stack:
            node, it = stack[-1]
            nxt = next((x for x in it if x not in seen), None)
            if nxt is None:
                stack.pop()
                finish.append(node)
            else:
                seen.add(nxt)
                stack.append((nxt, iter(succ.get(nxt, []))))
    comp: dict[int, int] = {}
    assigned: set[int] = set()
    for root in reversed(finish):
        if root in assigned:
            continue
        members, todo = [root], [root]
        assigned.add(root)
        while todo:
            for x in pred.get(todo.pop(), []):
                if x not in assigned:
                    assigned.add(x)
                    members.append(x)
                    todo.append(x)
        if len(members) > 1 or root in succ.get(root, []):
            comp.update(dict.fromkeys(members, root))
    return comp


def demote_partially_superseded(
    hits: list[Any], links: list[tuple[int, int, str]], query_terms: list[str] | set[str]
) -> list[Any]:
    """Rule 3 on the fetched head: ``links`` = ``(superseding, partly superseded, quoted span)``
    logical-id pairs of live scope=part ``supersedes`` links. See the module doc."""
    terms = set(query_terms)
    n = len(hits)
    edges = _edges(hits, links, terms, matched_in_span)
    comp = _cyclic_components(n, edges)
    edges = [(u, v) for u, v in edges if not (u in comp and comp.get(v) == comp[u])]  # cycles: ignored
    if not edges:
        return list(hits)
    old = order_6a96ba1(hits, links, terms)
    old_rank = {i: r for r, i in enumerate(old)}
    _order, old_left = _kahn(n, _edges(hits, links, terms, _old_rule))
    edges = [(u, v) for u, v in edges if not (u in old_left and v in old_left)]  # keeps 6a96ba1 feasible
    if not edges:
        return list(hits)
    deadline = [max(i, old_rank[i]) for i in range(n)]
    succ_left = [0] * n  # constraints "i before v" whose v is not placed yet
    preds: dict[int, list[int]] = {}
    for u, v in edges:
        succ_left[u] += 1
        preds.setdefault(v, []).append(u)
    remaining = set(range(n))
    backwards: list[int] = []
    while remaining:  # Lawler: place last the free hit with the latest deadline (ties: later rank)
        i = max((j for j in remaining if succ_left[j] == 0), key=lambda j: (deadline[j], j))
        backwards.append(i)
        remaining.discard(i)
        for u in preds.get(i, []):
            succ_left[u] -= 1
    out = [hits[i] for i in reversed(backwards)]
    for k in range(1, len(out)):
        if out[k].score > out[k - 1].score:
            out[k].score = out[k - 1].score
    return out


__all__ = ["demote_partially_superseded", "matched_in_span", "newer_first_on_ties", "order_6a96ba1"]
