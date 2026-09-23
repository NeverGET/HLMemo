"""W2f report pieces: McNemar, percentiles, error rates, the adjudication overlay, determinism."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hlmemo.bench import leaderboard, report, v2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "bench" / "v2_saved_public.json"


def _row(task: str, case: str, score: float | None, status: str = "ok", rep: int = 0, **kw) -> dict:  # noqa: ANN003
    return {
        "task": task,
        "case_id": case,
        "rep": rep,
        "status": status,
        "score": score,
        "tier": "easy",
        "pack": "public",
        "cost_usd": kw.pop("cost", 0.001),
        "latency_ms": kw.pop("lat", 100),
        **kw,
    }


def test_mcnemar_exact() -> None:
    assert report.mcnemar_exact(0, 0) == 1.0
    assert report.mcnemar_exact(5, 0) == pytest.approx(0.0625)  # 2 * 0.5**5
    assert report.mcnemar_exact(10, 10) == 1.0
    assert report.mcnemar_exact(1, 9) == pytest.approx(2 * (1 + 10) / 1024)


def test_group_stats_error_rate_cost_and_percentiles() -> None:
    rows = [_row("T9", f"c{i}", 1.0 if i < 8 else 0.5, lat=100 * (i + 1)) for i in range(10)]
    rows.append(_row("T9", "x", None, status="infra_error"))
    rows.append(_row("T9", "y", 0.0, status="json_fail", first_json_fail=True))
    s = report.group_stats(rows)
    assert s["n"] == 11 and s["correct"] == 8 and s["error_rate"] == round(1 - 8 / 11, 4)
    assert s["infra_error"] == 1 and s["json_fail_final"] == 1 and s["json_fail_first"] == 1
    assert s["latency_p50_ms"] == 500 and s["latency_p95_ms"] == 1000
    assert s["usd_per_correct"] == round(0.012 / 8, 8)


def test_compare_pairs_on_task_case_rep() -> None:
    a = [_row("T5", f"c{i}", 1.0) for i in range(6)]
    b = [_row("T5", f"c{i}", 0.0 if i < 5 else 1.0) for i in range(6)]
    cmp = report.compare(a, b, suite="v2")
    assert cmp["overall"] == {
        "pairs": 6,
        "a_correct": 6,
        "b_correct": 1,
        "a_only": 5,
        "b_only": 0,
        "p_mcnemar": 0.0625,
    }
    md = report.render_compare_md(cmp, "A", "B")
    assert "| overall | 6 | 6 | 1 | 5 | 0 | 0.0625 |" in md


def test_render_is_deterministic_and_clock_free() -> None:
    rows = [_row("T6", "c1", 1.0), _row("T7", "c2", 0.5)]
    s = report.summarize(rows, suite="v2")
    meta = {"model_id": "m", "profile": "p", "mode": "replay", "reps": 1, "gold": "raw", "packs": "public"}
    a, b = report.render_md(s, meta), report.render_md(report.summarize(list(rows), suite="v2"), dict(meta))
    assert a == b
    assert not re.search(r"20\d\d-\d\d-\d\d", a)


# --------------------------------------------------------------------------- adjudication overlay
@pytest.fixture(scope="module")
def adjusted() -> dict[tuple[str, str], tuple[dict, dict]]:
    packs = v2.load_packs(v2.default_packs(), gold="adjusted")
    return {(fam, c["id"]): (c, pack) for fam, pack in packs for c in pack["cases"]}


def test_overlay_is_public_safe_and_versioned() -> None:
    ov = v2.load_overlay()
    allowed = {
        "key_term_add_variants",
        "distractor_remove",
        "identifier_optional",
        "accept_lesson_set",
        "fact_drop_key",
        "fact_add_variants",
    }
    ids = {c["case"] for c in ov["cases"]}
    assert ids == {
        "T10-07",
        "T12-10",
        "T7-16",
        "T7-18",
        "T7P-08",
        "T7P-09",
        "T8P-02",
        "T9-01",
        "T9-03",
        "T9-15",
        "T9-25",
    }
    for c in ov["cases"]:
        assert c["verdict"] in {"gold_correct", "gold_too_narrow", "ambiguous"}
        for op in c.get("ops", []):
            assert op["op"] in allowed
            for v in op.get("variants", []):  # only short generic English words, never private text
                assert re.fullmatch(r"[a-z ]{3,12}", v), v
    assert re.fullmatch(r"adj-2\+[0-9a-f]{8}", v2.overlay_version())


def test_adjusted_gold_rescoring_of_public_misses(adjusted) -> None:  # noqa: ANN001
    """adj-2 (Sol 40, strict): T9-01, T9-03, T9-15, T9-25 and T10-07 are model errors; only the
    T7 keys and the T12 file-list reading were too narrow."""
    rows = {(r["case_id"], r["model"]): r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]}
    sol = "openai/gpt-6-sol"
    want = {
        "T9-01": 0.65,
        "T9-03": 0.65,
        "T9-15": 0.65,
        "T9-25": 0.0,
        "T10-07": 0.0,
        "T7-16": 1.0,
        "T7-18": 1.0,
        "T12-10": 0.8,
    }
    for cid, expected in want.items():
        r = rows[(cid, sol)]
        fam = r["family"]
        case, pack = adjusted[(fam, cid)]
        assert v2.score(fam, r["parsed"], case, pack, raw=v2.raw_of(r["parsed"]))[0] == expected, cid
    ov = {c["case"]: c for c in v2.load_overlay()["cases"]}
    assert {k for k, c in ov.items() if c["verdict"] == "gold_correct"} == {
        "T9-01",
        "T9-03",
        "T9-15",
        "T9-25",
        "T10-07",
        "T12-10",
    }
    assert not any(c.get("action") == "drop" for c in ov.values())


def test_t12_family_rule_accepts_both_file_readings(adjusted) -> None:  # noqa: ANN001
    case, _ = adjusted[("T12", "T12-10")]
    alt = {f["id"]: f["alt_files"] for f in case["gold"]["findings"] if "alt_files" in f}
    assert set(alt) == {"F08", "F15", "F19"}
    assert all(not any(p.startswith("tests/") for p in a[0]) for a in alt.values())
    easy, _ = adjusted[("T12", "T12-01")]  # no fix-touched files in easy documents
    assert not any("alt_files" in f for f in easy["gold"]["findings"])


def test_raw_gold_is_untouched() -> None:
    raw = {c["id"]: c for _, p in v2.load_packs(v2.default_packs(), gold="raw") for c in p["cases"]}
    assert "T10-07" in raw and "alt_lesson_ids" not in raw["T9-01"]["gold"]
    assert "retries" not in raw["T7-16"]["gold"]["key_terms"][0]


# --------------------------------------------------------------------------- leaderboard (Sol 40)
BASE = json.loads((ROOT / "eval" / "baselines" / "phase0.json").read_text(encoding="utf-8"))


def _cand(a: float, b: float | None, **kw) -> dict:  # noqa: ANN003
    scores = {"corpus_a": a} if b is None else {"corpus_a": a, "corpus_b_dev": b}
    return {
        "label": "iter",
        "commit": "abc1234",
        "config": {"lambda": 0.1},
        "usd_per_query": 0.0,
        "fixtures_sha256": {c: BASE["fixtures"][k] for c, k in _FIX.items() if c in scores},
        "scores": scores,
        **kw,
    }


_FIX = {"corpus_a": "corpus_a_questions_sha256", "corpus_b_dev": "corpus_b_dev_sha256"}


@pytest.fixture
def seeded() -> dict:
    doc = leaderboard.load(Path("/nonexistent/leaderboard.json"))
    leaderboard.seed_baseline(doc, BASE)
    return doc


def test_seed_baseline_never_rewrites_history(seeded: dict) -> None:
    before = json.dumps(seeded, sort_keys=True)
    with pytest.raises(leaderboard.LeaderboardError, match="append-only"):
        leaderboard.seed_baseline(seeded, BASE)
    assert json.dumps(seeded, sort_keys=True) == before
    assert leaderboard.best_of(seeded["retrieval"]["corpus_a"])["score"] == 0.793


def test_rule1_new_best_may_not_drop_another_corpus(seeded: dict) -> None:
    before = json.dumps(seeded, sort_keys=True)
    with pytest.raises(leaderboard.LeaderboardError, match="rule 1"):  # +2 on A, -2 on B
        leaderboard.add_retrieval_candidate(seeded, _cand(0.813, 0.431))
    with pytest.raises(leaderboard.LeaderboardError, match="rule 1: new best .* no score reported"):
        leaderboard.add_retrieval_candidate(seeded, _cand(0.813, None))
    assert json.dumps(seeded, sort_keys=True) == before  # a refusal writes nothing
    moved = leaderboard.add_retrieval_candidate(seeded, _cand(0.813, 0.431), decision="D-070")
    a = seeded["retrieval"]["corpus_a"]
    assert moved == ["corpus_a"] and leaderboard.best_of(a)["score"] == 0.813
    assert a["history"][-1]["decision"] == "D-070" and a["history"][-1]["rule_exceptions"]
    assert len(a["history"]) == 2 and a["history"][0]["score"] == 0.793  # history kept
    assert a["history"][-1]["config_hash"] and a["history"][-1]["commit"] == "abc1234"


def test_rule1_within_one_point_and_rule2_on_merge(seeded: dict) -> None:
    assert leaderboard.add_retrieval_candidate(seeded, _cand(0.80, 0.445)) == ["corpus_a"]  # -0.6 pt ok
    with pytest.raises(leaderboard.LeaderboardError, match="rule 2"):  # merging a -2 pt config
        leaderboard.add_retrieval_candidate(seeded, _cand(0.78, 0.445), merge=True)
    assert leaderboard.add_retrieval_candidate(seeded, _cand(0.78, 0.445)) == []  # a plain iteration


def test_data_errors_are_never_overridable(seeded: dict) -> None:
    bad_fixture = _cand(0.9, 0.5)
    bad_fixture["fixtures_sha256"]["corpus_a"] = "0" * 64
    for cand, msg in (
        (bad_fixture, "fixture sha256"),
        ({**_cand(0.9, 0.5), "usd_per_query": None}, "rule 3"),
        (_cand(0.9, 0.5, scores={"corpus_b_sealed": 0.5}), "unknown corpus|fixture|rule 4"),
    ):
        with pytest.raises(leaderboard.LeaderboardError, match=msg):
            leaderboard.add_retrieval_candidate(seeded, cand, decision="D-070")
    sealed = _cand(0.5, None)
    sealed["scores"] = {"corpus_b_sealed": 0.5}
    sealed["fixtures_sha256"] = {"corpus_b_sealed": BASE["fixtures"]["corpus_b_sealed_sha256"]}
    with pytest.raises(leaderboard.LeaderboardError, match="rule 4"):
        leaderboard.add_retrieval_candidate(seeded, sealed)
    leaderboard.add_retrieval_candidate(seeded, sealed, confirmation=True)
    with pytest.raises(leaderboard.LeaderboardError, match="D-xxx"):
        leaderboard.add_retrieval_candidate(seeded, _cand(0.9, 0.5), decision="later")


def _entry(score_rows: list[dict], meta: dict, packs: dict, incomplete: list[str], run: str = "r") -> dict:
    return leaderboard.librarian_entry(
        report.summarize(score_rows, suite="v2"),
        {"model_id": "m", "reps": 1, "config_hash": "cfg", **meta},
        gold="raw",
        run=run,
        source="f.json",
        commit="abc",
        packs=packs,
        incomplete=incomplete,
    )


def test_incomplete_runs_are_recorded_but_never_ranked() -> None:
    doc = leaderboard.load(Path("/nonexistent/leaderboard.json"))
    packs = [("T5", {"cases": [{"id": "c1"}, {"id": "c2"}]})]
    full = [_row("T5", "c1", 1.0), _row("T5", "c2", 0.5)]
    part = [_row("T5", "c1", 1.0)]
    assert leaderboard.coverage(full, packs, 1, {}) == []
    assert leaderboard.coverage(part, packs, 1, {}) == ["1 of 2 case x rep results missing"]
    assert "interrupted" in leaderboard.coverage(full, packs, 1, {"aborted": True})[0]
    ref = {"t5.json": "a" * 64}
    good = _entry(full, {}, ref, [], run="complete")
    partial = _entry(part + [_row("T5", "c2", 1.0)], {}, ref, ["interrupted"], run="partial")
    other_packs = _entry(full, {}, {"t5.json": "b" * 64}, [], run="other")
    for e in (good, partial, other_packs):
        leaderboard.add_librarian(doc, "bench_v2", e)
    b = doc["librarian"]["bench_v2"]
    assert b["best"]["raw"]["run"] == "complete"
    assert {e["run"]: e["complete"] for e in b["entries"]} == {
        "complete": True,
        "partial": False,
        "other": False,
    }
    assert "pack set differs" in b["entries"][-1]["incomplete"][-1]
    md = leaderboard.render_md(doc)
    assert "Not ranked (incomplete)" in md and md == leaderboard.render_md(json.loads(json.dumps(doc)))
    assert not leaderboard.add_librarian(doc, "bench_v2", good)  # identical: idempotent
    with pytest.raises(leaderboard.LeaderboardError, match="append-only"):
        leaderboard.add_librarian(doc, "bench_v2", {**good, "score": 0.1})
