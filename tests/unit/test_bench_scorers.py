"""W2f: the ported bench scorers reproduce the saved bench-v2 (D-066) scores exactly.

* Always: the committed public fixture (two saved runs, 320 calls incl. JSON fails and injection
  compliance) re-scores to the identical score AND detail; the validators reproduce the saved
  failure reasons; the canonical output text (what the production provider leaves us) gives the same
  T11 verdicts as the raw response did.
* On the owner machine: every saved v2 result file (``bench/results/*-v2.json``, gitignored) and the
  private pack re-score call-for-call (2,796 scored calls) and reproduce the saved macro means.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hlmemo.bench import report, v1, v2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "bench" / "v2_saved_public.json"
SAVED = [
    "20260923-184519-v2.json",
    "20260923-190032-v2.json",
    "20260923-194459-v2.json",
    "20260923-201313-v2.json",
]


def _results_dir() -> Path:
    return Path(os.environ.get("HLM_BENCH_RESULTS_DIR", ROOT / "bench" / "results"))


def _private_dir() -> Path:
    return Path(os.environ.get("HLM_BENCH_PRIVATE_DIR", ROOT / "docs" / "private" / "bench-v2"))


@pytest.fixture(scope="module")
def public_cases() -> dict[tuple[str, str], tuple[dict, dict]]:
    packs = v2.load_packs(v2.default_packs(), gold="raw")
    return {(fam, c["id"]): (c, pack) for fam, pack in packs for c in pack["cases"]}


def test_public_fixture_rescores_identically(public_cases) -> None:  # noqa: ANN001
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 320
    scored = 0
    for r in rows:
        case, pack = public_cases[(r["family"], r["case_id"])]
        if r["json_fail"]:
            obj, perr = v2.parse_json(r.get("content"))
            reason = perr or v2.validate(r["family"], obj, case, pack)
            assert reason == r["fail_reason"], r["case_id"]
            continue
        assert v2.validate(r["family"], r["parsed"], case, pack) is None
        s, d = v2.score(r["family"], r["parsed"], case, pack, raw=r.get("content") or v2.raw_of(r["parsed"]))
        assert (s, d) == (r["score"], r["detail"]), (r["model"], r["case_id"])
        scored += 1
    assert scored >= 300


def test_canonical_output_gives_the_same_injection_verdicts(public_cases) -> None:  # noqa: ANN001
    """The production provider keeps no raw response (CC-5); T11 canary checks run on the canonical
    output text and must agree with the verdicts computed from the raw response."""
    rows = [r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"] if r["family"] == "T11"]
    assert any(r["detail"].get("complied") for r in rows)  # the fixture has real compliance cases
    for r in rows:
        if r["json_fail"]:
            continue
        case, pack = public_cases[("T11", r["case_id"])]
        assert v2.score("T11", r["parsed"], case, pack, raw=v2.raw_of(r["parsed"]))[0] == r["score"]


def test_v1_scorer_matches_the_rubric() -> None:
    cfg = v1.load_fixture("risk")
    case = cfg["cases"][0]
    g = case["gold"]
    s, d = v1.score(
        "risk", {"warn": g["warn"], "matched_lesson_ids": g["matched_lesson_ids"], "message": ""}, case, cfg
    )
    assert s == 1.0 and d["warn_ok"]
    s, _ = v1.score("risk", {"warn": not g["warn"], "matched_lesson_ids": [], "message": ""}, case, cfg)
    assert s == round(0.5 * 0 + 0.5 * v1.jaccard([], g["matched_lesson_ids"]), 4)
    assert (
        v1.fixture_sha256("placement") == "c64f73a603282490223f70e24de175c3db4ec5a72dfe192c0994e9ebe299d390"
    )


@pytest.mark.skipif(
    not all((_results_dir() / f).is_file() for f in SAVED) or not _private_dir().is_dir(),
    reason="saved bench-v2 raw results / private pack are owner-machine only (gitignored)",
)
def test_every_saved_v2_call_rescores_identically() -> None:
    packs = v2.load_packs(v2.default_packs() + v2.expand_pack_paths([_private_dir()]), gold="raw")
    cases = {(fam, c["id"]): (c, pack) for fam, pack in packs for c in pack["cases"]}
    scored = 0
    for fn in SAVED:
        doc = json.loads((_results_dir() / fn).read_text(encoding="utf-8"))
        for c in doc["calls"]:
            if c.get("infra_error") or c.get("json_fail"):
                continue
            case, pack = cases[(c["family"], c["case_id"])]
            assert v2.score(c["family"], c["parsed"], case, pack, raw=c["content"]) == (
                c["score"],
                c["detail"],
            )
            assert v2.score(c["family"], c["parsed"], case, pack, raw=v2.raw_of(c["parsed"]))[0] == c["score"]
            scored += 1
        for old in doc["summary"]:  # the saved headline numbers (D-066) reproduce from the rows
            rows, _ = report.load_rows(f"{_results_dir() / fn}#{old['model']}")
            new = report.summarize(rows, suite="v2")
            assert new["macro_mean"] == old["macro_mean"]
            assert new["overall"]["mean"] == old["overall"]["mean"]
            assert new["overall"]["correct"] == old["overall"]["correct"]
    assert scored == 2796
