#!/usr/bin/env python3
"""G-LIVE-B — release-blocking live gate for W2b (PHASE2-4-ROADMAP W2b, CC-5, D-067).

Runs the W2b fixtures (``tests/fixtures/w2b/{relations,placement}.json``, pinned by sha256)
through the PRODUCTION prompts (``place/v1``, ``relate/v1``, ``relate_verify/v1``), the production
payload builders (``librarian/tasks/write_review.py``), batching (4 items per placement call; one
relation call per new item with its ≤ 8 existing items) and the D-067 guards
(``librarian/guards.py``: cited ids, quote evidence, temporal consistency, calibrated tiers,
abstention, the verifier for high-impact judgements), once per profile ALONE (the verifier is a
second call on the same profile here: the gate measures each model, not the chain), ``--reps``
times. ``--max-usd`` is a runaway guard for the whole run: a refused reservation is a FAIL.

Metrics per rep (the pass rule takes the MINIMUM over reps):
* placement accuracy: importance within ±2 of gold AND stability exact (≥ 0.90);
* contradiction exact: the final decision (relation, supersedes) after every guard equals gold,
  over every pair (≥ 0.90); an abstention is ``none``;
* false supersede: pairs whose final supersession is raised but differs from gold, over the pairs
  whose gold has no supersession (≤ 0.02).
Also reported: per-class accuracy, the confusion matrix, per-confidence precision (the
calibration evidence for ``guards.TIERS``), guard counters, verifier agreement, JSON failures,
latency and cost. Outputs (redacted, no raw provider response): ``eval/live/<date>-w2b-<profile>/``.

    uv run --frozen python eval/live/run_w2b.py --profile openrouter-gpt6-luna --fallback openrouter \\
        --reps 3 --max-usd 4 --env-file .env [--mode record --cassette-dir tests/cassettes/w2b]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval" / "live"))

from hlmemo.librarian import guards  # noqa: E402
from hlmemo.librarian.budget import MemoryBudget  # noqa: E402
from hlmemo.librarian.cassette import CassetteStore  # noqa: E402
from hlmemo.librarian.errors import BudgetDeferred, LibrarianError, SchemaFail  # noqa: E402
from hlmemo.librarian.ledger import MemoryLedger  # noqa: E402
from hlmemo.librarian.profiles import named_profile  # noqa: E402
from hlmemo.librarian.prompts import load_task  # noqa: E402
from hlmemo.librarian.provider import Provider  # noqa: E402
from hlmemo.librarian.redact import Redactor  # noqa: E402
from hlmemo.librarian.tasks import user_message  # noqa: E402
from hlmemo.librarian.tasks.write_review import (  # noqa: E402
    PAIRS_PER_CALL,
    place_payload,
    relate_payload,
    relate_texts,
    verify_payload,
)

FIXTURES: dict[str, tuple[str, str]] = {
    "relations": (
        "tests/fixtures/w2b/relations.json",
        "4a065fcf4d9773d9fb884d5537e193640efc33d5e3c846fb2baa32394a653789",
    ),
    "placement": (
        "tests/fixtures/w2b/placement.json",
        "627866da3446b4b15c4c5636b62cacedef31090378e2e1bcb9cc8040f0854bcf",
    ),
}
THRESHOLDS = {"placement": 0.90, "contradiction_exact": 0.90, "false_supersede_max": 0.02}
MAX_JSON_FAIL_RATE = 0.02
PLACE_BATCH = 4


class GateAbort(RuntimeError):
    pass


@dataclass(slots=True)
class Row:
    version_id: int
    kind: str
    title: str
    body: str
    valid_from: dt.datetime
    tags: list[str] = field(default_factory=list)


def _ts(day: str) -> dt.datetime:
    return dt.datetime.fromisoformat(day).replace(tzinfo=dt.UTC)


def load_fixture(name: str, *, verify: bool = True) -> dict[str, Any]:
    rel, pin = FIXTURES[name]
    raw = (ROOT / rel).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if verify and pin != "PIN" and digest != pin:
        raise GateAbort(f"fixture {rel} sha256 {digest} != pinned {pin}")
    return json.loads(raw)


def relation_cases(fx: dict[str, Any]) -> list[dict[str, Any]]:
    """One case per group: the new row, its existing rows (with cross flags) and the gold."""
    cases = []
    for gi, g in enumerate(fx["groups"], start=1):
        base = 10000 + gi * 10
        new = Row(base, g["new"]["kind"], g["new"]["title"], g["new"]["text"], _ts(g["new"]["t"]))
        existing = []
        for k, e in enumerate(g["existing"], start=1):
            row = Row(base + k, e["kind"], e["title"], e["text"], _ts(e["t"]))
            existing.append({"row": row, "cross": e["project"] == "other", "fx": e})
        cases.append({"id": g["id"], "new": new, "lang": g["new"]["lang"], "existing": existing})
    return cases


def placement_rows(fx: dict[str, Any]) -> list[tuple[Row, dict[str, Any]]]:
    out = []
    for i, it in enumerate(fx["items"], start=1):
        out.append((Row(20000 + i, it["kind"], it["title"], it["text"], _ts("2026-09-01")), it))
    return out


# --------------------------------------------------------------------------- one rep
async def _call(provider: Provider, task: str, payload: dict[str, Any], rec: dict[str, Any]) -> Any:
    try:
        res = await provider.complete(load_task(task), user_message(task, payload))
    except BudgetDeferred as exc:
        raise GateAbort(f"MAX_USD runaway guard tripped: {exc}") from exc
    except SchemaFail:
        rec["json_fail"] += 1
        return None
    except LibrarianError as exc:
        rec["infra_error"] += 1
        rec.setdefault("errors", []).append(str(exc)[:200])
        return None
    rec["calls"] += 1
    rec["latency_ms"].append(res.latency_ms)
    return res


async def run_rep(provider: Provider, rel_fx: dict[str, Any], place_fx: dict[str, Any]) -> dict[str, Any]:
    redactor = provider.redactor
    rec: dict[str, Any] = {"calls": 0, "json_fail": 0, "infra_error": 0, "latency_ms": []}
    # placement
    place: list[dict[str, Any]] = []
    rows = placement_rows(place_fx)
    for i in range(0, len(rows), PLACE_BATCH):
        batch = rows[i : i + PLACE_BATCH]
        payload = place_payload([r for r, _ in batch])
        res = await _call(provider, "place", payload, rec)
        out = (
            {}
            if res is None
            else guards.check_placement(res.output, [f"v{r.version_id}" for r, _ in batch])[0]
        )
        for r, it in batch:
            got = out.get(f"v{r.version_id}")
            gi, gs = it["gold"]
            imp_ok = got is not None and abs(int(got["importance"]) - gi) <= 2
            stab_ok = got is not None and got.get("stability") == gs
            place.append(
                {
                    "id": it["id"],
                    "lang": it["lang"],
                    "gold": [gi, gs],
                    "got": None if got is None else [got["importance"], got.get("stability")],
                    "ok": bool(imp_ok and stab_ok),
                }
            )
    # relations
    pairs: list[dict[str, Any]] = []
    guard_counts: Counter[str] = Counter()
    for case in relation_cases(rel_fx):
        s = case["new"]
        cands = [(e["row"], e["cross"]) for e in case["existing"]][:PAIRS_PER_CALL]
        res = await _call(provider, "relate", relate_payload(s, cands), rec)
        judged, counts = guards.check_relations(
            {} if res is None else res.output, relate_texts(s, cands), redact=redactor.text
        )
        guard_counts.update(counts)
        todo = []
        for e, j in zip(case["existing"], judged, strict=True):
            kind = guards.high_impact(j, cross_project=e["cross"])
            if kind is not None:
                todo.append((e, j, kind))
        if todo:
            items = verify_payload([(s, e["row"]) for e, _, _ in todo])
            vres = await _call(provider, "relate_verify", {"pairs": items}, rec)
            answers = (
                {} if vres is None else guards.check_verifications(vres.output, [it["id"] for it in items])
            )
            for it, (e, j, kind) in zip(items, todo, strict=True):
                guards.apply_verification(
                    j, kind, answers.get(it["id"]), new_is_b=s.valid_from >= e["row"].valid_from
                )
                guard_counts["verified" if j.verification["agreed"] else "verify_disagreed"] += 1
        raw_by_id = {}
        if res is not None:
            for r in res.output.get("results") or []:
                if isinstance(r, dict) and str(r.get("id")) not in raw_by_id:
                    raw_by_id[str(r.get("id"))] = r
        for e, j in zip(case["existing"], judged, strict=True):
            final = (j.relation, j.supersedes) if j.raised else ("none", "none")
            gold = tuple(e["fx"]["gold"])
            raw = raw_by_id.get(f"v{e['row'].version_id}") or {}
            pairs.append(
                {
                    "group": case["id"],
                    "id": e["fx"]["id"],
                    "class": e["fx"]["class"],
                    "cross": e["cross"],
                    "lang": f"{case['lang']}-{e['fx']['lang']}",
                    "gold": list(gold),
                    "final": list(final),
                    "raw": [raw.get("relation"), raw.get("supersedes"), raw.get("confidence")],
                    "tier": j.tier,
                    "flags": list(j.flags),
                    "verified": None if j.verification is None else j.verification.get("agreed"),
                    "exact": tuple(final) == gold,
                    "false_supersede": final[1] != "none" and final[1] != gold[1],
                }
            )
    return {"placement": place, "pairs": pairs, "guards": dict(guard_counts), **rec}


def rep_metrics(rep: dict[str, Any]) -> dict[str, Any]:
    place, pairs = rep["placement"], rep["pairs"]
    no_sup = [p for p in pairs if p["gold"][1] == "none"]
    fs = sum(p["false_supersede"] for p in pairs)
    by_class: dict[str, list[bool]] = {}
    for p in pairs:
        by_class.setdefault(p["class"], []).append(p["exact"])
    calib: dict[str, list[int]] = {}
    for p in pairs:
        rel, _sup, conf = p["raw"]
        if rel and rel != "none" and conf:
            ok = rel == p["gold"][0]
            calib.setdefault(f"{rel}/{conf}", []).append(int(ok))
    confusion = Counter(f"{p['gold'][0]}->{p['final'][0]}" for p in pairs)
    return {
        "placement": round(sum(p["ok"] for p in place) / len(place), 4),
        "contradiction_exact": round(sum(p["exact"] for p in pairs) / len(pairs), 4),
        "false_supersede": round(fs / max(1, len(no_sup)), 4),
        "false_supersede_all_pairs": round(fs / len(pairs), 4),
        "per_class": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_class.items())},
        "per_class_n": {k: len(v) for k, v in sorted(by_class.items())},
        "cross_exact": round(
            sum(p["exact"] for p in pairs if p["cross"]) / max(1, sum(p["cross"] for p in pairs)), 4
        ),
        "calibration_precision": {k: [round(sum(v) / len(v), 4), len(v)] for k, v in sorted(calib.items())},
        "confusion": dict(sorted(confusion.items())),
        "abstained": sum(1 for p in pairs if p["raw"][0] not in (None, "none") and p["final"][0] == "none"),
        "pairs": len(pairs),
        "placement_items": len(place),
    }


async def run_profile(
    profile_name: str, *, reps: int, budget: MemoryBudget, mode: str, cassettes: CassetteStore | None
) -> dict[str, Any]:
    profile = named_profile(profile_name)
    ledger = MemoryLedger()
    provider = Provider(
        [profile],
        mode=mode,
        budget=budget,
        ledger=ledger,
        cassettes=cassettes,
        redactor=Redactor(),
        timeout_s=120,
    )
    rel_fx, place_fx = load_fixture("relations"), load_fixture("placement")
    reps_out = []
    try:
        for rep in range(reps):
            out = await run_rep(provider, rel_fx, place_fx)
            m = rep_metrics(out)
            print(
                f"  [{profile_name}] rep{rep}: placement {m['placement']:.3f}"
                f" contradiction_exact {m['contradiction_exact']:.3f}"
                f" false_supersede {m['false_supersede']:.3f}"
                f" calls {out['calls']} json_fail {out['json_fail']} infra {out['infra_error']}",
                flush=True,
            )
            reps_out.append({"metrics": m, **out})
    finally:
        await provider.aclose()
    return summarize(profile, reps_out, ledger)


def summarize(profile: Any, reps: list[dict[str, Any]], ledger: MemoryLedger) -> dict[str, Any]:
    ms = [r["metrics"] for r in reps]
    mins = {
        "placement": min(m["placement"] for m in ms),
        "contradiction_exact": min(m["contradiction_exact"] for m in ms),
        "false_supersede": max(m["false_supersede"] for m in ms),
    }
    means = {
        "placement": round(statistics.fmean(m["placement"] for m in ms), 4),
        "contradiction_exact": round(statistics.fmean(m["contradiction_exact"] for m in ms), 4),
        "false_supersede": round(statistics.fmean(m["false_supersede"] for m in ms), 4),
    }
    calls = sum(r["calls"] for r in reps)
    json_fail = sum(r["json_fail"] for r in reps)
    rate = json_fail / max(1, calls + json_fail)
    ok = (
        mins["placement"] >= THRESHOLDS["placement"]
        and mins["contradiction_exact"] >= THRESHOLDS["contradiction_exact"]
        and mins["false_supersede"] <= THRESHOLDS["false_supersede_max"]
        and rate <= MAX_JSON_FAIL_RATE
        and sum(r["infra_error"] for r in reps) == 0
    )
    lat = sorted(x for r in reps for x in r["latency_ms"])
    cost = sum((Decimal(r.cost_usd) for r in ledger.rows), Decimal(0))
    return {
        "profile": profile.name,
        "model_id": profile.model_id,
        "reps": len(reps),
        "worst": mins,
        "mean": means,
        "thresholds": THRESHOLDS,
        "calls": calls,
        "json_fail": json_fail,
        "json_fail_rate": round(rate, 4),
        "infra_error": sum(r["infra_error"] for r in reps),
        "latency_p50_ms": lat[len(lat) // 2] if lat else None,
        "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        "cost_usd": str(cost),
        "pass": ok,
        "reps_detail": reps,
    }


def render(s: dict[str, Any], stamp: str) -> str:
    verdict = "PASS" if s["pass"] else "FAIL"
    w, m, t = s["worst"], s["mean"], s["thresholds"]
    lines = [
        f"# G-LIVE-B {stamp} — profile `{s['profile']}` (`{s['model_id']}`)",
        "",
        f"Verdict: **{verdict}** · reps {s['reps']} · calls {s['calls']} · JSON-fail {s['json_fail']}"
        f" ({s['json_fail_rate']:.1%}) · infra errors {s['infra_error']}"
        f" · p50/p95 {s['latency_p50_ms']}/{s['latency_p95_ms']} ms · cost ${s['cost_usd']}",
        "",
        "| metric | worst over reps | mean | threshold |",
        "|---|---|---|---|",
        f"| placement | {w['placement']:.3f} | {m['placement']:.3f} | ≥ {t['placement']:.2f} |",
        f"| contradiction exact | {w['contradiction_exact']:.3f} | {m['contradiction_exact']:.3f}"
        f" | ≥ {t['contradiction_exact']:.2f} |",
        f"| false supersede | {w['false_supersede']:.3f} | {m['false_supersede']:.3f}"
        f" | ≤ {t['false_supersede_max']:.2f} |",
        "",
        "Per class (rep 0): "
        + ", ".join(
            f"{k} {v:.2f} (n={s['reps_detail'][0]['metrics']['per_class_n'][k]})"
            for k, v in s["reps_detail"][0]["metrics"]["per_class"].items()
        ),
        "",
        "Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted;"
        " raw provider responses are never stored.",
    ]
    return "\n".join(lines) + "\n"


def load_env_file(path: Path) -> None:
    import os

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def amain(args: argparse.Namespace) -> int:
    if args.env_file:
        load_env_file(Path(args.env_file))
    profiles = [p for p in (args.profile, args.fallback) if p]
    cassettes = (
        CassetteStore(Path(args.cassette_dir), record_name=args.record_name) if args.mode != "live" else None
    )
    budget = MemoryBudget(Decimal(str(args.max_usd)))
    stamp = dt.date.today().isoformat()
    verdict = 0
    summaries = []
    for name in profiles:
        print(f"== profile {name}", flush=True)
        try:
            summary = await run_profile(
                name, reps=args.reps, budget=budget, mode=args.mode, cassettes=cassettes
            )
        except GateAbort as exc:
            print(f"FAIL (aborted): {exc}", flush=True)
            return 1
        print(render(summary, stamp))
        summaries.append(summary)
        if not summary["pass"]:
            verdict = 1
        if args.out:
            outdir = Path(args.out) / f"{stamp}-w2b-{name}"
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
            (outdir / "SUMMARY.md").write_text(render(summary, stamp))
    print(f"run total spend ${budget.spent} (reserved ${budget.reserved}, cap ${budget.cap})")
    return verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="openrouter-gpt6-luna")
    ap.add_argument("--fallback", default="openrouter", help="run the gate again on this profile ('' = skip)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=4.0)
    ap.add_argument("--mode", choices=["live", "record", "replay"], default="live")
    ap.add_argument("--cassette-dir", default=str(ROOT / "tests" / "cassettes" / "w2b"))
    ap.add_argument("--record-name", default="live_gate_w2b")
    ap.add_argument("--out", default=str(ROOT / "eval" / "live"), help="'' = do not write results")
    ap.add_argument(
        "--env-file", default="", help="load KEY=VALUE secrets (e.g. the repo .env) without echoing"
    )
    return asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
