"""``hlm curate``: the deterministic parts (candidates, builder, gate, FIXes, pass-2 combination,
authority filter) over a small SYNTHETIC export. No agent, no database work."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.curate import synth

from hlmemo.curate import exportdir
from hlmemo.curate import gate as g

LABELS = g.Labels(model="m", profile="p", prompt_version="v", generator="t", confidence=0.8)


@pytest.fixture
def ex(tmp_path: Path) -> exportdir.Export:
    return exportdir.load(synth.make_export(tmp_path / "export"))


def rec(src: int, dst: int, older: str, newer: str, ex: exportdir.Export, **kw: Any) -> dict[str, Any]:
    s, d = ex.head(src), ex.head(dst)
    return {
        "status": "proposed",
        "project": synth.PROJECT,
        "src_logical_id": src,
        "src_vid": s.version_id if s else 0,
        "dst_logical_id": dst,
        "dst_vid": d.version_id if d else 0,
        "scope": "part",
        "older_span": older,
        "newer_quote": newer,
        "relation": "updates",
        "confidence": 0.8,
        "cid": kw.pop("cid", f"c{src}-{dst}"),
        **kw,
    }


CAPS_OLD = "Prod caps are HOUR 1 / DAY 2 / MONTH 10 USD for the spend guard."
CAPS_NEW = "prod caps are now HOUR 3 / DAY 8 / MONTH 60 USD"
PAUSED = "the project is PAUSED by the owner"
ACTIVE = "The project is ACTIVE again since 2026-09-25"


def errors(res: g.GateResult, k: int = 0) -> list[str]:
    return res.rows[k]["errors"]


# ------------------------------------------------------------------ export reader
def test_export_reader_heads_card_and_live_links(ex: exportdir.Export) -> None:
    assert len(ex.items) == 10 and ex.card_ids == {1}
    assert ex.head(1) is None  # the project card is never a link endpoint
    assert ex.live_supersedes == {(90, 100)}
    it = ex.head(40)
    assert it is not None and it.clue == "v440" and it.source_path == "deploy/RUNBOOK.md#status"
    assert it.body.startswith("The project is ACTIVE")
    assert ex.head(80).source_path is None  # origin only


def test_fingerprint_detects_a_changed_file(tmp_path: Path) -> None:
    root = synth.make_export(tmp_path / "e")
    before = exportdir.fingerprint(root)
    f = next(root.rglob("d-010-*.md"))
    f.write_text(f.read_text() + "x", encoding="utf-8")
    assert exportdir.fingerprint(root) != before


# ------------------------------------------------------------------ gate
def test_gate_passes_a_clean_record(ex: exportdir.Export) -> None:
    res = g.gate([rec(20, 10, CAPS_OLD, CAPS_NEW, ex)], ex, synth.PROJECT)
    assert len(res.passed) == 1 and res.rows[0]["errors"] == [] and res.rows[0]["warnings"] == []


def test_gate_verbatim_and_length(ex: exportdir.Export) -> None:
    res = g.gate(
        [
            rec(20, 10, CAPS_OLD.replace("HOUR", "Hour"), CAPS_NEW, ex),
            rec(20, 10, "HOUR 1", CAPS_NEW, ex, cid="short"),
            rec(20, 10, CAPS_OLD, "x" * 301, ex, cid="long"),
            rec(20, 10, "**" + CAPS_OLD, CAPS_NEW, ex, cid="md"),
        ],
        ex,
        synth.PROJECT,
    )
    assert "older_span_not_verbatim" in errors(res, 0)
    assert "older_span_len(6)" in errors(res, 1)
    assert "newer_quote_len(301)" in errors(res, 2) and "newer_quote_not_verbatim" in errors(res, 2)
    assert "older_span_not_verbatim" in errors(res, 3)
    assert res.passed == []


def test_gate_uniqueness(tmp_path: Path) -> None:
    root = synth.make_export(tmp_path / "e")
    f = next(root.rglob("d-020-*.md"))
    f.write_text(
        f.read_text() + "Again: prod caps are now HOUR 3 / DAY 8 / MONTH 60 USD.\n", encoding="utf-8"
    )
    ex = exportdir.load(root)
    res = g.gate([rec(20, 10, CAPS_OLD, CAPS_NEW, ex)], ex, synth.PROJECT)
    assert errors(res) == ["newer_quote_ambiguous(2)"]


def test_gate_heads(ex: exportdir.Export) -> None:
    stale = rec(20, 10, CAPS_OLD, CAPS_NEW, ex)
    stale["dst_vid"] = 109
    res = g.gate(
        [
            stale,
            rec(20, 999, CAPS_OLD, CAPS_NEW, ex, cid="gone"),
            rec(20, 1, CAPS_OLD, CAPS_NEW, ex, cid="card"),
            rec(20, 20, CAPS_NEW, CAPS_NEW, ex, cid="self"),
        ],
        ex,
        synth.PROJECT,
    )
    assert errors(res, 0) == ["dst_vid_stale(head v110)"]
    assert "dst_not_current" in errors(res, 1)
    assert "dst_is_project_card" in errors(res, 2)
    assert "self_link" in errors(res, 3)


def test_gate_contract_fields(ex: exportdir.Export) -> None:
    r = rec(20, 10, CAPS_OLD, CAPS_NEW, ex)
    missing = {k: v for k, v in r.items() if k != "relation"}
    res = g.gate(
        [
            missing,
            {**r, "scope": "whole"},
            {**r, "status": "applied"},
            {**r, "project": "other"},
            {**r, "src_vid": "x"},
        ],
        ex,
        synth.PROJECT,
    )
    assert errors(res, 0) == ["missing:relation"]
    assert errors(res, 1) == ["scope_not_part"]
    assert errors(res, 2) == ["status_not_proposed"]
    assert errors(res, 3) == ["project_mismatch"]
    assert errors(res, 4) == ["bad_id"]


def test_gate_duplicates_first_clean_record_wins(ex: exportdir.Export) -> None:
    broken = rec(20, 10, "not in the body at all, really", CAPS_NEW, ex, cid="a")
    first = rec(20, 10, CAPS_OLD, CAPS_NEW, ex, cid="b")
    second = rec(20, 10, "Prod caps are HOUR 1 / DAY 2 / MONTH 10 USD", CAPS_NEW, ex, cid="c")
    res = g.gate([broken, first, second], ex, synth.PROJECT)
    assert [r["cid"] for r in res.passed] == ["b"]
    assert errors(res, 2) == ["duplicate_pair"]


def test_gate_both_directions_and_already_linked(ex: exportdir.Export) -> None:
    there = rec(40, 30, PAUSED, ACTIVE, ex, cid="there")
    back = rec(30, 40, "The project is ACTIVE again since 2026-09-25", PAUSED, ex, cid="back")
    linked = rec(
        90,
        100,
        "The key lives in the shell profile of the operator account.",
        "The key now lives in the prod env file, not in the shell profile.",
        ex,
        cid="linked",
    )
    reverse_linked = rec(
        100,
        90,
        "The key now lives in the prod env file, not in the shell profile.",
        "The key lives in the shell profile of the operator account.",
        ex,
        cid="rev",
    )
    res = g.gate([there, back, linked, reverse_linked], ex, synth.PROJECT)
    assert errors(res, 0) == ["both_directions"] and errors(res, 1) == ["both_directions"]
    assert errors(res, 2) == ["already_linked"]
    assert "already_linked" in errors(res, 3)
    assert res.passed == []


def test_gate_cycle_with_records_and_live_links(ex: exportdir.Export) -> None:
    a = rec(20, 10, CAPS_OLD, CAPS_NEW, ex, cid="a")
    b = rec(10, 90, "The key now lives in the prod env file", CAPS_OLD, ex, cid="b")
    c = rec(100, 20, CAPS_NEW, "The key lives in the shell profile of the operator account.", ex, cid="c")
    res = g.gate([a, b], ex, synth.PROJECT)
    assert len(res.passed) == 2  # 20 -> 10 -> 90 -> 100 (live): a chain
    res = g.gate([a, b, c], ex, synth.PROJECT)  # 100 -> 20 closes the loop through the live link
    assert all("cycle" in errors(res, k) for k in range(3)) and res.passed == []


def test_gate_warns_when_the_src_is_older(ex: exportdir.Export) -> None:
    r = rec(10, 20, "The owner raised the caps: prod caps are now HOUR 3", CAPS_OLD, ex)
    res = g.gate([r], ex, synth.PROJECT)
    assert res.rows[0]["warnings"] == ["WARN_src_older_valid_from"] and len(res.passed) == 1
    assert res.counts()["warnings"] == 1


def test_gate_counts(ex: exportdir.Export) -> None:
    res = g.gate(
        [rec(20, 10, CAPS_OLD, CAPS_NEW, ex), rec(20, 999, CAPS_OLD, CAPS_NEW, ex, cid="x")],
        ex,
        synth.PROJECT,
    )
    c = res.counts()
    assert c["records"] == 2 and c["passed"] == 1 and c["failed"] == 1
    assert c["errors"] == {"dst_not_current": 1}


# ------------------------------------------------------------------ authority
def test_authority_filter(ex: exportdir.Export) -> None:
    recs = [
        rec(20, 10, CAPS_OLD, CAPS_NEW, ex, cid="decision"),
        rec(40, 30, PAUSED, ACTIVE, ex, cid="runbook"),
        rec(
            50,
            60,
            "The writer is model-a for every research answer.",
            "the writer is model-b",
            ex,
            cid="note",
        ),
        rec(80, 10, CAPS_OLD, "always preview before an apply", ex, cid="nosource"),
    ]
    keep, held = g.authority_split(recs, ex, g.DEFAULT_AUTHORITY)
    assert [r["cid"] for r in keep] == ["decision", "runbook"]
    assert [(h["cid"], h["held_reason"], h["src_source"]) for h in held] == [
        ("note", "authority", "notes.md#rule-1"),
        ("nosource", "authority", None),
    ]
    keep, held = g.authority_split(recs, ex, ["deploy/*"])  # configurable
    assert [r["cid"] for r in keep] == ["runbook"]


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("docs/decisions/DECISIONS.md#D-121", True),
        ("docs/decisions/R4-RELEASE-PLAN.md#gates", True),
        ("deploy/RUNBOOK.md", True),
        ("docs/USAGE.md#commands", True),
        ("CLAUDE.md", True),
        ("docs/status/STATUS.md#now", False),
        ("docs/consults/18-x.md#a", False),
        ("other/CLAUDE.md", False),
        ("docs/decisionsX/a.md", False),
    ],
)
def test_default_authority_globs(path: str, ok: bool) -> None:
    it = exportdir.Item(1, 1, "fact", "t", "", "f.md", "b", {"system": "markdown", "path": path})
    assert g.is_authoritative(it, g.DEFAULT_AUTHORITY) is ok


# ------------------------------------------------------------------ pass 2: combine + FIX
def v(verdict: str, fix: dict[str, Any] | None = None, tests: list[int] | None = None) -> dict[str, Any]:
    return {"verdict": verdict, "fix": fix, "failed_tests": tests or [], "reason": "r"}


def test_combine_rules() -> None:
    """Owner rule (cross mode): a record survives only on KEEP+KEEP, KEEP+FIX or identical FIXes."""
    assert g.combine([v("KEEP")], 1) == ("KEEP", None)
    assert g.combine([v("KEEP"), v("KEEP")], 2) == ("KEEP", None)
    assert g.combine([], 1) == ("INCOMPLETE", None)
    assert g.combine([v("KEEP")], 2) == ("INCOMPLETE", None)  # one refuter's slice failed
    assert g.combine([v("KEEP"), v("DROP", tests=[1])], 2) == ("DROP", None)
    assert g.combine([v("FIX", {"older_span": "abc"}), v("DROP", tests=[1])], 2) == ("DROP", None)
    assert g.combine([v("DROP", tests=[1]), v("DROP", tests=[5])], 2) == ("DROP", None)
    keep_fix = g.combine([v("KEEP"), v("FIX", {"older_span": "the span", "newer_quote": None})], 2)
    assert keep_fix == ("FIX", {"older_span": "the span"})
    same = g.combine(
        [v("FIX", {"older_span": "the span"}), v("FIX", {"older_span": "the span", "newer_quote": ""})], 2
    )
    assert same == ("FIX", {"older_span": "the span"})  # empty/null fields mean "no change"


def test_combine_differing_fixes_are_a_conflict() -> None:
    nested = [v("FIX", {"older_span": "a long stale span"}), v("FIX", {"older_span": "stale span"})]
    assert g.combine(nested, 2) == ("FIX_CONFLICT", None)  # identical or held, never merged
    halves = [v("FIX", {"older_span": "x span"}), v("FIX", {"newer_quote": "q"})]
    assert g.combine(halves, 2) == ("FIX_CONFLICT", None)
    src = [v("FIX", {"src_logical_id": 1, "src_vid": 2}), v("FIX", {"src_logical_id": 3, "src_vid": 4})]
    assert g.combine(src, 2) == ("FIX_CONFLICT", None)
    same_src = [v("FIX", {"src_logical_id": 1, "src_vid": 2, "newer_quote": "q"})] * 2
    assert g.combine(same_src, 2) == ("FIX", {"newer_quote": "q", "src_logical_id": 1, "src_vid": 2})
    assert g.HELD_REASONS["FIX_CONFLICT"] == "refuter_fix_conflict"


@pytest.mark.parametrize(
    ("verdicts", "expected", "cls"),
    [
        ([v("KEEP"), v("KEEP")], 2, "keep_keep"),
        ([v("KEEP"), v("FIX", {"older_span": "s"})], 2, "keep_fix"),
        ([v("FIX", {"older_span": "s"}), v("FIX", {"older_span": "s"})], 2, "fix_identical"),
        ([v("FIX", {"older_span": "s"}), v("FIX", {"older_span": "t"})], 2, "fix_conflict"),
        ([v("DROP", tests=[1]), v("DROP", tests=[2])], 2, "drop_unanimous"),
        ([v("KEEP"), v("DROP", tests=[1])], 2, "drop_split"),
        ([v("KEEP")], 2, "incomplete"),
        ([v("KEEP")], 1, "keep_keep"),
    ],
)
def test_agreement_classes(verdicts: list[dict[str, Any]], expected: int, cls: str) -> None:
    assert g.agreement(verdicts, expected) == cls and cls in g.AGREEMENT_CLASSES


def test_apply_fix_then_regate(ex: exportdir.Export) -> None:
    r = rec(40, 30, "the project is PAUSED by the owner until the budget review is done.", ACTIVE, ex)
    fixed = g.apply_fix(r, {"older_span": PAUSED})
    assert fixed["older_span"] == PAUSED and fixed["fixed"] is True and r["older_span"] != PAUSED
    assert len(g.gate([fixed], ex, synth.PROJECT).passed) == 1
    moved = g.apply_fix(r, {"src_logical_id": 20, "src_vid": 220, "newer_quote": CAPS_NEW})
    assert (moved["src_logical_id"], moved["src_vid"], moved["src_clue"]) == (20, 220, "v220")
    bad = g.apply_fix(r, {"older_span": "a span that is not in the body"})
    assert "older_span_not_verbatim" in g.gate([bad], ex, synth.PROJECT).rows[0]["errors"]


# ------------------------------------------------------------------ candidates + builder
def test_normalize_librarian_audit(ex: exportdir.Export) -> None:
    cands, skipped = g.normalize_candidates(synth.audit(), ex)
    assert [c["cid"] for c in cands] == ["c0001", "c0002", "c0003", "c0004", "c0005", "c0006"]
    assert [c["question_id"] for c in cands] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert {s["reason"] for s in skipped} == {"not_in_export", "no_link_action", "duplicate_pair"}
    c = cands[0]
    assert (c["proposed_src"], c["proposed_dst"], c["relation"], c["origin"]) == (
        10,
        20,
        "contradicts",
        "librarian",
    )
    assert [s["file"] for s in c["subjects"]] == ["fact/d-010-prod-caps.md", "fact/d-020-caps-raised.md"]
    only, skipped = g.normalize_candidates(synth.audit(), ex, status="open")
    assert only == [] and {s["reason"] for s in skipped} == {"status_filtered"}


def test_normalize_pairs_and_errors(ex: exportdir.Export) -> None:
    data = {
        "origin": "map",
        "pairs": [
            {"newer_logical_id": 20, "older_logical_id": 10, "why": "w", "area": "caps"},
            {"newer_logical_id": 20, "older_logical_id": 20, "why": "w"},
            {"older_logical_id": 20, "why": "w"},
        ],
    }
    cands, skipped = g.normalize_candidates(data, ex)
    assert len(cands) == 1 and cands[0]["origin"] == "map" and cands[0]["area"] == "caps"
    assert [s["reason"] for s in skipped] == ["self_pair", "bad_pair"]
    with pytest.raises(ValueError):
        g.normalize_candidates({"what": 1}, ex)


def test_build_records(ex: exportdir.Export) -> None:
    cands, _ = g.normalize_candidates(synth.audit(), ex)
    ans = synth.answers()["pass1"]
    verdicts: dict[str, dict[str, Any]] = {}
    for c in cands:
        a, b = (s["logical_id"] for s in c["subjects"])
        verdicts[c["cid"]] = {
            "cid": c["cid"],
            **ans.get(f"{min(a, b)}-{max(a, b)}", {"verdict": "NO_CONFLICT"}),
        }
    verdicts["c0003"] = {**verdicts["c0003"], "older_span": None}
    verdicts["c0005"] = {**verdicts["c0005"], "newer_logical_id": 20}
    del verdicts["c0006"]
    records, skipped = g.build_records(cands, verdicts, ex, project=synth.PROJECT, labels=LABELS)
    assert [(r["src_logical_id"], r["dst_logical_id"]) for r in records] == [(20, 10), (40, 30)]
    r = records[0]
    assert (r["src_vid"], r["dst_vid"], r["scope"], r["status"], r["model"], r["confidence"]) == (
        220,
        110,
        "part",
        "proposed",
        "m",
        0.8,
    )
    assert {s["cid"]: s["reason"] for s in skipped} == {
        "c0003": "no_spans_or_ids",
        "c0004": "verdict_no_conflict",
        "c0005": "ids_not_in_candidate",
        "c0006": "no_verdict",
    }
    # the builder's records pass the gate as they are
    assert len(g.gate(records, ex, synth.PROJECT).passed) == 2


def test_build_dedupes_pairs(ex: exportdir.Export) -> None:
    cands, _ = g.normalize_candidates(
        {"pairs": [{"newer_logical_id": 20, "older_logical_id": 10, "why": "a"}]}, ex
    )
    twin = copy.deepcopy(cands[0]) | {"cid": "c0002"}
    v1 = {"verdict": "SUPERSESSION", "newer_logical_id": 20, "older_logical_id": 10}
    v1 |= {"older_span": CAPS_OLD, "newer_quote": CAPS_NEW, "why": "w"}
    recs, skipped = g.build_records(
        [cands[0], twin], {"c0001": v1, "c0002": v1 | {"cid": "c0002"}}, ex, project="demo", labels=LABELS
    )
    assert len(recs) == 1 and skipped == [{"cid": "c0002", "reason": "duplicate_pair"}]


def test_slices() -> None:
    assert g.slices(list(range(7)), 3) == [[0, 1, 2], [3, 4], [5, 6]]
    assert g.slices([1], 3) == [[1]]
    assert g.slices([], 3) == []
