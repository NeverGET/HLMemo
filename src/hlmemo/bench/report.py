"""Bench summaries, the markdown report, McNemar comparison and offline re-scoring (W2f).

Everything here works on plain row dicts (one per bench call), so a live/replayed ``hlm bench``
run and a re-scored saved result file (``bench/run.py`` v1/v2 format) produce the same summary.
The report is deterministic: no clock, no host, fixed float formatting, rows in item order, so a
replayed run renders byte-identically (G-B1).

Definitions (bench/README, D-067):
* score = the task's deterministic rubric (v1: eval/live/RUBRIC.md; v2: bench/v2/DESIGN.md §2);
* correct = score >= 0.8; error rate = 1 - correct / scored calls (per task: D-067's per-task
  error rate, reported next to $/correct);
* JSON fail (first) = the first answer did not parse or failed the schema/task validation (the
  production provider then retries once); JSON fail (final) = the retry failed too (scored 0);
* infra / cap = provider unavailable after retries, or the ``--max-usd`` reservation refused:
  excluded from accuracy;
* $/task = cost / scored calls; $/correct = cost / correct calls; projected $/month =
  mean cost per call x calls/day x 30 (the D-019 cost model: 60 librarian jobs/day).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

from hlmemo.bench import v1, v2

CORRECT_AT = 0.8
DAYS_PER_MONTH = 30
SCORED = ("ok", "json_fail")


# --------------------------------------------------------------------------- stats
def _pct_rank(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r["status"] in SCORED]
    n = len(scored)
    correct = sum(1 for r in scored if r["score"] >= CORRECT_AT)
    responded = [r for r in rows if r["status"] in SCORED]
    per_rep: dict[int, list[float]] = {}
    for r in scored:
        per_rep.setdefault(int(r["rep"]), []).append(r["score"])
    rep_means = [sum(v) / len(v) for _, v in sorted(per_rep.items())]
    mu = sum(rep_means) / len(rep_means) if rep_means else None
    cost = sum((Decimal(str(r.get("cost_usd") or 0)) for r in rows), Decimal(0))
    lat = [int(r["latency_ms"]) for r in rows if r["status"] == "ok" and r.get("latency_ms") is not None]
    return {
        "n": n,
        "mean": _r(sum(r["score"] for r in scored) / n) if n else None,
        "correct": correct,
        "correct_rate": _r(correct / n) if n else None,
        "error_rate": _r(1 - correct / n) if n else None,
        "min_rep": _r(min(rep_means)) if rep_means else None,
        "rep_sd": _r((sum((x - mu) ** 2 for x in rep_means) / len(rep_means)) ** 0.5) if rep_means else None,
        "json_fail_first": sum(1 for r in responded if r.get("first_json_fail")),
        "json_fail_first_rate": _r(sum(1 for r in responded if r.get("first_json_fail")) / len(responded))
        if responded
        else None,
        "json_fail_final": sum(1 for r in rows if r["status"] == "json_fail"),
        "infra_error": sum(1 for r in rows if r["status"] == "infra_error"),
        "budget_deferred": sum(1 for r in rows if r["status"] == "budget_deferred"),
        "not_run": sum(1 for r in rows if r["status"] == "not_run"),
        "latency_p50_ms": _pct_rank(lat, 0.50),
        "latency_p95_ms": _pct_rank(lat, 0.95),
        "latency_avg_ms": round(sum(lat) / len(lat)) if lat else None,
        "cost_usd": float(cost.quantize(Decimal("0.00000001"))),
        "usd_per_task": _r(float(cost) / n, 8) if n else None,
        "usd_per_correct": _r(float(cost) / correct, 8) if correct else None,
    }


def _order(suite: str) -> tuple[list[str], dict[str, str]]:
    if suite == "v1":
        return list(v1.TASKS), dict(v1.TASK_LABEL)
    return list(v2.FAMILIES), {f: f for f in v2.FAMILIES}


def gate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """bench-v2 error metrics (the G-LIVE-B/C/D shapes); v1 rows yield {}."""
    ok = [r for r in rows if r["status"] == "ok" and r.get("detail")]
    m: dict[str, Any] = {}
    t6 = [r for r in ok if r["task"] == "T6"]
    if t6:
        pairs = sum(r["detail"].get("pairs", 0) for r in t6)
        fs = sum(r["detail"].get("false_supersede", 0) for r in t6)
        m["T6_false_supersede_rate"] = _r(fs / pairs) if pairs else None
    t9 = [r for r in ok if r["task"] == "T9"]
    if t9:
        pos = [r for r in t9 if r["detail"].get("caught") is not None]
        neg = [r for r in t9 if r["detail"].get("false_warn") is not None]
        m["T9_catch_rate"] = _r(sum(1 for r in pos if r["detail"]["caught"]) / len(pos)) if pos else None
        m["T9_false_warn_rate"] = (
            _r(sum(1 for r in neg if r["detail"]["false_warn"]) / len(neg)) if neg else None
        )
    t10 = [r for r in ok if r["task"] == "T10"]
    if t10:
        neg10 = [r for r in t10 if not r["detail"].get("answerable")]
        m["T10_false_answer_rate"] = (
            _r(sum(1 for r in neg10 if r["detail"]["false_answer"]) / len(neg10)) if neg10 else None
        )
    t11 = [r for r in ok if r["task"] == "T11"]
    if t11:
        m["T11_complied"] = f"{sum(1 for r in t11 if r['detail'].get('complied'))}/{len(t11)}"
    t7 = [r for r in ok if r["task"] == "T7" and not r["detail"].get("identifier_optional")]
    if t7:
        m["T7_identifier_hit_rate"] = _r(sum(1 for r in t7 if r["detail"].get("identifier_hit")) / len(t7))
    t8 = [r for r in ok if r["task"] == "T8"]
    if t8:
        m["T8_avg_hallucinated_tokens"] = _r(
            sum(len(r["detail"].get("hallucinated", [])) for r in t8) / len(t8), 2
        )
        m["T8_coverage"] = _r(sum(r["detail"].get("coverage", 0) for r in t8) / len(t8))
    return m


def summarize(rows: list[dict[str, Any]], *, suite: str, calls_per_day: int = 60) -> dict[str, Any]:
    tasks, _ = _order(suite)
    out: dict[str, Any] = {"suite": suite, "overall": group_stats(rows), "tasks": {}, "tiers": {}}
    out["task_tier"], out["packs"] = {}, {}
    for t in tasks:
        tr = [r for r in rows if r["task"] == t]
        if tr:
            out["tasks"][t] = group_stats(tr)
            for tier in v2.TIERS:
                tt = [r for r in tr if r.get("tier") == tier]
                if tt:
                    out["task_tier"][f"{t}/{tier}"] = group_stats(tt)
    for tier in v2.TIERS:
        tt = [r for r in rows if r.get("tier") == tier]
        if tt:
            out["tiers"][tier] = group_stats(tt)
    for pk in sorted({str(r.get("pack")) for r in rows}):
        out["packs"][pk] = group_stats([r for r in rows if str(r.get("pack")) == pk])
    means = [s["mean"] for s in out["tasks"].values() if s["mean"] is not None]
    out["macro_mean"] = _r(sum(means) / len(means)) if means else None
    errs = [s["error_rate"] for s in out["tasks"].values() if s["error_rate"] is not None]
    out["macro_error_rate"] = _r(sum(errs) / len(errs)) if errs else None
    out["metrics"] = gate_metrics(rows)
    calls = [r for r in rows if r["status"] not in ("not_run", "budget_deferred")]
    cost = sum((Decimal(str(r.get("cost_usd") or 0)) for r in calls), Decimal(0))
    per_call = float(cost) / len(calls) if calls else None
    out["projection"] = {
        "calls_per_day": calls_per_day,
        "usd_per_call": _r(per_call, 8),
        "usd_per_month": _r(per_call * calls_per_day * DAYS_PER_MONTH, 2) if per_call is not None else None,
    }
    tok = {"prompt": 0, "completion": 0, "cached": 0, "reasoning": 0}
    for r in rows:
        for k in tok:
            tok[k] += int(r.get(f"{k}_tokens") or 0)
    out["tokens"] = tok
    return out


# --------------------------------------------------------------------------- markdown
def _p(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _usd(v: float | None, nd: int = 6) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def _ms(v: int | None) -> str:
    return "-" if v is None else f"{v}"


def _versions(v: Any) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k} {x}" for k, x in v.items())
    return str(v)


def render_md(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    """The run report. ``meta`` must be deterministic (no timestamps) for G-B1."""
    suite = summary["suite"]
    tasks, label = _order(suite)
    present = [t for t in tasks if t in summary["tasks"]]
    o = summary["overall"]
    lines = [
        f"# hlm bench — suite {suite} — `{meta.get('model_id')}`",
        "",
        f"profile `{meta.get('profile')}` · mode {meta.get('mode')} · reps {meta.get('reps')}"
        f" · gold {meta.get('gold')} · packs {meta.get('packs')}"
        + (f" · limit {meta['limit']}" if meta.get("limit") else "")
        + (" · **PARTIAL: stopped at the --max-usd cap**" if meta.get("aborted") else ""),
        "",
        f"prompt {_versions(meta.get('prompt_versions'))} · schema {_versions(meta.get('schema_versions'))}"
        f" · redaction {meta.get('redaction_version')} · max-usd {meta.get('max_usd')}",
        "",
        f"budget: {meta.get('budget', '-')} · config {meta.get('config_hash', '-')}",
        "",
        "## Overview",
        "",
        "| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms"
        " | cost USD | $/task | $/correct | $/month |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| {_p(summary['macro_mean'])} | {o['correct']}/{o['n']} | {_p(o['error_rate'])}"
        f" | {o['json_fail_first']} ({_p(o['json_fail_first_rate'])}) / {o['json_fail_final']}"
        f" | {o['infra_error']} | {o['budget_deferred'] + o['not_run']} | {_ms(o['latency_p50_ms'])}"
        f" | {_ms(o['latency_p95_ms'])} | {_usd(o['cost_usd'])} | {_usd(o['usd_per_task'], 8)}"
        f" | {_usd(o['usd_per_correct'], 8)} | {_usd(summary['projection']['usd_per_month'], 2)} |",
        "",
        "## Per task",
        "",
        "| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms"
        " | $/task | $/correct | easy | medium | hard |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in present:
        s = summary["tasks"][t]
        tiers = " | ".join(_p(summary["task_tier"].get(f"{t}/{x}", {}).get("mean")) for x in v2.TIERS)
        lines.append(
            f"| {label[t]} {t if suite == 'v1' else ''}".rstrip()
            + f" | {s['n']} | {_p(s['mean'])} | {s['correct']} | {_p(s['error_rate'])} | {_p(s['min_rep'])}"
            f" | {_p(s['rep_sd'])} | {s['json_fail_first']}/{s['json_fail_final']}"
            f" | {_ms(s['latency_p50_ms'])}"
            f" | {_ms(s['latency_p95_ms'])} | {_usd(s['usd_per_task'], 8)} | {_usd(s['usd_per_correct'], 8)}"
            f" | {tiers} |"
        )
    for tier in v2.TIERS:
        if tier in summary["tiers"]:
            s = summary["tiers"][tier]
            lines.append(
                f"| all/{tier} | {s['n']} | {_p(s['mean'])} | {s['correct']} | {_p(s['error_rate'])}"
                f" | {_p(s['min_rep'])} | {_p(s['rep_sd'])} | {s['json_fail_first']}/{s['json_fail_final']}"
                f" | {_ms(s['latency_p50_ms'])} | {_ms(s['latency_p95_ms'])} | {_usd(s['usd_per_task'], 8)}"
                f" | {_usd(s['usd_per_correct'], 8)} | | | |"
            )
    if summary["packs"]:
        lines += [
            "",
            "Packs: " + ", ".join(f"{k} {_p(v['mean'])} (n={v['n']})" for k, v in summary["packs"].items()),
        ]
    if summary["metrics"]:
        lines += ["", "Error metrics: " + ", ".join(f"{k}={v}" for k, v in summary["metrics"].items())]
    tok = summary["tokens"]
    proj = summary["projection"]
    lines += [
        f"Tokens: prompt {tok['prompt']}, completion {tok['completion']} (reasoning {tok['reasoning']}),"
        f" cached {tok['cached']}.",
        f"Projection: {_usd(proj['usd_per_call'], 8)} USD/call x {proj['calls_per_day']} calls/day x"
        f" {DAYS_PER_MONTH} = {_usd(proj['usd_per_month'], 2)} USD/month.",
        "",
        "correct = score >= 0.8; error rate = 1 - correct/n;"
        " JSON first = the first answer failed parse/schema"
        " (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded."
        " Definitions: src/hlmemo/bench/report.py.",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- McNemar
def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs (b: A right/B wrong, c: the reverse)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[tuple, tuple[bool, bool]]:
    def key_map(rows: list[dict[str, Any]]) -> dict[tuple, bool]:
        return {
            (r["task"], r["case_id"], int(r["rep"])): r["score"] >= CORRECT_AT
            for r in rows
            if r["status"] in SCORED
        }

    a, b = key_map(rows_a), key_map(rows_b)
    return {k: (a[k], b[k]) for k in sorted(a.keys() & b.keys())}


def compare(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]], *, suite: str) -> dict[str, Any]:
    pairs = paired(rows_a, rows_b)
    tasks, _ = _order(suite)

    def block(keys: Iterable[tuple]) -> dict[str, Any]:
        ks = list(keys)
        a_only = sum(1 for k in ks if pairs[k][0] and not pairs[k][1])
        b_only = sum(1 for k in ks if pairs[k][1] and not pairs[k][0])
        return {
            "pairs": len(ks),
            "a_correct": sum(1 for k in ks if pairs[k][0]),
            "b_correct": sum(1 for k in ks if pairs[k][1]),
            "a_only": a_only,
            "b_only": b_only,
            "p_mcnemar": round(mcnemar_exact(a_only, b_only), 6),
        }

    out = {"overall": block(pairs), "tasks": {}}
    for t in tasks:
        ks = [k for k in pairs if k[0] == t]
        if ks:
            out["tasks"][t] = block(ks)
    return out


def render_compare_md(cmp: dict[str, Any], name_a: str, name_b: str) -> str:
    lines = [
        f"# hlm bench --compare: A = `{name_a}` vs B = `{name_b}`",
        "",
        "Paired on (task, case, rep); correct = score >= 0.8;"
        " exact two-sided McNemar on the discordant pairs.",
        "",
        "| task | pairs | A correct | B correct | A only | B only | McNemar p |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, blk in [*cmp["tasks"].items(), ("overall", cmp["overall"])]:
        lines.append(
            f"| {name} | {blk['pairs']} | {blk['a_correct']} | {blk['b_correct']} | {blk['a_only']}"
            f" | {blk['b_only']} | {blk['p_mcnemar']:.4f} |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- result files
def load_rows(spec: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows of a result file: ``path.json`` (an hlm bench result) or ``path.json#model`` (one model of
    a ``bench/run.py`` result). Returns (rows, meta)."""
    path, _, model = spec.partition("#")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if "rows" in doc:  # hlm bench format
        return doc["rows"], doc.get("meta", {})
    calls = doc["calls"]
    models = sorted({c["model"] for c in calls})
    if not model:
        if len(models) != 1:
            raise ValueError(f"{path} holds several models {models}: use {path}#<model>")
        model = models[0]
    rows = [legacy_row(c) for c in calls if c["model"] == model and not c.get("dry_run")]
    mid = next((c.get("model_id") for c in calls if c["model"] == model), model)
    suite = "v2" if any(c.get("suite") == "v2" for c in calls) else "v1"
    args = doc.get("args") or {}
    prompt = doc.get("system_prompt") or ""
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    same_prompt = suite == "v2" and prompt_sha == v2.PROMPT_SHA256
    config = {
        "harness": "bench/run.py (legacy raw API)",
        "model_id": mid,
        "reasoning_arg": args.get("reasoning"),
        "temperature": 0,
        "seed": 42,
        "response_format": "json_object",
        "prompt_sha256": prompt_sha,
    }
    raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, {
        "model_id": mid,
        "model": model,
        "suite": suite,
        "source": Path(path).name,
        "harness": config["harness"],
        "reasoning": {"bench_run_reasoning": args.get("reasoning")} if args.get("reasoning") else None,
        "reps": args.get("runs"),
        "aborted": bool(doc.get("partial")),
        "limit": args.get("limit") or 0,
        "tasks_subset": args.get("tasks") or None,
        "prompt_versions": v2.PROMPT_VERSION if same_prompt else f"bench-v1 single prompt {prompt_sha[:12]}",
        "schema_versions": v2.SCHEMA_VERSION if suite == "v2" else "bench-v1",
        "config": config,
        "config_hash": hashlib.sha256(raw).hexdigest()[:12],
    }


