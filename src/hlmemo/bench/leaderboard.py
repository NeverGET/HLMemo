"""The W-E leaderboard (``eval/results/leaderboard.json`` → ``LEADERBOARD.md``), append-only.

Two kinds of boards:

* **retrieval** (one per corpus: A, B dev, B sealed, later each Phase 5 project set). A board holds
  the full iteration ``history`` and a pointer to the ``best`` iteration. ``seed_baseline`` only creates
  boards that do not exist yet; after that the only way in is ``add_retrieval_candidate``, which
  appends one iteration per reported corpus and enforces the W-E rules (below). Nothing is rewritten.
* **librarian** (bench suites): one entry per (model run × gold version), with the per-task error
  rates next to $/correct (D-067), the config hash, prompt/schema versions, commit and the sha256 of
  every pack scored. An entry is **ranked** only when it is complete: the board's reference pack set
  (hash for hash) was covered case × rep with no infra error, no ``--max-usd`` stop, no interrupted
  (partial) run, no ``--limit`` and no task subset. Incomplete entries are recorded with the reason
  and never become best.

W-E rules (roadmap W-E, D-058, D-067) as enforced by ``add_retrieval_candidate``:

1. a candidate that sets a new best on one corpus must report every other ranked corpus and must not
   be more than 1 point below its best there (overridable only with a logged ``decision``);
2. with ``merge=True`` (the configuration is being merged), no reported corpus may drop more than 1
   point below its best (overridable only with a logged ``decision``);
3. every iteration records its cost (``usd_per_query``); missing cost is refused;
4. the sealed corpus is confirmation only: a sealed score is accepted only with ``confirmation=True``.

Data errors are never overridable: an unknown corpus, a fixture sha256 that differs from the board's
(a different question set is not comparable), a missing cost, a malformed decision id.

Retrieval candidate file (``hlm bench leaderboard add-candidate --retrieval FILE``)::

    {"label": "...", "commit": "abc1234", "date": "2026-09-24",
     "config": {...} | "config_hash": "...", "prompt_versions": ..., "schema_versions": ...,
     "model_id": null, "embedder": "...", "usd_per_query": 0.0,
     "fixtures_sha256": {"corpus_a": "<sha256>", "corpus_b_dev": "<sha256>"},
     "scores": {"corpus_a": 0.80, "corpus_b_dev": 0.46},
     "secondary": {"corpus_a": {...}}, "complete": true}
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from hlmemo.bench import v2

SCHEMA = 2
POINT = 0.01  # 1 point on a 0..1 score
EPS = 1e-9
DECISION_RE = re.compile(r"^D-\d{3,}[a-z]?$")
SCORED = ("ok", "json_fail")

RULES = [
    "(1) A change that sets a new best on one corpus must not lose more than 1 point on any other"
    " corpus's best (it must report every ranked corpus); otherwise refused unless a decision is logged.",
    "(2) A merge that drops any corpus below its best by more than 1 point needs a logged decision.",
    "(3) Every iteration records $/query (retrieval) or $/correct (librarian) next to its score: the"
    " production budget is set on price/performance after development (D-058).",
    "(4) Tuning uses corpus A and the Phase 5 project sets; corpus B's sealed subset is for confirmation"
    " only, never for tuning.",
    "(5) History is append-only. Librarian boards keep the per-task error rates next to $/correct (D-067);"
    " only complete runs (full reference pack set by sha256, every case x rep, no infra/cap/partial) are"
    " ranked; a gold change is a new gold version and entries are re-scored under it (raw stays).",
]


class LeaderboardError(ValueError):
    """A refused change: history is append-only and the W-E rules hold."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load(path: Path) -> dict[str, Any]:
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("schema") != SCHEMA:
            raise LeaderboardError([f"{path}: schema {doc.get('schema')} != {SCHEMA}"])
        return doc
    return {"schema": SCHEMA, "rules": RULES, "retrieval": {}, "librarian": {}}


