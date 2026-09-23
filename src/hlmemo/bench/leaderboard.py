"""The W-E leaderboard (``eval/results/leaderboard.json`` → ``LEADERBOARD.md``).

Two kinds of boards:

* **retrieval** (per corpus: A, B dev, B sealed, later each Phase 5 project set): the best score so
  far with commit, config, prompt versions, model id and cost/query (roadmap W-E, D-058). The
  optimize-to-best rules are checked by ``check_retrieval``.
* **librarian** (bench suites): one entry per model run, scored raw (D-066 gold) and adjusted (the
  adjudication overlay), with the per-task error rates next to $/correct (D-067). The best entry per
  gold version is the highest macro mean; ties go to the lower $/correct.

The markdown is rendered from the JSON only, deterministically, so it never drifts from the data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hlmemo.bench import v2

RULES = [
    "(1) A change that sets a new best on one corpus must not lose more than 1 point on any other"
    " corpus's best.",
    "(2) A merge that drops any corpus below its best by more than 1 point needs a logged decision.",
    "(3) Every iteration records $/query (retrieval) or $/correct (librarian) next to its score: the"
    " production budget is set on price/performance after development (D-058).",
    "(4) Tuning uses corpus A and the Phase 5 project sets; corpus B's sealed subset is for confirmation"
    " only, never for tuning.",
    "(5) Librarian boards keep the per-task error rates next to $/correct (D-067); a gold change is a new"
    " gold version and every entry is re-scored under it (raw stays for history).",
]


def load(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": 1, "rules": RULES, "retrieval": {}, "librarian": {}}


def save(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- librarian
def librarian_entry(
    summary: dict[str, Any], meta: dict[str, Any], *, gold: str, run: str, source: str, commit: str | None
) -> dict[str, Any]:
    tasks = summary["tasks"]
    o = summary["overall"]
    return {
        "model_id": meta.get("model_id"),
        "reasoning": meta.get("reasoning"),
        "run": run,
        "reps": meta.get("reps"),
        "gold": gold,
        "score": summary["macro_mean"],
        "overall_mean": o["mean"],
        "correct_rate": o["correct_rate"],
        "macro_error_rate": summary["macro_error_rate"],
        "error_rates": {t: s["error_rate"] for t, s in tasks.items()},
        "task_means": {t: s["mean"] for t, s in tasks.items()},
        "metrics": summary["metrics"],
        "n": o["n"],
        "json_fail_first": o["json_fail_first"],
        "json_fail_final": o["json_fail_final"],
        "cost_usd": o["cost_usd"],
        "usd_per_correct": o["usd_per_correct"],
        "usd_per_month": summary["projection"]["usd_per_month"],
        "latency_p50_ms": o["latency_p50_ms"],
        "latency_p95_ms": o["latency_p95_ms"],
        "commit": commit,
        "prompt_version": meta.get("prompt_versions"),
        "schema_version": meta.get("schema_versions"),
        "harness": meta.get("harness", "hlm bench"),
        "source": source,
    }


def _entry_key(e: dict[str, Any]) -> tuple:
    return (e["model_id"], json.dumps(e.get("reasoning"), sort_keys=True), e["run"], e["gold"], e["source"])


def add_librarian(doc: dict[str, Any], board: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Upsert ``entry`` (same model/reasoning/run/gold/source replaces) and recompute the bests."""
    b = doc.setdefault("librarian", {}).setdefault(
        board, {"primary": "macro mean of per-task mean scores", "entries": [], "best": {}}
    )
    b["entries"] = [e for e in b["entries"] if _entry_key(e) != _entry_key(entry)] + [entry]
    b["entries"].sort(
        key=lambda e: (e["gold"], -(e["score"] or 0), e["usd_per_correct"] or 1e9, e["model_id"])
    )
    b["best"] = {}
    for e in b["entries"]:
        cur = b["best"].get(e["gold"])
        if cur is None:
            b["best"][e["gold"]] = {"model_id": e["model_id"], "run": e["run"], "score": e["score"]}
    return b


