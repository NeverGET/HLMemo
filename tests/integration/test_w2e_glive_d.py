"""G-LIVE-D (CC-5, W2e): ``memory.query`` synthesis against the REAL provider.

Opt-in: ``HLM_GLIVE_D=1`` and ``OPENROUTER_API_KEY`` (``HLM_W2E_ENV_FILE=<.env>``; never printed).

Subset: the WEAK-evidence questions (top-hit RRF < τ_s at budget 3000) of the held-out ``test``
split of ``tests/fixtures/synthesis`` (τ_s and the prompt were tuned on ``cal`` only). Every
question runs through the production path (fast path, privacy gate, redaction, the provider with its
retries, the 6 s cap, the citation validator, the post-call re-check, packing), once per profile,
each profile ALONE (like G-LIVE-C: the gate measures each model, not the fallback chain),
``HLM_GLIVE_D_REPS`` (3) times.

Answer accuracy (W2e): with synthesis = an answer key in the synthesis text; fast path only = an
answer key in the text of one ``memory.drilldown`` of the top-3 clues (deterministic, the same in
every rep). A synthesis that is unavailable (timeout, failure) or abstains counts as a miss. Pass rule
per profile: on EVERY rep, synthesis accuracy − fast-path accuracy ≥ 0.03 (the W-E first bar X); the
minimum over the reps is reported. Also reported: the "system" accuracy (the synthesis when it
answered, else the fast path), abstentions and false answers on the fixture's negative questions,
statuses, latency and cost. Spend is guarded by one reservation shared by the whole run
(``HLM_GLIVE_D_MAX_USD``, default 1.5): a refused reservation aborts as a FAIL.

Outputs (public fixture; no raw provider response): ``eval/live/<date>-synthesis/results.json`` and
``SUMMARY.md`` (``HLM_GLIVE_D_OUT`` overrides the directory).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hlmemo.config import get_settings
from hlmemo.core import synthesis_service as ss
from hlmemo.core.read_service import default_read_deps, drilldown, query
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.tasks import synthesis as syn
from tests.integration._synthesis_fixtures import (
    PROJECT,
    keys_in,
    load_env_file,
    load_questions,
    seed_corpus,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_GLIVE_D") != "1", reason="live gate: HLM_GLIVE_D=1"),
]

ROOT = Path(__file__).resolve().parents[2]
PROFILES = os.environ.get("HLM_GLIVE_D_PROFILES", "openrouter-gpt6-luna,openrouter").split(",")
REPS = int(os.environ.get("HLM_GLIVE_D_REPS", "3"))
MAX_USD = Decimal(os.environ.get("HLM_GLIVE_D_MAX_USD", "1.5"))
SPLIT = os.environ.get("HLM_GLIVE_D_SPLIT", "test")
BUDGET = 3000
X = 0.03


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


def _p(xs: list[float], q: int) -> float | None:
    if not xs:
        return None
    return statistics.quantiles(xs, n=100, method="inclusive")[q - 1] if len(xs) > 1 else xs[0]


async def test_glive_d(connect, db_dsn) -> None:  # noqa: ANN001
    load_env_file()
    assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY not set (HLM_W2E_ENV_FILE=<.env>)"
    deps = default_read_deps()
    world = await seed_corpus(connect, deps, deps.embedder)
    subset: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    async with await connect() as conn:
        for q in load_questions():
            args = {"project": PROJECT, "query": q["question"], "token_budget": BUDGET}
            res = await query(conn, world.ctx, args, deps=deps)
            await conn.commit()
            if q.get("negative"):
                negatives.append({**q, "weak": ss.weak(res) is None})
                continue
            if q["split"] != SPLIT or ss.weak(res) is not None:
                continue
            clues = [h["clue"] for h in res["hits"][:3]]
            d = await drilldown(
                conn, world.ctx, {"project": PROJECT, "clue_ids": clues, "token_budget": 32000}, deps=deps
            )
            await conn.commit()
            fast = bool(keys_in("\n".join(it["text"] for it in d["items"]), q["keys"]))
            subset.append({**q, "fast3": fast, "top": res["hits"][0]["score"]})
    assert len(subset) >= 30, f"weak-evidence subset has {len(subset)} questions (< 30)"
    fast_acc = sum(r["fast3"] for r in subset) / len(subset)
    budget = MemoryBudget(MAX_USD)
    settings = get_settings(db_dsn=db_dsn, librarian_enabled=True, llm_mode="live", llm_budget_disabled=False)
    report: dict[str, Any] = {
        "fixture": "tests/fixtures/synthesis",
        "split": SPLIT,
        "tau_s": ss.TAU_S,
        "subset": len(subset),
        "negatives": len(negatives),
        "fast_path_accuracy": round(fast_acc, 3),
        "reps": REPS,
        "profiles": {},
    }
    verdicts: dict[str, bool] = {}
    for name in PROFILES:
        profile = named_profile(name)
        ledger = MemoryLedger()
        provider = Provider(
            [profile],
            mode="live",
            budget=budget,
            ledger=ledger,
            redactor=Redactor.from_settings(settings),
            timeout_s=syn.HTTP_TIMEOUT_S,
        )
        synth = syn.Synthesizer(settings, provider=provider)  # production cap: SYNTH_TIMEOUT_S
        reps: list[dict[str, Any]] = []
        try:
            for rep in range(REPS):
                rows = []
                for q in subset + [n for n in negatives if n["weak"]]:
                    synth.breaker.success()  # the gate measures the model, not a tripped breaker
                    async with await connect() as conn:
                        await conn.commit()  # idle: synthesis never runs inside a tx (D-062)
                        out = await ss.query_synthesize(
                            conn,
                            world.ctx,
                            {"project": PROJECT, "query": q["question"], "token_budget": BUDGET},
                            deps=deps,
                            synth=synth,
                        )
                        await conn.commit()
                    assert out.get("synthesis_reason") != syn.BUDGET, "MAX_USD runaway guard tripped: FAIL"
                    s = out.get("synthesis")
                    status = s["status"] if s else f"unavailable:{out['synthesis_reason']}"
                    assert s is None or set(s["clues"]) <= {h["clue"] for h in out["hits"]}
                    hit = bool(s and keys_in(s["text"], q["keys"])) if q["keys"] else False
                    rows.append(
                        {
                            "id": q["id"],
                            "negative": bool(q.get("negative")),
                            "status": status,
                            "tier": s.get("tier") if s else None,
                            "hit": hit,
                            "fast3": q.get("fast3", False),
                            "system": hit if status == "answered" else q.get("fast3", False),
                        }
                    )
                pos = [r for r in rows if not r["negative"]]
                neg = [r for r in rows if r["negative"]]
                acc = sum(r["hit"] for r in pos) / len(pos)
                entry = {
                    "rep": rep,
                    "synthesis_accuracy": round(acc, 3),
                    "fast_path_accuracy": round(fast_acc, 3),
                    "delta": round(acc - fast_acc, 3),
                    "system_accuracy": round(sum(r["system"] for r in pos) / len(pos), 3),
                    "statuses": {
                        k: sum(r["status"] == k for r in pos) for k in sorted({r["status"] for r in pos})
                    },
                    "negatives_weak": len(neg),
                    "negatives_answered": sum(r["status"] == "answered" for r in neg),
                    "negatives_abstained": sum(r["status"] == "insufficient_evidence" for r in neg),
                    "wins": sorted(r["id"] for r in pos if r["hit"] and not r["fast3"]),
                    "losses": sorted(r["id"] for r in pos if r["fast3"] and not r["hit"]),
                }
                reps.append(entry)
                print(f"\nG-LIVE-D {name} rep {rep}: {json.dumps(entry)}")
        finally:
            await synth.aclose()
        ok_rows = [row for row in ledger.rows if row.outcome in ("ok", "schema_retry_ok")]
        lat = [float(row.latency_ms or 0) for row in ok_rows]
        cost = sum((row.cost_usd for row in ledger.rows), Decimal(0))
        delta_min = min(e["delta"] for e in reps)
        passed = delta_min >= X
        verdicts[name] = passed
        report["profiles"][name] = {
            "model_id": profile.model_id,
            "reps": reps,
            "synthesis_accuracy_min": min(e["synthesis_accuracy"] for e in reps),
            "synthesis_accuracy_mean": round(statistics.mean(e["synthesis_accuracy"] for e in reps), 3),
            "delta_min": delta_min,
            "delta_mean": round(statistics.mean(e["delta"] for e in reps), 3),
            "system_accuracy_min": min(e["system_accuracy"] for e in reps),
            "negatives_answered_max": max(e["negatives_answered"] for e in reps),
            "calls": len(ledger.rows),
            "outcomes": {
                o: sum(r.outcome == o for r in ledger.rows) for o in sorted({r.outcome for r in ledger.rows})
            },
            "latency_ms_p50": _p(lat, 50),
            "latency_ms_p95": _p(lat, 95),
            "cost_usd": str(cost),
            "pass": passed,
        }
    report["spent_usd"] = str(budget.spent)
    report["max_usd"] = str(MAX_USD)
    today = dt.date.today().isoformat()
    out_dir = Path(os.environ.get("HLM_GLIVE_D_OUT") or ROOT / "eval" / "live" / f"{today}-synthesis")
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - end of a live run
    (out_dir / "results.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    lines = [
        f"# G-LIVE-D (memory.query synthesis) {today}",
        "",
        f"Fixture `tests/fixtures/synthesis`: the {len(subset)} weak-evidence questions (top RRF < "
        f"τ_s = {ss.TAU_S}) of the held-out `{SPLIT}` split, plus the weak negatives; {REPS} reps per "
        f"profile, each profile alone, production path with the {syn.SYNTH_TIMEOUT_S:g} s cap. Fast path "
        f"only (answer key in one drilldown of the top-3 clues): {fast_acc:.3f}. Pass: synthesis "
        f"accuracy (answer key in the synthesis text) − fast path ≥ {X} on every rep. Spent "
        f"${budget.spent} of the ${MAX_USD} guard.",
        "",
        "| profile | model | synthesis acc min / mean | Δ vs fast path min / mean | system acc min "
        "| negatives answered (max) | p50 / p95 ms | cost $ | pass |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, p in report["profiles"].items():
        lines.append(
            f"| {name} | {p['model_id']} | {p['synthesis_accuracy_min']:.3f} / "
            f"{p['synthesis_accuracy_mean']:.3f} | {p['delta_min']:+.3f} / {p['delta_mean']:+.3f} | "
            f"{p['system_accuracy_min']:.3f} | {p['negatives_answered_max']} | "
            f"{p['latency_ms_p50']} / {p['latency_ms_p95']} | {float(p['cost_usd']):.4f} | "
            f"{'PASS' if p['pass'] else 'FAIL'} |"
        )
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    assert all(verdicts.values()), verdicts
