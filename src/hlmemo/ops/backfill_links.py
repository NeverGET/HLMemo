"""D-195 / R4 (R-1, R-12): apply or revert REVIEWED supersession proposals of one project
(``hlm links backfill``). LLM-free: the automatic proposer of branch wf-supersede-backfill (candidate
generator + ``supersede_check`` task) is NOT part of this port (D-195: precision .69, rejected); the
proposals file comes from a curated, reader-verified review.

``apply`` (``--apply --proposals F``; ``--dry-run`` computes the same result and rolls back): the
``proposed`` records of the file for ``--project`` (``min_confidence`` filter; one per logical pair;
a pair proposed in both directions, or on a cycle with each other or with a live link, is dropped;
a pair a live link already joins, in either direction, is ``already_linked``) become ``supersedes``
links through the SAME path ``hlm links explicit`` writes (``ops/explicit_links``): the project's
advisory lock, the per-item locks, then UNDER THOSE LOCKS each endpoint's head is re-checked. R-1: the
apply is ALL OR NOTHING and validates the WHOLE FILE: EVERY ``proposed`` record labelled for
``--project`` (whatever it later becomes: below ``min_confidence``, a duplicate, dropped, already
linked or fresh; Astra 90 R-1) is checked under the locks. If any record's head moved (``stale``: not
the version the record names) or any endpoint does not belong to ``--project`` (``foreign``: the
head's ``project_ids`` lack the project), the WHOLE apply is rejected (``BackfillRejected``): zero
events, zero links. Otherwise
``librarian.actor.materialize`` and ONE ``librarian`` system event (operator device 1, client
``hlm-backfill``, op ``supersede_backfill``) whose ``resolved.mutations`` are the records, then
``apply_mutations``. Replayable (``db/replay`` re-applies the recorded mutations); no version changes.

``revert`` (``--revert``, which needs ``--project``): ONE event of ``link_supersede`` records for EVERY
live ``props.by = backfill`` link of the project: PROJECT-WIDE, not one apply's links
(``superseded_at``, never deleted; bi-temporal reads before it still see them).

Link ``props``: ``{by: backfill, scope, quote, span, declaration, relation, model, profile,
prompt_version, confidence, generator}``. ``span`` is the older item's outdated text and
``declaration`` the newer item's quote. ``quote`` keeps the read contract of every ``supersedes`` link
(D-057 rule 3, D-184 ``research.quote_overlaps``): for a PART-scope link the older item's span, for a
WHOLE-scope link the newer item's quote.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection

from hlmemo import __version__
from hlmemo.core.explicit_supersession import _cyclic
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import write_queries as q
from hlmemo.ops import explicit_links as xl

BY = "backfill"
CLIENT = f"hlm-backfill/{__version__}"
OP = "supersede_backfill"
OP_REVERT = "supersede_backfill_revert"


class BackfillRejected(Exception):
    """R-1: the whole apply is refused (a stale head or an endpoint outside the project); the
    caller rolls back, so nothing was written. ``details``: the offending pairs."""

    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__("backfill apply rejected: nothing was written")
        self.details = details


# ------------------------------------------------------------------ proposals file
def read_proposals(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def candidates(records: list[dict[str, Any]], slug: str) -> list[dict[str, Any]]:
    """R-1: every ``proposed`` record the file labels for ``slug`` (explicitly or by default): the
    records the whole-file validation checks, before any drop or ``already_linked`` filtering."""
    return [r for r in records if r.get("status") == "proposed" and r.get("project", slug) == slug]


def select(
    records: list[dict[str, Any]], slug: str, min_confidence: float = 0.0
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The ``proposed`` records of ``slug`` at ``min_confidence`` or more, one per logical pair
    (the most confident), minus pairs proposed in both directions and proposals on a cycle.
    Returns ``(kept, dropped counts)``."""
    dropped: Counter[str] = Counter()
    best: dict[tuple[int, int], dict[str, Any]] = {}
    for r in records:
        if r.get("status") != "proposed":
            continue
        if r.get("project", slug) != slug:
            dropped["other_project"] += 1
            continue
        if float(r.get("confidence") or 0.0) < min_confidence:
            dropped["below_min_confidence"] += 1
            continue
        key = (int(r["src_logical_id"]), int(r["dst_logical_id"]))
        if key in best:
            dropped["duplicate"] += 1
            if float(r.get("confidence") or 0.0) <= float(best[key].get("confidence") or 0.0):
                continue
        best[key] = r
    both = {k for k in best if (k[1], k[0]) in best}
    dropped["both_directions"] += len(both)
    kept = {k: r for k, r in best.items() if k not in both}
    cyclic = _cyclic(kept)
    dropped["cycle"] += len(cyclic & set(kept))
    return [r for k, r in sorted(kept.items()) if k not in cyclic], {k: v for k, v in dropped.items() if v}


