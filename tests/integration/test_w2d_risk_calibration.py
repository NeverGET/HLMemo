"""W2d calibration of the deterministic risk-check constants (opt-in: ``HLM_RISK_CALIBRATE=1``).

Seeds the fixture world, runs the deterministic stage for every case and grid-searches
``VEC_MAX_DIST`` × ``TAU`` on the CAL split: maximize (catch - false_warn) subject to catch ≥ 0.70
(G-R2), ties → the higher TAU (fewer warnings). ``TAU_STRICT`` (candidates the privacy gate never
sends to the judge) = the smallest threshold with at most 1 cal negative above it. The chosen values
are reported for the test split too; they are copied into ``core/risk_service.py`` by hand (a
constant change is a reviewed diff). ``HLM_RISK_CALIBRATE_OUT=<file>`` also dumps per-case signals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hlmemo.core import risk_service as rs
from hlmemo.core.read_service import default_read_deps
from tests.integration._risk_fixtures import load_cases, rates, score_case, seed_world

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_RISK_CALIBRATE") != "1", reason="opt-in: HLM_RISK_CALIBRATE=1"),
]

VEC_GRID = [0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.20, 1.0]


def _lessons(world, cands) -> list[str | None]:  # noqa: ANN001
    return [world.lesson_of_logical.get(c.logical_id) for c in cands]


async def test_calibrate(connect) -> None:  # noqa: ANN001
    deps = default_read_deps()
    world = await seed_world(connect, deps.embedder)
    cases = load_cases()
    per_case = []
    async with await connect() as conn:
        for case in cases:
            cands, _ = await rs.candidates(
                conn, world.ctx_reader, world.projects["rk-main"], case["task"], deps
            )
            await conn.commit()
            per_case.append((case, cands))

    # recall of the judge's candidate set (gold lesson within the top-10 at all)
    pos = [(c, k) for c, k in per_case if c["gold"]]
    recall = sum(bool(set(c["gold"]) & set(_lessons(world, k))) for c, k in pos) / len(pos)
    print(f"\ncandidate recall@{rs.TOP_K} (positives): {recall:.3f}")
    for c, k in pos:
        if not set(c["gold"]) & set(_lessons(world, k)):
            print("  MISS", c["id"], c["gold"], _lessons(world, k)[:5])

    def evaluate(split: str, vec_max: float, tau: float) -> dict[str, float]:
        scored = []
        for case, cands in per_case:
            if split != "all" and case["split"] != split:
                continue
            hits = []
            for c in cands:
                d = rs.det_score(c.ranks, c.vector_dist, vec_max)
                if d >= tau:
                    hits.append((d, world.lesson_of_logical.get(c.logical_id)))
            hits.sort(key=lambda h: -h[0])
            lessons = [h[1] for h in hits[: rs.MAX_WARNINGS]]
            scored.append(score_case(case, bool(hits), lessons))
        return rates(scored)

    taus = sorted({round(x / 10000, 4) for x in range(150, 700, 5)})
    best = None
    for vm in VEC_GRID:
        for tau in taus:
            r = evaluate("cal", vm, tau)
            if r["catch"] < 0.70:
                continue
            key = (r["catch"] - r["false_warn"], tau)
            if best is None or key > best[0]:
                best = (key, vm, tau, r)
    assert best is not None, "no (VEC_MAX_DIST, TAU) reaches catch 0.70 on the cal split"
    _, vm, tau, r_cal = best
    r_test = evaluate("test", vm, tau)
    r_all = evaluate("all", vm, tau)
    print(f"best VEC_MAX_DIST={vm} TAU={tau}: cal={r_cal} test={r_test} all={r_all}")

    # TAU_STRICT: over the candidates the privacy gate withholds from the judge (device-scoped,
    # hlm-global = policy librarian:off), the smallest threshold >= TAU with at most ONE cal
    # negative whose best withheld candidate reaches it
    def withheld(c: rs.RiskCandidate) -> bool:
        return c.device_scope.startswith("device:") or c.project == "hlm-global"

    neg_withheld = sorted(
        max((rs.det_score(c.ranks, c.vector_dist, vm) for c in cands if withheld(c)), default=0.0)
        for case, cands in per_case
        if case["split"] == "cal" and not case["gold"]
    )
    strict = round(neg_withheld[-2] + 0.0005, 4) if len(neg_withheld) >= 2 else tau
    print(f"TAU_STRICT (<=1 cal negative with a withheld candidate above, >= TAU): {max(strict, tau)}")
    print(f"current constants: VEC_MAX_DIST={rs.VEC_MAX_DIST} TAU={rs.TAU} TAU_STRICT={rs.TAU_STRICT}")
    cur_cal = evaluate("cal", rs.VEC_MAX_DIST, rs.TAU)
    cur_test = evaluate("test", rs.VEC_MAX_DIST, rs.TAU)
    print(f"  current cal={cur_cal} test={cur_test}")

    out = os.environ.get("HLM_RISK_CALIBRATE_OUT")
    if out:
        dump = [
            {
                "id": case["id"],
                "split": case["split"],
                "gold": case["gold"],
                "candidates": [
                    {
                        "lesson": world.lesson_of_logical.get(c.logical_id),
                        "rrf": round(c.rrf, 5),
                        "det": round(c.det_score, 5),
                        "dist": None if c.vector_dist is None else round(c.vector_dist, 4),
                        "lists": c.lists,
                        "ranks": list(c.ranks),
                    }
                    for c in cands
                ],
            }
            for case, cands in per_case
        ]
        Path(out).write_text(json.dumps(dump, indent=1), encoding="utf-8")  # noqa: ASYNC240
