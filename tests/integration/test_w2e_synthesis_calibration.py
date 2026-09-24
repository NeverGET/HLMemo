"""W2e calibration of τ_s, the weak-evidence threshold of ``memory.query`` synthesis (opt-in:
``HLM_W2E_CALIBRATE=1``; about three minutes: the docs corpus is seeded first).

For every positive question of ``tests/fixtures/synthesis`` (budget 3000, the preflight default):
the fast path's top-hit RRF score, whether the fast path answers it (an answer key in the text of
one ``memory.drilldown`` of the top-3 clues: ``fast3``) and whether the synthesis input could
(a key in the top-10 hit chunks: ``oracle10``, an upper bound for synthesis). τ_s is chosen on the
``cal`` split only: the threshold that best separates the questions the fast path misses from those
it answers (maximum Youden J = P(weak | fast miss) − P(weak | fast answer), candidates = midpoints
between observed scores, rounded to 4 decimals; ties → the lower τ, i.e. fewer LLM calls). It is
reported on the held-out ``test`` split and must equal ``core.synthesis_service.TAU_S`` (a constant
change is a reviewed diff). ``HLM_W2E_CALIBRATE_OUT=<file>`` dumps the per-question signals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hlmemo.core import synthesis_service as ss
from hlmemo.core.read_service import default_read_deps, drilldown, query
from tests.integration._synthesis_fixtures import PROJECT, keys_in, load_questions, seed_corpus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_W2E_CALIBRATE") != "1", reason="opt-in: HLM_W2E_CALIBRATE=1"),
]

BUDGET = 3000


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


def youden(rows: list[dict[str, Any]], tau: float) -> float:
    miss = [r for r in rows if not r["fast3"]]
    hit = [r for r in rows if r["fast3"]]
    if not miss or not hit:
        return 0.0
    return sum(r["top"] < tau for r in miss) / len(miss) - sum(r["top"] < tau for r in hit) / len(hit)


def report(rows: list[dict[str, Any]], tau: float) -> dict[str, Any]:
    weak = [r for r in rows if r["top"] < tau]
    strong = [r for r in rows if r["top"] >= tau]

    def acc(xs: list[dict[str, Any]], key: str) -> float | None:
        return round(sum(r[key] for r in xs) / len(xs), 3) if xs else None

    return {
        "n": len(rows),
        "weak": len(weak),
        "weak_share": round(len(weak) / len(rows), 3),
        "youden_j": round(youden(rows, tau), 3),
        "fast3_weak": acc(weak, "fast3"),
        "fast3_strong": acc(strong, "fast3"),
        "oracle10_weak": acc(weak, "oracle10"),
        "fast_misses_flagged": f"{sum(not r['fast3'] for r in weak)}/{sum(not r['fast3'] for r in rows)}",
    }


def calibrate(rows: list[dict[str, Any]]) -> float:
    scores = sorted({r["top"] for r in rows})
    cands = sorted({round((a + b) / 2, 4) for a, b in zip(scores, scores[1:], strict=False)})
    best = max(cands, key=lambda t: (round(youden(rows, t), 6), -t))
    return best


async def test_calibrate_tau_s(connect) -> None:  # noqa: ANN001
    deps = default_read_deps()
    world = await seed_corpus(connect, deps, deps.embedder)
    rows: list[dict[str, Any]] = []
    async with await connect() as conn:
        for q in load_questions():
            if q.get("negative"):
                continue
            res = await query(
                conn,
                world.ctx,
                {"project": PROJECT, "query": q["question"], "token_budget": BUDGET},
                deps=deps,
            )
            await conn.commit()
            hits = res["hits"]
            d = await drilldown(
                conn,
                world.ctx,
                {"project": PROJECT, "clue_ids": [h["clue"] for h in hits[:3]], "token_budget": 32000},
                deps=deps,
            )
            await conn.commit()
            async with conn.transaction():
                excerpts, _ = await ss._excerpts(conn, hits)
            rows.append(
                {
                    "id": q["id"],
                    "split": q["split"],
                    "lang": q["lang"],
                    "top": float(hits[0]["score"]) if hits else 0.0,
                    "evidence": res["evidence"],
                    "fast3": bool(keys_in("\n".join(it["text"] for it in d["items"]), q["keys"])),
                    "oracle10": bool(keys_in("\n".join(e.text for e in excerpts), q["keys"])),
                }
            )
    cal = [r for r in rows if r["split"] == "cal"]
    test = [r for r in rows if r["split"] == "test"]
    tau = calibrate(cal)
    out = {
        "tau_s": tau,
        "pinned": ss.TAU_S,
        "corpus": {"items": world.items, "chunks": world.chunks},
        "cal": report(cal, tau),
        "test": report(test, tau),
        "all": report(rows, tau),
    }
    print("\nW2e τ_s calibration: " + json.dumps(out, indent=1))
    dump = os.environ.get("HLM_W2E_CALIBRATE_OUT")
    if dump:
        Path(dump).write_text(json.dumps({**out, "rows": rows}, indent=1) + "\n")  # noqa: ASYNC240
    assert tau == ss.TAU_S, f"calibrated τ_s {tau} != pinned TAU_S {ss.TAU_S}"
