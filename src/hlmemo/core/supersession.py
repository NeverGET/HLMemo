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
3. **Fact-level supersession** (``demote_partially_superseded``, D-076): a live ``supersedes`` link
   with ``props.scope = part`` says that ONE statement of the older item is outdated while its
   other statements stay valid, so the item is never hidden; when both are hits and the partly
   outdated item ranks above the item that replaced its statement, it is moved to just after it
   (its displayed score capped at that item's, so scores stay non-increasing). Without such links
   (every database before the librarian applies one) the order is unchanged.
"""

from __future__ import annotations

from typing import Any

from hlmemo.core.normalize import normalize


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


def demote_partially_superseded(ordered: list[Any], pairs: list[tuple[int, int]]) -> list[Any]:
    """Rule 3: for each ``(superseding, partly superseded)`` logical-id pair (sorted, applied in
    order), a partly superseded hit ranked ABOVE its superseding hit moves to just after it."""
    out = list(ordered)
    for src, dst in sorted(pairs):
        pos = {f.logical_id: i for i, f in enumerate(out)}
        if src not in pos or dst not in pos or pos[dst] > pos[src]:
            continue
        moved = out.pop(pos[dst])
        at = pos[src]  # the superseding hit shifted up by one
        if moved.score > out[at - 1].score:
            moved.score = out[at - 1].score
        out.insert(at, moved)
    return out


__all__ = ["demote_partially_superseded", "newer_first_on_ties"]
