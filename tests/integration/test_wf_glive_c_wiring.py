"""G-LIVE-C through the PRODUCTION WIRING (D-094: "re-shown through the production wiring before
activation"): ``memory.risk_check`` with the primary forced unavailable, so every judged verdict
comes from ``HLM_FALLBACK_PROFILE__RISK_JUDGE``.

Opt-in: ``HLM_GLIVE_C_WIRING=1`` and ``OPENROUTER_API_KEY`` (or ``HLM_GLIVE_C_KEY_FILE=<.env>``, from
which ONLY that variable is read; never printed). Unlike ``test_w2d_glive_c`` (each profile alone,
injected provider), nothing is injected here:

* the configuration is the ``HLM_PROFILE`` / ``HLM_FALLBACK_PROFILE*`` lines of
  ``deploy/llm.env.example`` (the D-094 production mapping), set as environment variables;
* the primary is forced unavailable by pointing ITS base URL (``HLM_LLM_BASE_URL``, which only the
  primary reads) at a closed local port: every primary attempt fails at connect time;
* ``RiskJudge(settings)`` builds everything itself: ``judge_chain`` (per-task fallback +
  qualification), the provider with the latency policy and per-profile breakers, the Postgres spend
  guard (``llm_budget``) and ledger (``llm_calls``) of the test database.

Every case resets the breakers (as the G-LIVE-C runner does), so each one takes the full path: a
refused primary attempt, then the fallback inside the 4 s cap. Pass rule (G-LIVE-C): catch >= 0.85
and false-warn <= 0.10 on every rep; every judged verdict must come from the task fallback.
Spend guard: ``HLM_GLIVE_C_MAX_USD`` (default 2) as the hour/day/month caps of the DB guard.

Outputs (aggregates, no provider response): ``eval/live/<date>-risk-fallback-wiring/`` (or
``HLM_GLIVE_C_OUT``) ``results.json`` and ``SUMMARY.md``.
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
from tests.integration._risk_fixtures import MAIN, load_cases, rates, score_case, seed_world

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("HLM_GLIVE_C_WIRING") != "1", reason="live gate: HLM_GLIVE_C_WIRING=1"),
]

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy" / "llm.env.example"
REPS = int(os.environ.get("HLM_GLIVE_C_REPS", "4"))
MAX_USD = float(os.environ.get("HLM_GLIVE_C_MAX_USD", "2"))
DEAD_PRIMARY_URL = "http://127.0.0.1:9/v1"  # discard port, nothing listens: connection refused
CATCH_MIN, FALSE_WARN_MAX = 0.85, 0.10


@pytest.fixture(autouse=True)
async def _clean_tables():
    yield


def _p(xs: list[float], q: int) -> float:
    return statistics.quantiles(xs, n=100, method="inclusive")[q - 1] if len(xs) > 1 else xs[0]


def _production_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key in list(os.environ):
        if key.upper().startswith(("HLM_FALLBACK_PROFILE", "HLM_PROFILE", "HLM_LLM_")):
            monkeypatch.delenv(key)
    monkeypatch.delenv("HLM_PROFILES_DIR", raising=False)
    mapping = {}
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if (
            sep
            and not line.startswith("#")
            and (key == "HLM_PROFILE" or key.startswith("HLM_FALLBACK_PROFILE"))
        ):
            mapping[key] = value
            monkeypatch.setenv(key, value)
    if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("HLM_GLIVE_C_KEY_FILE"):
        for line in Path(os.environ["HLM_GLIVE_C_KEY_FILE"]).read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key.strip() == "OPENROUTER_API_KEY":  # only this variable, never printed
                monkeypatch.setenv("OPENROUTER_API_KEY", value.strip().strip("'\""))
    assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY not set (HLM_GLIVE_C_KEY_FILE=<.env>)"
    return mapping


async def test_glive_c_through_the_production_wiring(connect, db_dsn, monkeypatch) -> None:  # noqa: ANN001
    mapping = _production_env(monkeypatch)
    deps = default_read_deps()
    world = await seed_world(connect, deps.embedder)
    cases = load_cases()
    settings = get_settings(
        db_dsn=db_dsn,
        librarian_enabled=True,
        llm_mode="live",
        llm_budget_disabled=False,
        llm_budget_hour_usd=MAX_USD,
        llm_budget_day_usd=MAX_USD,
        llm_budget_month_usd=MAX_USD,
        llm_base_url=DEAD_PRIMARY_URL,  # the PRIMARY only: fallback profiles load from their files
    )
    judge = rj.RiskJudge(settings)
    chain = [p.name for p in judge.chain]
    fallback = settings.task_fallback_profiles["risk_judge"]
    assert chain == [settings.profile, fallback], chain
    assert judge.chain[0].base_url == DEAD_PRIMARY_URL and judge.chain[1].base_url != DEAD_PRIMARY_URL
    async with await connect() as conn:  # this run's rows only
        cur = await conn.execute("SELECT clock_timestamp()")
        since = (await cur.fetchone())[0]
        await conn.commit()
    reps: list[dict[str, Any]] = []
    try:
        for rep in range(REPS):
            rows = []
            for case in cases:
                judge.breaker.success()  # every case takes the full path (refused primary, fallback)
                async with await connect() as conn:
                    await conn.commit()  # idle: the judge never runs inside a tx (D-062)
                    out = await rs.risk_check(
                        conn,
                        world.ctx_reader,
                        {"project": MAIN, "task": case["task"], "token_budget": 4000},
                        deps=deps,
                        judge=judge,
                    )
                    await conn.commit()
                assert out["judge"] != rj.BUDGET and out.get("reason") != rj.BUDGET, (
                    "spend guard tripped: FAIL"
                )
                lessons = [world.lesson_of_clue(w["clue"]) for w in out["warnings"]]
                s = score_case(case, out["verdict"] == rs.VERDICT_WARN, lessons)
                status = out["judge"] if out["judged"] else f"retrieval_only:{out.get('reason')}"
                rows.append({"id": case["id"], "split": case["split"], "status": status, **s})
            entry = {
                "rep": rep,
                **rates(rows),
                "judged_only": rates([x for x in rows if x["status"] in rj.JUDGED]),
                "statuses": {
                    k: sum(x["status"] == k for x in rows) for k in sorted({x["status"] for x in rows})
                },
                "misses": [x["id"] for x in rows if x["positive"] and not x["caught"]],
                "false_warns": [x["id"] for x in rows if not x["positive"] and x["false_warn"]],
            }
            reps.append(entry)
            print(f"\nG-LIVE-C wiring rep {rep}: {json.dumps(entry)}")
    finally:
        await judge.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT profile, model_id, outcome, cost_usd, latency_ms FROM llm_calls"
            " WHERE task = 'risk_judge' AND created_at > %s",
            (since,),
        )
        ledger = await cur.fetchall()
        await conn.commit()
    by_profile: dict[str, dict[str, int]] = {}
    for profile, _model, outcome, _cost, _lat in ledger:
        by_profile.setdefault(profile, {}).setdefault(outcome, 0)
        by_profile[profile][outcome] += 1
    fb_rows = [r for r in ledger if r[0] == fallback]
    lat = [float(r[4] or 0) for r in fb_rows if r[2] in ("ok", "schema_retry_ok")]
    provider_usd = sum((Decimal(r[3]) for r in fb_rows), Decimal(0))  # real calls (usage.cost)
    guard_usd = sum((Decimal(r[3]) for r in ledger), Decimal(0))  # + the refused primary at worst case
    judged = sum(sum(v for k, v in e["statuses"].items() if k in rj.JUDGED) for e in reps)
    total = sum(sum(e["statuses"].values()) for e in reps)
    passed = (
        min(e["catch"] for e in reps) >= CATCH_MIN and max(e["false_warn"] for e in reps) <= FALSE_WARN_MAX
    )
    report = {
        "fixture": "tests/fixtures/risk",
        "wiring": {
            "mapping": mapping,
            "primary_forced_unavailable": DEAD_PRIMARY_URL,
            "judge_chain": chain,
            "fallback_model": judge.chain[1].model_id,
        },
        "reps": reps,
        "catch_min": min(e["catch"] for e in reps),
        "catch_mean": statistics.mean(e["catch"] for e in reps),
        "false_warn_max": max(e["false_warn"] for e in reps),
        "false_warn_mean": statistics.mean(e["false_warn"] for e in reps),
        "judged": f"{judged}/{total}",
        "ledger_outcomes": by_profile,
        "fallback_latency_ms_p50": _p(lat, 50) if lat else None,
        "fallback_latency_ms_p95": _p(lat, 95) if lat else None,
        "provider_cost_usd": str(provider_usd),
        "guard_accounted_usd": str(guard_usd),
        "max_usd": MAX_USD,
        "pass": passed,
    }
    today = dt.date.today().isoformat()
    out_dir = Path(
        os.environ.get("HLM_GLIVE_C_OUT") or ROOT / "eval" / "live" / f"{today}-risk-fallback-wiring"
    )
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - end of a live run
    (out_dir / "results.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    per_rep = ", ".join(f"{e['false_warn']:.3f}" for e in reps)
    fb_model = judge.chain[1].model_id
    lines = [
        f"# G-LIVE-C through the production wiring (D-094) {today}",
        "",
        "`memory.risk_check` end to end (`RiskJudge(settings)`: judge_chain, latency policy,",
        "per-profile breakers, Postgres spend guard + ledger) with the `deploy/llm.env.example`",
        f"mapping and the primary `{settings.profile}` forced unavailable (base URL",
        f"{DEAD_PRIMARY_URL}, connection refused), so every judged verdict comes from",
        f"`HLM_FALLBACK_PROFILE__RISK_JUDGE={fallback}` (`{fb_model}`). Fixture",
        f"`tests/fixtures/risk` (40 positive / 40 negative), {REPS} reps, 4 s cap; breakers reset per case.",
        "",
        "| judge chain | catch min / mean | false-warn max / mean (per rep) | judged "
        "| fallback p50 / p95 ms | provider $ | pass |",
        "|---|---|---|---|---|---|---|",
        f"| {' → '.join(chain)} | {report['catch_min']:.3f} / {report['catch_mean']:.3f} | "
        f"{report['false_warn_max']:.3f} / {report['false_warn_mean']:.3f} ({per_rep}) | "
        f"{judged}/{total} | {report['fallback_latency_ms_p50']} / {report['fallback_latency_ms_p95']} | "
        f"{float(provider_usd):.4f} | {'PASS' if passed else 'FAIL'} |",
        "",
        "Ledger (risk_judge rows by profile/outcome):",
        f"`{json.dumps(by_profile, sort_keys=True)}`. Spend guard accounted ${float(guard_usd):.4f}",
        f"(the refused primary attempts are settled at their worst case) of the ${MAX_USD:g} caps.",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    judged_statuses = {k for e in reps for k in e["statuses"] if k in rj.JUDGED}
    assert judged_statuses <= {rj.OK_FALLBACK}, judged_statuses  # every verdict from the task fallback
    assert set(by_profile) <= {settings.profile, fallback}, by_profile  # never another profile
    assert passed, report