def save(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- retrieval
def baseline_boards(base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Boards + iteration 1 from ``eval/baselines/phase0.json`` (the W-E Phase 0 baseline, D-057)."""
    config = {"system": base.get("system"), "drill_top": 5, "budget": 3000, "librarian": None}
    common = {
        "id": 1,
        "label": "Phase 0 baseline (D-057)",
        "date": base.get("date"),
        "commit": base.get("commit"),
        "config": config,
        "config_hash": _hash(config),
        "prompt_versions": None,
        "schema_versions": None,
        "model_id": None,
        "embedder": f"{base['embedder']['id']}@{base['embedder']['revision'][:12]}",
        "usd_per_query": 0.0,
        "decision": None,
        "merge": False,
        "complete": True,
        "source": "eval/baselines/phase0.json",
    }
    a = base["corpus_a"]["runs"]["drill_top5"]["budgets"]["3000"]
    b = base["corpus_b_dev"]["runs"]["drill_top5"]["budgets"]["3000"]
    boards = {
        "corpus_a": {
            "primary": "hit@5",
            "fixture_sha256": base["fixtures"]["corpus_a_questions_sha256"],
            "first": {
                "score": a["overall"]["hit@5"],
                "secondary": {
                    "mrr": a["overall"]["mrr"],
                    "l2_key": a["overall"]["l2_key"],
                    "l1_key": a["overall"]["l1_key"],
                    "temporal_l2_stale_first": a["temporal"]["l2_stale_first"],
                    "negative_auc": a["negative"]["auc_pos_gt_neg"],
                    "tokens_query_mean": a["tokens"]["query_mean"],
                },
            },
        },
        "corpus_b_dev": {
            "primary": "evidence Recall@5",
            "fixture_sha256": base["fixtures"]["corpus_b_dev_sha256"],
            "first": {
                "score": b["overall"]["evidence_r5"],
                "secondary": {
                    "hit@5": b["overall"]["hit@5"],
                    "mrr": b["overall"]["mrr"],
                    "l2_key": b["overall"]["l2_key"],
                    "stale_claim_l1_top3": b["overall"]["stale_claim_l1_top3"],
                    "tokens_query_mean": b["overall"]["tokens_query_mean"],
                },
            },
        },
        "corpus_b_sealed": {
            "primary": "evidence Recall@5",
            "fixture_sha256": base["fixtures"]["corpus_b_sealed_sha256"],
            "sealed": True,
            "note": "confirmation only (rule 4): scored once, in aggregate, at the final acceptance",
            "first": None,
        },
    }
    out = {}
    for name, bd in boards.items():
        first = bd.pop("first")
        it = (
            None
            if first is None
            else {**common, "corpus": name, **first, "fixture_sha256": bd["fixture_sha256"]}
        )
        out[name] = {**bd, "history": [it] if it else [], "best": 1 if it else None}
    return out


def seed_baseline(doc: dict[str, Any], base: dict[str, Any]) -> list[str]:
    """Create the retrieval boards. Refused when any of them exists (history is never rewritten)."""
    boards = baseline_boards(base)
    exists = sorted(set(boards) & set(doc.setdefault("retrieval", {})))
    if exists:
        raise LeaderboardError(
            [f"retrieval board(s) {exists} already exist: history is append-only, use add-candidate"]
        )
    doc["retrieval"].update(boards)
    return sorted(boards)


def best_of(board: dict[str, Any]) -> dict[str, Any] | None:
    bid = board.get("best")
    return next((it for it in board.get("history", []) if it["id"] == bid), None) if bid else None


def rule_violations(
    doc: dict[str, Any], cand: dict[str, Any], *, merge: bool, confirmation: bool
) -> tuple[list[str], list[str]]:
    """(fatal data errors, W-E rule violations that a logged decision may override)."""
    fatal: list[str] = []
    rules: list[str] = []
    boards = doc.get("retrieval", {})
    scores: dict[str, float] = cand.get("scores") or {}
    if not scores:
        fatal.append("candidate has no scores")
    for key in ("label", "commit"):
        if not cand.get(key):
            fatal.append(f"candidate has no {key}")
    if "config" not in cand and "config_hash" not in cand:
        fatal.append("candidate has no config / config_hash")
    if cand.get("usd_per_query") is None:
        fatal.append("rule 3: the candidate records no usd_per_query")
    for c in scores:
        b = boards.get(c)
        if b is None:
            fatal.append(f"unknown corpus {c!r} (seed its board first)")
            continue
        got = (cand.get("fixtures_sha256") or {}).get(c)
        if got != b.get("fixture_sha256"):
            fatal.append(f"{c}: fixture sha256 {str(got)[:12]} != board {str(b.get('fixture_sha256'))[:12]}")
        if b.get("sealed") and not confirmation:
            fatal.append(f"rule 4: {c} is sealed (confirmation only); pass confirmation to record it")
    bests = {
        c: best_of(b)["score"] for c, b in boards.items() if not b.get("sealed") and best_of(b) is not None
    }
    new_best = sorted(c for c in scores if c in bests and scores[c] > bests[c] + EPS)
    if new_best:
        for c, best in sorted(bests.items()):
            if c in new_best:
                continue
            if c not in scores:
                rules.append(f"rule 1: new best on {new_best} but no score reported for {c}")
            elif scores[c] < best - POINT - EPS:
                rules.append(
                    f"rule 1: new best on {new_best} but {c} drops to {scores[c]:.3f} (best {best:.3f})"
                )
    if merge:
        for c, best in sorted(bests.items()):
            if c in scores and scores[c] < best - POINT - EPS:
                rules.append(f"rule 2: merge drops {c} to {scores[c]:.3f} (best {best:.3f})")
    return fatal, rules


def add_retrieval_candidate(
    doc: dict[str, Any],
    cand: dict[str, Any],
    *,
    decision: str | None = None,
    merge: bool = False,
    confirmation: bool = False,
    source: str | None = None,
) -> list[str]:
    """Append one iteration to every reported corpus (or raise ``LeaderboardError``). Returns the
    corpora whose best moved."""
    if decision is not None and not DECISION_RE.match(decision):
        raise LeaderboardError([f"decision id {decision!r} is not of the form D-xxx"])
    fatal, rules = rule_violations(doc, cand, merge=merge, confirmation=confirmation)
    if fatal:
        raise LeaderboardError(fatal)
    if rules and decision is None:
        raise LeaderboardError([*rules, "refused: pass --decision D-xxx once the exception is logged"])
    config_hash = cand.get("config_hash") or _hash(cand["config"])
    moved = []
    for c, score in sorted(cand["scores"].items()):
        board = doc["retrieval"][c]
        it = {
            "id": max((x["id"] for x in board["history"]), default=0) + 1,
            "corpus": c,
            "label": cand["label"],
            "date": cand.get("date"),
            "commit": cand["commit"],
            "config": cand.get("config"),
            "config_hash": config_hash,
            "prompt_versions": cand.get("prompt_versions"),
            "schema_versions": cand.get("schema_versions"),
            "model_id": cand.get("model_id"),
            "embedder": cand.get("embedder"),
            "usd_per_query": cand["usd_per_query"],
            "fixture_sha256": cand["fixtures_sha256"][c],
            "score": score,
            "secondary": (cand.get("secondary") or {}).get(c, {}),
            "decision": decision,
            "rule_exceptions": rules,
            "merge": merge,
            "confirmation": confirmation,
            "complete": bool(cand.get("complete", True)),
            "source": source,
        }
        board["history"].append(it)
        cur = best_of(board)
        if it["complete"] and (cur is None or score > cur["score"] + EPS):
            board["best"] = it["id"]
            moved.append(c)
    return moved


# --------------------------------------------------------------------------- librarian
def pack_hashes(paths: list[Path]) -> dict[str, str]:
    return {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def coverage(
    rows: list[dict[str, Any]], packs: list[tuple[str, dict]], reps: int, meta: dict[str, Any]
) -> list[str]:
    """Reasons a run is incomplete (empty = complete) against the loaded (gold-applied) packs."""
    reasons = []
    if meta.get("aborted"):
        reasons.append("interrupted (partial run or --max-usd stop)")
    if meta.get("limit"):
        reasons.append(f"--limit {meta['limit']}")
    if meta.get("tasks_subset"):
        reasons.append(f"task subset {meta['tasks_subset']}")
    want = {(fam, c["id"], rep) for fam, p in packs for c in p["cases"] for rep in range(max(1, reps or 1))}
    have = {(r["task"], r["case_id"], int(r["rep"])) for r in rows if r["status"] in SCORED}
    missing = want - have
    if missing:
        reasons.append(f"{len(missing)} of {len(want)} case x rep results missing")
    bad = [r["status"] for r in rows if r["status"] not in SCORED]
    for status in sorted(set(bad)):
        reasons.append(f"{bad.count(status)} {status}")
    return reasons


def librarian_entry(
    summary: dict[str, Any],
    meta: dict[str, Any],
    *,
    gold: str,
    run: str,
    source: str,
    commit: str | None,
    packs: dict[str, str],
    incomplete: list[str],
) -> dict[str, Any]:
    tasks = summary["tasks"]
    o = summary["overall"]
    return {
        "model_id": meta.get("model_id"),
        "reasoning": meta.get("reasoning"),
        "run": run,
        "reps": meta.get("reps"),
        "gold": gold,
        "complete": not incomplete,
        "incomplete": incomplete,
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
        "config_hash": meta.get("config_hash"),
        "config": meta.get("config"),
        "prompt_version": meta.get("prompt_versions"),
        "schema_version": meta.get("schema_versions"),
        "packs_sha256": packs,
        "harness": meta.get("harness", "hlm bench"),
        "budget": meta.get("budget"),
        "source": source,
    }


def _entry_key(e: dict[str, Any]) -> tuple:
    return (e["model_id"], e["run"], e["gold"], e["source"], e.get("config_hash"))


def add_librarian(doc: dict[str, Any], board: str, entry: dict[str, Any]) -> bool:
    """Append ``entry`` (idempotent for an identical entry; a changed entry with the same key is
    refused). The board's reference pack set is fixed by its first entry; an entry scored on other
    pack hashes is incomplete. Returns True when appended."""
    b = doc.setdefault("librarian", {}).setdefault(
        board,
        {
            "primary": "macro mean of per-task mean scores",
            "reference_packs": dict(entry["packs_sha256"]),
            "entries": [],
            "best": {},
        },
    )
    if entry["packs_sha256"] != b["reference_packs"]:
        diff = sorted(set(entry["packs_sha256"].items()) ^ set(b["reference_packs"].items()))
        entry = {
            **entry,
            "complete": False,
            "incomplete": [*entry["incomplete"], f"pack set differs: {diff[:4]}"],
        }
    for e in b["entries"]:
        if _entry_key(e) == _entry_key(entry):
            if e == entry:
                return False
            raise LeaderboardError([f"entry {_entry_key(entry)} exists with other content (append-only)"])
    b["entries"].append(entry)
    b["best"] = {}
    for g in sorted({e["gold"] for e in b["entries"]}):
        ranked = [e for e in b["entries"] if e["gold"] == g and e["complete"] and e["score"] is not None]
        if ranked:
            top = min(
                ranked, key=lambda e: (-e["score"], e["usd_per_correct"] or 1e9, e["model_id"], e["run"])
            )
            b["best"][g] = {"model_id": top["model_id"], "run": top["run"], "score": top["score"]}
    return True


# --------------------------------------------------------------------------- markdown
def _p(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}"


def _u(v: float | None, nd: int = 6) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def render_md(doc: dict[str, Any]) -> str:
    lines = [
        "# HLMemo leaderboard",
        "",
        "Rendered from `eval/results/leaderboard.json` by `hlm bench leaderboard` (append-only; do not edit"
        " by hand).",
        "",
        "## Rules (W-E optimize-to-best, D-058, D-067)",
        "",
        *[f"- {r}" for r in doc.get("rules", RULES)],
        "",
        "## Retrieval (best so far per corpus)",
        "",
        "| corpus | primary | best | iteration | config | commit | embedder | $/query | fixture"
        " | secondary |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, b in doc.get("retrieval", {}).items():
        best = best_of(b)
        if not best:
            lines.append(
                f"| {name} | {b['primary']} | {'sealed' if b.get('sealed') else '-'} | - | - | - | - | - |"
                f" {b['fixture_sha256'][:12]} | {b.get('note', '')} |"
            )
            continue
        sec = "; ".join(f"{k}={v}" for k, v in best.get("secondary", {}).items())
        lines.append(
            f"| {name} | {b['primary']} | {best['score']:.3f} | #{best['id']} {best['label']}"
            f" | {best['config_hash']}"
            f" | {best['commit']} | {best['embedder']} | {best['usd_per_query']}"
            f" | {b['fixture_sha256'][:12]} | {sec} |"
        )
    hist = [(name, it) for name, b in doc.get("retrieval", {}).items() for it in b.get("history", [])]
    if hist:
        lines += [
            "",
            "History (append-only):",
            "",
            "| corpus | # | date | label | commit | config | score | $/query | decision | merge | complete |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for name, it in hist:
            lines.append(
                f"| {name} | {it['id']} | {it['date']} | {it['label']} | {it['commit']} | {it['config_hash']}"
                f" | {it['score']:.3f} | {it['usd_per_query']} | {it['decision'] or '-'} | {it['merge']}"
                f" | {it['complete']} |"
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
            "Per-task error rate = share of calls scoring < 0.8 (D-067), next to $/correct. Reference packs: "
            + ", ".join(f"{k} {v[:8]}" for k, v in sorted(b["reference_packs"].items())),
        ]
        for gold in sorted({e["gold"] for e in b["entries"]}, key=lambda g: (g != "raw", g)):
            ranked = [e for e in b["entries"] if e["gold"] == gold and e["complete"]]
            ranked.sort(
                key=lambda e: (-(e["score"] or 0), e["usd_per_correct"] or 1e9, e["model_id"], e["run"])
            )
            lines += [
                "",
                f"### gold {gold}" + (" (D-066 labels)" if gold == "raw" else " (after the adjudication)"),
                "",
                "| model | run | reps | score | correct | "
                + " | ".join(f"err {f}" for f in fams)
                + " | JSON fail | $/correct | $/month | p50/p95 ms | commit | config | prompt |",
                "|---|---|---|---|---|" + "---|" * len(fams) + "---|---|---|---|---|---|---|",
            ]
            for e in ranked:
                errs = " | ".join(_p(e["error_rates"].get(f)) for f in fams)
                lines.append(
                    f"| {e['model_id']} | {e['run']} | {e['reps']} | {_p(e['score'])}"
                    f" | {_p(e['correct_rate'])}"
                    f" | {errs} | {e['json_fail_first']}/{e['n']} | {_u(e['usd_per_correct'])}"
                    f" | {_u(e['usd_per_month'], 2)} | {e['latency_p50_ms']}/{e['latency_p95_ms']}"
                    f" | {e['commit']} | {e['config_hash']} | {e['prompt_version']} |"
                )
            best = b["best"].get(gold)
            if best:
                lines += ["", f"Best ({gold}): `{best['model_id']}` ({best['run']}) {_p(best['score'])}."]
            unranked = [e for e in b["entries"] if e["gold"] == gold and not e["complete"]]
            if unranked:
                lines += ["", "Not ranked (incomplete):", ""]
                for e in unranked:
                    lines.append(
                        f"- `{e['model_id']}` ({e['run']}): {_p(e['score'])} — " + "; ".join(e["incomplete"])
                    )
        adjusted = [e for e in b["entries"] if e["gold"] != "raw"]
        if adjusted:
            lines += ["", "Error metrics (adjusted gold):", ""]
            for e in adjusted:
                lines.append(
                    f"- `{e['model_id']}` ({e['run']}): "
                    + ", ".join(f"{k}={v}" for k, v in e["metrics"].items())
                )
    return "\n".join(lines) + "\n"


__all__ = [
    "RULES",
    "LeaderboardError",
    "add_librarian",
    "add_retrieval_candidate",
    "baseline_boards",
    "best_of",
    "coverage",
    "librarian_entry",
    "load",
    "pack_hashes",
    "render_md",
    "rule_violations",
    "save",
    "seed_baseline",
]