def link_props(r: dict[str, Any]) -> dict[str, Any]:
    """Link ``props`` (module doc): ``quote`` follows the read contract of its scope."""
    part = r["scope"] == "part"
    return {
        "by": BY,
        "scope": r["scope"],
        "quote": r["older_span"] if part else r["newer_quote"],
        "span": r["older_span"],
        "declaration": r["newer_quote"],
        "relation": r.get("relation"),
        "model": r.get("model"),
        "profile": r.get("profile"),
        "prompt_version": r.get("prompt_version"),
        "confidence": r.get("confidence"),
        "generator": r.get("generator"),
    }


# ------------------------------------------------------------------ apply / revert
async def _lock(conn: AsyncConnection, pid: int) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(3, hashtext(%s))", (f"backfill:{pid}",))


def _pair(r: dict[str, Any]) -> tuple[int, int]:
    return int(r["src_logical_id"]), int(r["dst_logical_id"])


async def _validate(
    conn: AsyncConnection, pid: int, slug: str, whole: list[dict[str, Any]], *, selected: int
) -> None:
    """R-1, UNDER the endpoint locks: every record's heads must be the versions it names and belong
    to the project; any ``stale`` or ``foreign`` record raises ``BackfillRejected`` (all or nothing)."""
    from hlmemo.librarian.actor import head_endpoint

    stale: list[dict[str, Any]] = []
    foreign: list[dict[str, Any]] = []
    for r in whole:
        src = await head_endpoint(conn, int(r["src_logical_id"]))
        dst = await head_endpoint(conn, int(r["dst_logical_id"]))
        where = {"src_vid": int(r["src_vid"]), "dst_vid": int(r["dst_vid"])}
        if (
            src is None
            or dst is None
            or (src.version_id, dst.version_id) != (where["src_vid"], where["dst_vid"])
        ):
            stale.append(
                {
                    **where,
                    "src_head": src.version_id if src else None,
                    "dst_head": dst.version_id if dst else None,
                }
            )
        elif pid not in src.project_ids or pid not in dst.project_ids:
            foreign.append(
                {**where, "src_projects": list(src.project_ids), "dst_projects": list(dst.project_ids)}
            )
    if stale or foreign:
        raise BackfillRejected(
            {
                "project": slug,
                "validated": len(whole),
                "selected": selected,
                "stale": stale,
                "foreign": foreign,
            }
        )


