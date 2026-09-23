"""G-LIVE-C (release-blocking, CC-5): ``memory.risk_check`` end to end against the REAL provider.

Opt-in: ``HLM_GLIVE_C=1`` and ``OPENROUTER_API_KEY`` (never printed). Every case of the W2d fixture
runs through the production path — deterministic stage, privacy gate, redaction, the provider with
its retries, the 4 s cap — once per profile (each profile ALONE, like ``eval/live/run.py``: the gate
measures each model, not the fallback chain), ``HLM_GLIVE_C_REPS`` (3) times.

Pass rule per profile: catch ≥ 0.85 and false-warn ≤ 0.10 on EVERY rep (the minimum catch and the
maximum false-warn over the reps). Spend is guarded by one in-process reservation shared by the
whole run (``HLM_GLIVE_C_MAX_USD``, default 3): a refused reservation aborts as a FAIL.

Outputs (task texts are the public fixture; no raw provider response): ``eval/live/<date>-risk/
results.json`` and ``SUMMARY.md``: per profile and rep catch / false-warn (split cal/test, judged
only and end to end), judge statuses, latency p50/p95 and cost.
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
from hlmemo.core import risk_service as rs
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian import risk_judge as rj
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from tests.integration._risk_fixtures import (
    MAIN,
    load_cases,
    load_env_file,
    rates,
    score_case,
    seed_world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_GLIVE_C") != "1", reason="live gate: HLM_GLIVE_C=1"),
]

ROOT = Path(__file__).resolve().parents[2]
PROFILES = os.environ.get("HLM_GLIVE_C_PROFILES", "openrouter-gpt6-luna,openrouter").split(",")
REPS = int(os.environ.get("HLM_GLIVE_C_REPS", "3"))
MAX_USD = Decimal(os.environ.get("HLM_GLIVE_C_MAX_USD", "3"))
CATCH_MIN, FALSE_WARN_MAX = 0.85, 0.10


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


def _p(xs: list[float], q: int) -> float:
    return statistics.quantiles(xs, n=100, method="inclusive")[q - 1] if len(xs) > 1 else xs[0]


async def test_glive_c(connect, db_dsn) -> None:  # noqa: ANN001
    load_env_file()
    assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY not set (HLM_W2D_ENV_FILE=<.env>)"
    deps = default_read_deps()
    world = await seed_world(connect, deps.embedder)
    cases = load_cases()
    budget = MemoryBudget(MAX_USD)
    settings = get_settings(db_dsn=db_dsn, librarian_enabled=True, llm_mode="live", llm_budget_disabled=False)
    report: dict[str, Any] = {"fixture": "tests/fixtures/risk", "reps": REPS, "profiles": {}}
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
            timeout_s=rj.HTTP_TIMEOUT_S,
        )
        judge = rj.RiskJudge(settings, provider=provider)
        reps: list[dict[str, Any]] = []
        try:
            for rep in range(REPS):
                rows = []
                for case in cases:
                    judge.breaker.success()  # the gate measures the model, not a tripped breaker
                    async with await connect() as conn:
                        out = await rs.risk_check(
                            conn,
                            world.ctx_reader,
                            {"project": MAIN, "task": case["task"], "token_budget": 4000},
                            deps=deps,
                            judge=judge,
                        )
                        await conn.commit()
                    assert out["judge"] != rj.BUDGET, "MAX_USD runaway guard tripped: FAIL"
                    lessons = [world.lesson_of_clue(w["clue"]) for w in out["warnings"]]
                    s = score_case(case, out["verdict"] == rs.VERDICT_WARN, lessons)
                    rows.append(
                        {
                            "id": case["id"],
                            "split": case["split"],
                            "judge": out["judge"],
                            **s,
                            "lessons": lessons,
                        }
                    )
                r = rates(rows)
                judged = [x for x in rows if x["judge"] in rj.JUDGED]
                entry = {
                    "rep": rep,
                    **r,
                    "cal": rates([x for x in rows if x["split"] == "cal"]),
                    "test": rates([x for x in rows if x["split"] == "test"]),
                    "judged_only": rates(judged) if judged else None,
                    "statuses": {
                        k: sum(x["judge"] == k for x in rows) for k in sorted({x["judge"] for x in rows})
                    },
                    "misses": [x["id"] for x in rows if x["positive"] and not x["caught"]],
                    "false_warns": [x["id"] for x in rows if not x["positive"] and x["false_warn"]],
                }
                reps.append(entry)
                print(f"\nG-LIVE-C {name} rep {rep}: {json.dumps(entry)}")
        finally:
            await judge.aclose()
        ok_rows = [row for row in ledger.rows if row.outcome in ("ok", "schema_retry_ok")]
        lat = [float(row.latency_ms or 0) for row in ok_rows]
        cost = sum((row.cost_usd for row in ledger.rows), Decimal(0))
        passed = (
            min(e["catch"] for e in reps) >= CATCH_MIN
            and max(e["false_warn"] for e in reps) <= FALSE_WARN_MAX
        )
        verdicts[name] = passed
        report["profiles"][name] = {
            "model_id": profile.model_id,
            "reps": reps,
            "catch_min": min(e["catch"] for e in reps),
            "catch_mean": statistics.mean(e["catch"] for e in reps),
            "false_warn_max": max(e["false_warn"] for e in reps),
            "false_warn_mean": statistics.mean(e["false_warn"] for e in reps),
            "calls": len(ledger.rows),
            "outcomes": {
                o: sum(r.outcome == o for r in ledger.rows) for o in sorted({r.outcome for r in ledger.rows})
            },
            "latency_ms_p50": _p(lat, 50) if lat else None,
            "latency_ms_p95": _p(lat, 95) if lat else None,
            "cost_usd": str(cost),
            "pass": passed,
        }
    report["spent_usd"] = str(budget.spent)
    report["max_usd"] = str(MAX_USD)
    out_dir = ROOT / "eval" / "live" / f"{dt.date.today().isoformat()}-risk"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    lines = [
        f"# G-LIVE-C (memory.risk_check) {dt.date.today().isoformat()}",
        "",
        f"Fixture `tests/fixtures/risk` (40 positive / 40 negative), {REPS} reps, production path "
        f"with the {rj.JUDGE_TIMEOUT_S:g} s cap. Pass: catch >= {CATCH_MIN} and false-warn <= "
        f"{FALSE_WARN_MAX} on every rep. Spent ${budget.spent} of the ${MAX_USD} guard.",
        "",
        "| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ "
        "| pass |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, p in report["profiles"].items():
        judged = sum(sum(v for k, v in e["statuses"].items() if k in rj.JUDGED) for e in p["reps"])
        total = sum(sum(e["statuses"].values()) for e in p["reps"])
        lines.append(
            f"| {name} | {p['model_id']} | {p['catch_min']:.3f} / {p['catch_mean']:.3f} | "
            f"{p['false_warn_max']:.3f} / {p['false_warn_mean']:.3f} | {judged}/{total} | "
            f"{p['latency_ms_p50']} / {p['latency_ms_p95']} | {float(p['cost_usd']):.4f} | "
            f"{'PASS' if p['pass'] else 'FAIL'} |"
        )
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    assert all(verdicts.values()), verdicts
