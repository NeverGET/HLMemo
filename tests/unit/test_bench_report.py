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
    assert re.fullmatch(r"adj-1\+[0-9a-f]{8}", v2.overlay_version())


def test_adjusted_gold_rescoring_of_public_misses(adjusted) -> None:  # noqa: ANN001
    rows = {(r["case_id"], r["model"]): r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]}
    sol = "openai/gpt-6-sol"
    want = {
        "T9-01": 1.0,
        "T9-03": 1.0,
        "T9-15": 0.65,
        "T9-25": 0.0,
        "T7-16": 1.0,
        "T7-18": 1.0,
        "T12-10": 0.8,
    }
    for cid, expected in want.items():
        r = rows[(cid, sol)]
        fam = r["family"]
        case, pack = adjusted[(fam, cid)]
        assert v2.score(fam, r["parsed"], case, pack, raw=v2.raw_of(r["parsed"]))[0] == expected, cid
    assert ("T10", "T10-07") not in adjusted  # ambiguous -> dropped


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


def test_leaderboard_render_is_stable(tmp_path: Path) -> None:
    doc = leaderboard.load(tmp_path / "missing.json")
    rows = [_row("T5", "c1", 1.0), _row("T9", "c2", 0.65)]
    s = report.summarize(rows, suite="v2")
    e = leaderboard.librarian_entry(
        s, {"model_id": "m", "reps": 1}, gold="raw", run="r", source="f", commit="abc"
    )
    leaderboard.add_librarian(doc, "bench_v2", e)
    leaderboard.add_librarian(doc, "bench_v2", e)  # upsert, not duplicate
    assert len(doc["librarian"]["bench_v2"]["entries"]) == 1
    md = leaderboard.render_md(doc)
    assert md == leaderboard.render_md(json.loads(json.dumps(doc)))
    assert "err T9" in md and "$/correct" in md