# --------------------------------------------------------------------------- retrieval
def retrieval_from_baseline(base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Seed entries from ``eval/baselines/phase0.json`` (the W-E Phase 0 baseline, D-057)."""
    common = {
        "system": base.get("system"),
        "commit": base.get("commit"),
        "embedder": f"{base['embedder']['id']}@{base['embedder']['revision'][:12]}",
        "librarian": None,
        "usd_per_query": 0.0,
        "source": "eval/baselines/phase0.json",
        "date": base.get("date"),
    }
    out: dict[str, dict[str, Any]] = {}
    a = base["corpus_a"]["runs"]["drill_top5"]["budgets"]["3000"]
    out["corpus_a"] = {
        "primary": "hit@5",
        "fixture_sha256": base["fixtures"]["corpus_a_questions_sha256"],
        "best": {
            **common,
            "config": "drill_top5, budget 3000",
            "score": a["overall"]["hit@5"],
            "secondary": {
                "mrr": a["overall"]["mrr"],
                "l2_key": a["overall"]["l2_key"],
                "l1_key": a["overall"]["l1_key"],
                "temporal_l2_stale_first": a["temporal"]["l2_stale_first"],
                "negative_auc": a["negative"]["auc_pos_gt_neg"],
                "tokens_query_mean": a["tokens"]["query_mean"],
            },
            "by_category_hit@5": {k: v.get("hit@5") for k, v in a["by_category"].items() if "hit@5" in v},
        },
    }
    b = base["corpus_b_dev"]["runs"]["drill_top5"]["budgets"]["3000"]
    out["corpus_b_dev"] = {
        "primary": "evidence Recall@5",
        "fixture_sha256": base["fixtures"]["corpus_b_dev_sha256"],
        "best": {
            **common,
            "config": "drill_top5, budget 3000",
            "score": b["overall"]["evidence_r5"],
            "secondary": {
                "hit@5": b["overall"]["hit@5"],
                "mrr": b["overall"]["mrr"],
                "l2_key": b["overall"]["l2_key"],
                "stale_claim_l1_top3": b["overall"]["stale_claim_l1_top3"],
                "tokens_query_mean": b["overall"]["tokens_query_mean"],
            },
            "by_category_evidence_r5": {
                k: v.get("evidence_r5") for k, v in b["by_category"].items() if "evidence_r5" in v
            },
        },
    }
    out["corpus_b_sealed"] = {
        "primary": "evidence Recall@5",
        "fixture_sha256": base["fixtures"]["corpus_b_sealed_sha256"],
        "sealed": True,
        "best": None,
        "note": "confirmation only (rule 4): scored once, in aggregate, at the final acceptance",
    }
    for board in out.values():
        board["history"] = [board["best"]] if board["best"] else []
    return out


def check_retrieval(doc: dict[str, Any], corpus: str, candidate: dict[str, float]) -> list[str]:
    """Rules (1)/(2): ``candidate`` maps corpus -> score of one configuration. Returns violations."""
    issues = []
    for name, board in doc.get("retrieval", {}).items():
        best = (board.get("best") or {}).get("score")
        if best is None or name not in candidate or name == corpus:
            continue
        if candidate[name] < best - 0.01:
            issues.append(f"{name}: {candidate[name]:.3f} is more than 1 point below its best {best:.3f}")
    return issues


# --------------------------------------------------------------------------- markdown
def _p(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}"


def _u(v: float | None, nd: int = 6) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def render_md(doc: dict[str, Any]) -> str:
    lines = [
        "# HLMemo leaderboard",
        "",
        "Rendered from `eval/results/leaderboard.json` by `hlm bench leaderboard` (do not edit by hand).",
        "",
        "## Rules (W-E optimize-to-best, D-058, D-067)",
        "",
        *[f"- {r}" for r in doc.get("rules", RULES)],
        "",
        "## Retrieval (best so far per corpus)",
        "",
        "| corpus | primary | best | config | commit | embedder | librarian | $/query | secondary |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, b in doc.get("retrieval", {}).items():
        best = b.get("best")
        if not best:
            lines.append(f"| {name} | {b['primary']} | sealed | - | - | - | - | - | {b.get('note', '')} |")
            continue
        sec = "; ".join(f"{k}={v}" for k, v in best.get("secondary", {}).items())
        lines.append(
            f"| {name} | {b['primary']} | {best['score']:.3f} | {best['config']} | {best['commit']}"
            f" | {best['embedder']} | {best['librarian'] or 'none'} | {best['usd_per_query']} | {sec} |"
        )
    for board, b in doc.get("librarian", {}).items():
        fams = (
            v2.FAMILIES
            if board == "bench_v2"
            else sorted({t for e in b["entries"] for t in e["error_rates"]})
        )
        lines += [
            "",
            f"## Librarian: {board} (score = {b['primary']}, %)",
            "",
            "Per-task error rate = share of calls scoring < 0.8 (D-067), next to $/correct.",
        ]
        for gold in sorted({e["gold"] for e in b["entries"]}, key=lambda g: (g != "raw", g)):
            lines += [
                "",
                f"### gold {gold}" + (" (D-066 labels)" if gold == "raw" else " (after the adjudication)"),
                "",
                "| model | run | reps | score | correct | "
                + " | ".join(f"err {f}" for f in fams)
                + " | JSON fail | $/correct | $/month | p50/p95 ms | commit | prompt |",
                "|---|---|---|---|---|" + "---|" * len(fams) + "---|---|---|---|---|---|",
            ]
            for e in [x for x in b["entries"] if x["gold"] == gold]:
                errs = " | ".join(_p(e["error_rates"].get(f)) for f in fams)
                lines.append(
                    f"| {e['model_id']} | {e['run']} | {e['reps']} | {_p(e['score'])} | {_p(e['correct_rate'])}"
                    f" | {errs} | {e['json_fail_first']}/{e['n']} | {_u(e['usd_per_correct'])}"
                    f" | {_u(e['usd_per_month'], 2)} | {e['latency_p50_ms']}/{e['latency_p95_ms']}"
                    f" | {e['commit']} | {e['prompt_version']} |"
                )
            best = b["best"].get(gold)
            if best:
                lines.append("")
                lines.append(f"Best ({gold}): `{best['model_id']}` ({best['run']}) {_p(best['score'])}.")
        metrics = [e for e in b["entries"] if e["gold"] != "raw"]
        if metrics:
            lines += ["", "Error metrics (adjusted gold):", ""]
            for e in metrics:
                lines.append(
                    f"- `{e['model_id']}` ({e['run']}): "
                    + ", ".join(f"{k}={v}" for k, v in e["metrics"].items())
                )
    return "\n".join(lines) + "\n"


__all__ = [
    "RULES",
    "add_librarian",
    "check_retrieval",
    "librarian_entry",
    "load",
    "render_md",
    "retrieval_from_baseline",
    "save",
]
