"""R4 (R-12): the ported backfill's proposal selection and link props, without a database (the
automatic proposer of wf-supersede-backfill is not ported)."""

from __future__ import annotations

from typing import Any

from hlmemo.ops import backfill_links as bf


def _prop(src: int, dst: int, conf: float, scope: str = "part", **kw: Any) -> dict[str, Any]:
    return {
        "project": "p",
        "status": "proposed",
        "src_vid": src,
        "dst_vid": dst,
        "src_logical_id": src + 100,
        "dst_logical_id": dst + 100,
        "scope": scope,
        "relation": f"supersedes_{scope}",
        "older_span": f"old {dst} span",
        "newer_quote": f"new {src} quote",
        "confidence": conf,
        **kw,
    }


def test_select_filters_dedupes_and_drops_conflicts() -> None:
    records = [
        _prop(2, 1, 0.9),
        _prop(2, 1, 0.95),  # the same pair twice: the more confident one
        _prop(3, 1, 0.4),  # below the floor
        _prop(5, 4, 0.9),
        _prop(4, 5, 0.9),  # both directions: both dropped
        _prop(7, 6, 0.9),
        _prop(8, 7, 0.9),
        _prop(6, 8, 0.9),  # a cycle 6 -> 8 -> 7 -> 6: all dropped
        {**_prop(9, 1, 0.9), "status": "rejected"},
        _prop(9, 1, 0.9, project="other"),  # another project's record: counted, never applied here
    ]
    kept, dropped = bf.select(records, "p", min_confidence=0.5)
    assert [(r["src_vid"], r["dst_vid"], r["confidence"]) for r in kept] == [(2, 1, 0.95)]
    assert dropped == {
        "other_project": 1,
        "below_min_confidence": 1,
        "duplicate": 1,
        "both_directions": 2,
        "cycle": 3,
    }


def test_link_props_follow_the_read_contract_of_their_scope() -> None:
    part = bf.link_props(_prop(2, 1, 0.9, model="m", profile="pf", prompt_version="v1", generator=1))
    assert part["by"] == "backfill" and part["quote"] == "old 1 span" == part["span"]
    assert part["declaration"] == "new 2 quote" and part["model"] == "m" and part["confidence"] == 0.9
    whole = bf.link_props(_prop(2, 1, 0.9, scope="whole"))
    assert whole["scope"] == "whole" and whole["quote"] == "new 2 quote" and whole["span"] == "old 1 span"


def test_the_proposer_is_not_ported() -> None:
    import importlib.util

    assert importlib.util.find_spec("hlmemo.core.supersede_candidates") is None
    assert importlib.util.find_spec("hlmemo.librarian.tasks.supersede_check") is None
    assert not hasattr(bf, "dry_run")  # --dry-run here only previews an apply/revert