_LEGACY_V1_TASK = {
    "placement": "placement",
    "contradiction": "contradiction",
    "summarization": "summary",
    "risk_check": "risk",
}


def legacy_row(c: dict[str, Any]) -> dict[str, Any]:
    """A ``bench/run.py`` call record as a report row (first-attempt semantics: no retry there)."""
    status = "infra_error" if c.get("infra_error") else ("json_fail" if c.get("json_fail") else "ok")
    task = c.get("family") or _LEGACY_V1_TASK.get(c["task"], c["task"])
    return {
        "suite": c.get("suite", "v1"),
        "task": task,
        "job": c.get("job", c.get("task")),
        "case_id": c["case_id"],
        "tier": c.get("tier"),
        "pack": c.get("pack", "v1"),
        "rep": c.get("run", 0),
        "status": status,
        "score": c.get("score") if status != "infra_error" else None,
        "detail": c.get("detail") or {},
        "latency_ms": c.get("latency_ms"),
        "cost_usd": c.get("cost_usd") or 0,
        "prompt_tokens": c.get("prompt_tokens"),
        "completion_tokens": c.get("completion_tokens"),
        "cached_tokens": c.get("cached_tokens"),
        "reasoning_tokens": c.get("reasoning_tokens"),
        "first_json_fail": bool(c.get("json_fail")),
        "parsed": c.get("parsed"),
        "content": c.get("content"),
    }


def rescore_rows(
    rows: list[dict[str, Any]], packs: list[tuple[str, dict]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Re-score saved v2 rows against ``packs`` (raw or adjusted gold). Rows whose case is not in the
    packs (a dropped case, or the private pack not given) are left out and counted."""
    cases = {(fam, c["id"]): (c, pack) for fam, pack in packs for c in pack["cases"]}
    out, skipped = [], {"missing_case": 0}
    for r in rows:
        hit = cases.get((r["task"], r["case_id"]))
        if hit is None:
            skipped["missing_case"] += 1
            continue
        case, pack = hit
        nr = dict(r)
        obj = r.get("parsed") if r.get("parsed") is not None else r.get("output")  # legacy | hlm bench
        if r["status"] == "ok" and obj is not None:
            raw = r.get("content") or v2.raw_of(obj)
            nr["score"], nr["detail"] = v2.score(r["task"], obj, case, pack, raw=raw)
        out.append(nr)
    return out, skipped


__all__ = [
    "CORRECT_AT",
    "compare",
    "gate_metrics",
    "group_stats",
    "legacy_row",
    "load_rows",
    "mcnemar_exact",
    "render_compare_md",
    "render_md",
    "rescore_rows",
    "summarize",
]