async def apply(
    conn: AsyncConnection,
    slug: str,
    records: list[dict[str, Any]],
    *,
    min_confidence: float = 0.0,
    preview: bool = False,
) -> dict[str, Any]:
    """``apply`` (module doc), in the caller's transaction; the caller commits (or rolls back a
    preview). Raises ``BackfillRejected`` (nothing written) on a stale or foreign endpoint."""
    pid = await xl.project_id(conn, slug)
    await _lock(conn, pid)
    kept, dropped = select(records, slug, min_confidence)
    lids = sorted({lid for r in kept for lid in _pair(r)})
    existing = await xl.live_supersedes(conn, lids)
    already = [r for r in kept if _pair(r) in existing or _pair(r)[::-1] in existing]  # either direction
    fresh = [r for r in kept if r not in already]
    cyclic = _cyclic([*(_pair(r) for r in fresh), *existing])
    on_cycle = [r for r in fresh if _pair(r) in cyclic]
    fresh = [r for r in fresh if r not in on_cycle]
    out: dict[str, Any] = {
        "project": slug,
        "preview": preview,
        "min_confidence": min_confidence,
        "selected": len(kept),
        "dropped": {**dropped, **({"cycle_with_live": len(on_cycle)} if on_cycle else {})},
        "already_linked": len(already),
        "applied": 0,
        "event_id": None,
        "links": [],
    }
    whole = candidates(records, slug)  # R-1: the WHOLE file, not only the fresh pairs
    await q.lock_logical_ids(conn, sorted({lid for r in whole for lid in _pair(r)}))
    await _validate(conn, pid, slug, whole, selected=len(kept))
    if not fresh:
        return out
    from hlmemo.librarian.actor import materialize

    out["links"] = [
        {
            "src_vid": r["src_vid"],
            "dst_vid": r["dst_vid"],
            "scope": r["scope"],
            "confidence": r.get("confidence"),
        }
        for r in fresh
    ]
    if preview:
        return out
    cur = await conn.execute(
        "SELECT version_id, valid_from FROM memory_versions WHERE version_id = ANY(%s)",
        (sorted({int(v) for r in fresh for v in (r["src_vid"], r["dst_vid"])}),),
    )
    vf = {int(v): t for v, t in await cur.fetchall()}
    actions = [
        {
            "op": "link_insert",
            "rel": "supersedes",
            "src_logical_id": int(r["src_logical_id"]),
            "dst_logical_id": int(r["dst_logical_id"]),
            "dst_version_id": None,
            "valid_from": fmt_ts(max(vf[int(r["src_vid"])], vf[int(r["dst_vid"])])),
            "props": link_props(r),
            "assessed": {
                str(r["src_logical_id"]): int(r["src_vid"]),
                str(r["dst_logical_id"]): int(r["dst_vid"]),
            },
        }
        for r in fresh
    ]
    records_, _ = await materialize(conn, None, None, actions)
    if not records_:
        return out
    request = {
        "actor": CLIENT,
        "op": OP,
        "project": slug,
        "min_confidence": min_confidence,
        "pairs": [
            {
                k: r.get(k)
                for k in ("src_vid", "dst_vid", "scope", "relation", "confidence", "prompt_version", "model")
            }
            for r in fresh
        ],
    }
    event_id, _at = await xl._record(conn, pid, request, records_, client=CLIENT)
    out.update(applied=len(records_), event_id=event_id)
    return out


async def revert(conn: AsyncConnection, slug: str, *, preview: bool = False) -> dict[str, Any]:
    """``revert`` (module doc): supersede EVERY live backfill link of the project in one event."""
    pid = await xl.project_id(conn, slug)
    await _lock(conn, pid)
    links = await xl.explicit_links(conn, pid, by=BY)
    out: dict[str, Any] = {
        "project": slug,
        "preview": preview,
        "links": links,
        "reverted": 0,
        "event_id": None,
    }
    if preview or not links:
        return out
    await q.lock_logical_ids(
        conn, [lid for ln in links for lid in (ln["src_logical_id"], ln["dst_logical_id"])]
    )
    links = await xl.explicit_links(conn, pid, by=BY)  # re-read under the locks
    records = [
        {
            "op": "link_supersede",
            "link_id": ln["link_id"],
            "src_logical_id": ln["src_logical_id"],
            "dst_logical_id": ln["dst_logical_id"],
        }
        for ln in links
    ]
    if not records:
        return out
    request = {"actor": CLIENT, "op": OP_REVERT, "project": slug, "link_ids": [r["link_id"] for r in records]}
    event_id, _at = await xl._record(conn, pid, request, records, client=CLIENT)
    out.update(links=links, reverted=len(records), event_id=event_id)
    return out


__all__ = [
    "BY",
    "CLIENT",
    "OP",
    "OP_REVERT",
    "BackfillRejected",
    "apply",
    "candidates",
    "link_props",
    "read_proposals",
    "revert",
    "select",
]
