#!/usr/bin/env python3
"""Release-blocking live gate runner (CC-5; G-LIVE-A for W2a).

Runs every LLM task fixture (``src/hlmemo/bench/tasks/v1/t1..t4``, pinned by sha256) through the
PRODUCTION path — versioned prompts (``hlmemo.librarian.prompts``), the redactor, the provider with
its retry/schema/backoff logic and the atomic reservation — once per profile (default, then the
fallback profile, each alone: the gate measures each model, not the fallback chain), ``--reps``
times. The fixtures, payloads, semantic checks, scoring and the call loop are ``hlm bench``'s
(``hlmemo.bench.v1`` / ``hlmemo.bench.runner``, W2f); this file keeps only the gate policy.
Scoring follows ``eval/live/RUBRIC.md`` (the D-019 bench scoring). Pass rule per task and
profile: the MINIMUM over reps of the per-rep mean score ≥ the rubric threshold; the mean is
reported. ``--max-usd`` is a runaway guard shared by the whole run: a reservation the guard
refuses aborts the run as a FAIL, never a skip.

Outputs (redacted, no raw provider response): ``eval/live/<date>-<profile>/results.json`` and
``SUMMARY.md``. Exit 0 only when every profile passes.

    uv run --frozen python eval/live/run.py --profile openrouter --fallback openrouter-luna \
        --reps 3 --max-usd 5 --env-file .env
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import statistics
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hlmemo.bench import v1  # noqa: E402
from hlmemo.bench.runner import Item, run_items  # noqa: E402
from hlmemo.librarian.budget import MemoryBudget  # noqa: E402
from hlmemo.librarian.cassette import CassetteStore  # noqa: E402
from hlmemo.librarian.ledger import MemoryLedger  # noqa: E402
from hlmemo.librarian.profiles import named_profile  # noqa: E402
from hlmemo.librarian.prompts import load_task  # noqa: E402
from hlmemo.librarian.provider import Provider  # noqa: E402
from hlmemo.librarian.redact import Redactor  # noqa: E402
from hlmemo.librarian.tasks import JOB_NAMES, user_message  # noqa: E402

#: task -> (fixture path, pinned sha256). A fixture edit is a gate change: update the pin in the
#: same commit and re-run the gate for every profile.
FIXTURES: dict[str, tuple[str, str]] = {
    "placement": (
        "src/hlmemo/bench/tasks/v1/t1_placement.json",
        "c64f73a603282490223f70e24de175c3db4ec5a72dfe192c0994e9ebe299d390",
    ),
    "contradiction": (
        "src/hlmemo/bench/tasks/v1/t2_contradiction.json",
        "d5b710200d02b03e4ba5e4388abeb0715e18ef7f98739a9fd975dd4abef3c8a9",
    ),
    "summary": (
        "src/hlmemo/bench/tasks/v1/t3_summarization.json",
        "10aa5896c62c2f04e6f100eef689f8ae9ed16d1ee40c6453d1a1363f4f9cbbd2",
    ),
    "risk": (
        "src/hlmemo/bench/tasks/v1/t4_risk_check.json",
        "01be797f1c1c6fbdd8bfc1883ef3a50afc64c6c87f9d0fc7f135042d7ec34ace",
    ),
}
#: RUBRIC.md thresholds (minimum over reps of the per-rep mean score)
THRESHOLDS = {"placement": 0.90, "contradiction": 0.90, "summary": 0.80, "risk": 0.85}
MAX_JSON_FAIL_RATE = 0.02

# The rubric pieces are hlm bench's (one implementation, W2f); re-exported for callers of this file.
case_payload = v1.case_payload
semantic_check = v1.semantic_check
jaccard = v1.jaccard
score = v1.score


class GateAbort(RuntimeError):
    pass


# --------------------------------------------------------------------------- fixtures
def load_fixture(task: str, *, verify: bool = True) -> dict[str, Any]:
    rel, pin = FIXTURES[task]
    raw = (ROOT / rel).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if verify and digest != pin:
        raise GateAbort(f"fixture {rel} sha256 {digest} != pinned {pin}")
    return v1.load_fixture(task, raw)


def gate_items(tasks: list[str], reps: int, limit: int) -> list[Item]:
    """rep -> task -> case, exactly the order of the pre-W2f loop (recorded cassettes stay valid)."""
    items: list[Item] = []
    fixtures = {t: load_fixture(t) for t in tasks}
    for rep in range(reps):
        for task in tasks:
            cfg = fixtures[task]
            spec = load_task(task)
            cases = cfg["cases"][:limit] if limit else cfg["cases"]
            for case in cases:
                items.append(
                    Item(
                        suite="v1",
                        task=task,
                        job=JOB_NAMES[task],
                        case_id=case["id"],
                        tier=None,
                        pack="v1",
                        rep=rep,
                        spec=spec,
                        user=user_message(task, case_payload(task, case)),
                        validate=semantic_check(task, case),
                        scorer=lambda obj, t=task, c=case, f=cfg: score(t, obj, c, f),
                    )
                )
    return items


# --------------------------------------------------------------------------- run
async def run_profile(
    profile_name: str,
    *,
    tasks: list[str],
    reps: int,
    budget: MemoryBudget,
    mode: str,
    cassettes: CassetteStore | None,
    limit: int,
) -> dict[str, Any]:
    profile = named_profile(profile_name)
    ledger = MemoryLedger()
    redactor = Redactor()
    provider = Provider(
        [profile],
        mode=mode,
        budget=budget,
        ledger=ledger,
        cassettes=cassettes,
        redactor=redactor,
        timeout_s=120,
    )

    def show(o: Any) -> None:
        flag = {"ok": "OK ", "json_fail": "BAD"}.get(o.status, "ERR")
        s = 0.0 if o.score is None else o.score
        print(f"  [{profile_name}] rep{o.item.rep} {flag} {o.item.task}/{o.item.case_id} score={s:.2f}", flush=True)

    try:
        outcomes = await run_items(gate_items(tasks, reps, limit), provider, ledger, concurrency=1, progress=show)
    finally:
        await provider.aclose()
    calls: list[dict[str, Any]] = []
    for o in outcomes:
        if o.status in ("budget_deferred", "not_run"):
            raise GateAbort(f"MAX_USD runaway guard tripped: {o.error}")
        rec: dict[str, Any] = {"task": o.item.task, "case": o.item.case_id, "rep": o.item.rep}
        if o.status == "json_fail":
            rec.update(score=0.0, json_fail=True, infra_error=False, error=(o.error or "")[:200])
        elif o.status == "infra_error":
            rec.update(score=0.0, json_fail=False, infra_error=True, error=(o.error or "")[:200])
        else:
            rec.update(
                score=o.score,
                detail=o.detail,
                json_fail=False,
                infra_error=False,
                output=redactor.value(o.output),
                latency_ms=o.latency_ms,
                cost_usd=str(o.cost_usd),  # every attempt of the call (ledger), incl. a schema retry
                prompt_tokens=o.usage.get("prompt_tokens"),
                completion_tokens=o.usage.get("completion_tokens"),
            )
        calls.append(rec)
    return summarize(profile, tasks, reps, calls, ledger)


def summarize(
    profile: Any, tasks: list[str], reps: int, calls: list[dict[str, Any]], ledger: MemoryLedger
) -> dict[str, Any]:
    per_task: dict[str, Any] = {}
    ok = True
    for task in tasks:
        rep_means = []
        for rep in range(reps):
            scores = [c["score"] for c in calls if c["task"] == task and c["rep"] == rep]
            if scores:
                rep_means.append(sum(scores) / len(scores))
        mn = min(rep_means) if rep_means else 0.0
        passed = mn >= THRESHOLDS[task]
        ok &= passed
        per_task[task] = {
            "rep_means": [round(x, 4) for x in rep_means],
            "min": round(mn, 4),
            "mean": round(statistics.fmean(rep_means), 4) if rep_means else 0.0,
            "threshold": THRESHOLDS[task],
            "pass": passed,
        }
    n = len(calls)
    json_fail = sum(1 for c in calls if c["json_fail"])
    infra = sum(1 for c in calls if c["infra_error"])
    rate = json_fail / n if n else 0.0
    ok &= rate <= MAX_JSON_FAIL_RATE
    lat = sorted(c["latency_ms"] for c in calls if c.get("latency_ms") is not None)
    cost = sum((Decimal(r.cost_usd) for r in ledger.rows), Decimal(0))
    return {
        "profile": profile.name,
        "model_id": profile.model_id,
        "reps": reps,
        "calls": n,
        "ledger_rows": len(ledger.rows),
        "ledger_outcomes": {
            o: sum(1 for r in ledger.rows if r.outcome == o) for o in {r.outcome for r in ledger.rows}
        },
        "json_fail": json_fail,
        "json_fail_rate": round(rate, 4),
        "infra_error": infra,
        "latency_p50_ms": lat[len(lat) // 2] if lat else None,
        "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        "cost_usd": str(cost),
        "tasks": per_task,
        "pass": ok,
        "results": calls,
    }


def render(summary: dict[str, Any], stamp: str) -> str:
    s = summary
    verdict = "PASS" if s["pass"] else "FAIL"
    lines = [
        f"# Live gate {stamp} — profile `{s['profile']}` (`{s['model_id']}`)",
        "",
        f"Verdict: **{verdict}** · reps {s['reps']} · calls {s['calls']}"
        f" · JSON-fail {s['json_fail']} ({s['json_fail_rate']:.1%}) · infra errors {s['infra_error']}"
        f" · p50/p95 {s['latency_p50_ms']}/{s['latency_p95_ms']} ms · cost ${s['cost_usd']}",
        "",
        "| task | per-rep mean | min | mean | threshold | pass |",
        "|---|---|---|---|---|---|",
    ]
    for task, t in summary["tasks"].items():
        lines.append(
            f"| {task} | {', '.join(f'{x:.3f}' for x in t['rep_means'])} | {t['min']:.3f} | {t['mean']:.3f}"
            f" | {t['threshold']:.2f} | {'yes' if t['pass'] else 'NO'} |"
        )
    lines += [
        "",
        "Rubric: eval/live/RUBRIC.md. Outputs are redacted; raw provider responses are never stored.",
    ]
    return "\n".join(lines) + "\n"


def load_env_file(path: Path) -> None:
    """KEY=VALUE lines; existing environment wins; nothing is printed."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def amain(args: argparse.Namespace) -> int:
    if args.env_file:
        load_env_file(Path(args.env_file))
    tasks = [t for t in args.tasks.split(",") if t] if args.tasks else list(FIXTURES)
    profiles = [p for p in (args.profile, args.fallback) if p]
    cassettes = (
        CassetteStore(Path(args.cassette_dir), record_name=args.record_name) if args.mode != "live" else None
    )
    budget = MemoryBudget(Decimal(str(args.max_usd)))
    stamp = dt.date.today().isoformat()
    verdict = 0
    for name in profiles:
        print(f"== profile {name}", flush=True)
        try:
            summary = await run_profile(
                name,
                tasks=tasks,
                reps=args.reps,
                budget=budget,
                mode=args.mode,
                cassettes=cassettes,
                limit=args.limit,
            )
        except GateAbort as exc:
            print(f"FAIL (aborted): {exc}", flush=True)
            return 1
        print(render(summary, stamp))
        if not summary["pass"]:
            verdict = 1
        if args.out:
            outdir = Path(args.out) / f"{stamp}-{name}"
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
            (outdir / "SUMMARY.md").write_text(render(summary, stamp))
    print(f"run total spend ${budget.spent} (reserved ${budget.reserved}, cap ${budget.cap})")
    return verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="openrouter")
    ap.add_argument(
        "--fallback", default="openrouter-luna", help="run the gate again on this profile ('' = skip)"
    )
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument(
        "--tasks", default="", help="comma-separated subset of placement,contradiction,summary,risk"
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="first N cases per task (smoke only; not a gate run)"
    )
    ap.add_argument("--mode", choices=["live", "record", "replay"], default="live")
    ap.add_argument("--cassette-dir", default=str(ROOT / "tests" / "cassettes" / "w2a"))
    ap.add_argument("--record-name", default="live_gate")
    ap.add_argument("--out", default=str(ROOT / "eval" / "live"), help="'' = do not write results")
    ap.add_argument(
        "--env-file", default="", help="load KEY=VALUE secrets (e.g. the repo .env) without echoing"
    )
    return asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
