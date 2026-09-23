#!/usr/bin/env python3
"""Real-data retrieval evaluation for an HLMemo project (read-only; no LLM).

For every question of a JSONL question file, calls `memory.query` at one or more token budgets and one
`memory.drilldown` round over the top clues, over MCP streamable HTTP (PHASE0-SPEC §3), and scores:

  * L1 (preflight view): does any answer key occur in the query response text (card + titles + previews)?
    Rank of the first gold source among the hit titles (hit@1/3/5/10, MRR).
  * L2 (after one drilldown of the top-k clues): does any answer key occur in the drilldown text
    (`l2_drill`) or in query ∪ drilldown (`l2`)? Tokens and latency of that round.
  * temporal (questions with `stale_answer`): does the latest key appear, and does a stale key appear
    ranked above it (stale-first)? Stale keys come from a `stale_keys` field (see --overlay) or are
    extracted heuristically from `stale_answer` (identifier-like tokens).
  * negative (questions without gold sources): evidence value and the top-1 score, compared with the
    top-1 score of the positives (best single threshold, AUC).
  * deep rank: one extra query at --deep-budget returns (nearly) the full deduped candidate list, so the
    true rank of the gold source is known even when it is outside the preflight window.

Question fields: id, category, lang, question, gold_sources (list of titles/paths; empty = negative),
answer_keys (verbatim strings), stale_answer (optional). A hit title matches a gold source on the part
before " § " (split files). --gold-alias PREFIX=REPLACEMENT rewrites gold paths whose importer title
differs (e.g. an extra root imported under a label).

Keys are matched case-insensitively after NFKC + casefold and whitespace collapsing.

Outputs in --out-dir: results.jsonl (one record per question), summary.json, summary.md.
Credentials: $HLM_DEVICE_TOKEN or <config-dir>/credentials.toml (never printed). Standard library only;
reuses the MCP client of import_corpus.py next to this file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_corpus import Mcp, ToolError, load_token  # noqa: E402

SECTION_SEP = " § "
HIT_KS = (1, 3, 5, 10)
_WS = re.compile(r"\s+")
_STALE_TOKEN = re.compile(r"[\w][\w./:{}+-]*[\w}]")


# --------------------------------------------------------------------------- text helpers


def norm(s: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", s).casefold()).strip()


def keys_in(text_norm: str, keys: list[str]) -> list[str]:
    return [k for k in keys if norm(k) and norm(k) in text_norm]


def stale_keys_for(q: dict[str, Any]) -> list[str]:
    if q.get("stale_keys"):
        return list(q["stale_keys"])
    sa = q.get("stale_answer") or ""
    out = []
    for tok in _STALE_TOKEN.findall(sa):
        if len(tok) >= 4 and re.search(r"[0-9/_.:]", tok):
            out.append(tok)
    return out


def source_of_title(title: str) -> str:
    return title.split(SECTION_SEP, 1)[0]


def apply_alias(path: str, aliases: list[tuple[str, str]]) -> str:
    for prefix, repl in aliases:
        if path.startswith(prefix):
            return repl + path[len(prefix) :]
    return path


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))
    return round(s[k], 1)


def rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


# --------------------------------------------------------------------------- one question


def hit_view(h: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "clue": h["clue"],
        "title": h["title"],
        "kind": h["kind"],
        "score": h["score"],
        "valid_from": h.get("valid_from"),
        "preview_chars": len(h.get("preview", "")),
    }


def query_text(res: dict[str, Any]) -> str:
    parts = []
    if res.get("card"):
        parts.append(res["card"].get("text", ""))
    for h in res.get("hits", []):
        parts.append(h.get("title", ""))
        parts.append(h.get("preview", ""))
    return "\n".join(parts)


def first_rank(units: list[str], keys: list[str]) -> int | None:
    for i, u in enumerate(units, start=1):
        if keys_in(norm(u), keys):
            return i
    return None


def temporal_view(units: list[str], latest: list[str], stale: list[str]) -> dict[str, Any]:
    lr, sr = first_rank(units, latest), first_rank(units, stale) if stale else None
    return {
        "latest_rank": lr,
        "stale_rank": sr,
        "latest_present": lr is not None,
        "stale_first": sr is not None and (lr is None or sr < lr),
    }


def evaluate_budget(
    mcp: Mcp,
    a: argparse.Namespace,
    q: dict[str, Any],
    gold: set[str],
    budget: int,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    keys = list(q.get("answer_keys") or [])
    t0 = time.perf_counter()
    res = mcp.call_retry(
        "memory.query",
        {"project": a.project, "query": q["question"], "token_budget": budget},
        errors,
        f"{q['id']} query b={budget}",
    )
    q_ms = (time.perf_counter() - t0) * 1000
    hits = res.get("hits", [])
    gold_rank = next((i for i, h in enumerate(hits, start=1) if source_of_title(h["title"]) in gold), None)
    qtext = norm(query_text(res))
    l1_keys = keys_in(qtext, keys)
    out: dict[str, Any] = {
        "budget": budget,
        "evidence": res.get("evidence"),
        "tokens_used": res["budget"]["used"],
        "latency_ms": round(q_ms, 1),
        "n_hits": len(hits),
        "omitted": res.get("omitted"),
        "card": bool(res.get("card")),
        "hits": [hit_view(h, i) for i, h in enumerate(hits, start=1)],
        "top1_score": hits[0]["score"] if hits else None,
        "gold_rank": gold_rank,
        "l1_keys": l1_keys,
        "l1": bool(l1_keys),
    }
    clues = [h["clue"] for h in hits[: a.drill_top]]
    drill_text, d_used, d_ms, d_trunc, items_units = "", 0, 0.0, False, []
    if clues:
        t0 = time.perf_counter()
        try:
            d = mcp.call_retry(
                "memory.drilldown",
                {"project": a.project, "clue_ids": clues, "token_budget": a.drill_budget},
                errors,
                f"{q['id']} drilldown b={budget}",
            )
            d_ms = (time.perf_counter() - t0) * 1000
            d_used = d["budget"]["used"]
            d_trunc = d.get("next_cursor") is not None
            items_units = [it.get("text", "") for it in d.get("items", [])]
            drill_text = "\n".join(items_units)
        except ToolError as exc:
            errors.append({"ctx": f"{q['id']} drilldown", "code": exc.code, "message": exc.message[:200]})
    dnorm = norm(drill_text)
    l2_drill = keys_in(dnorm, keys)
    out.update(
        {
            "drill_clues": clues,
            "drill_tokens": d_used,
            "drill_latency_ms": round(d_ms, 1),
            "drill_truncated": d_trunc,
            "l2_drill_keys": l2_drill,
            "l2_drill": bool(l2_drill),
            "l2": bool(l2_drill) or bool(l1_keys),
            "l2_all_keys": bool(keys) and set(l2_drill) | set(l1_keys) == set(keys),
        }
    )
    if q.get("stale_answer"):
        stale = stale_keys_for(q)
        hit_units = [h.get("title", "") + "\n" + h.get("preview", "") for h in hits]
        out["temporal"] = {
            "stale_keys_n": len(stale),
            "l1": temporal_view(hit_units, keys, stale),
            "l2": temporal_view(items_units, keys, stale),
        }
    return out


def evaluate(mcp: Mcp, a: argparse.Namespace, q: dict[str, Any], aliases, errors) -> dict[str, Any]:
    gold = {apply_alias(g, aliases) for g in (q.get("gold_sources") or [])}
    rec: dict[str, Any] = {
        "id": q["id"],
        "category": q.get("category"),
        "lang": q.get("lang"),
        "positive": bool(gold),
        "gold": sorted(gold),
        "n_keys": len(q.get("answer_keys") or []),
        "runs": [evaluate_budget(mcp, a, q, gold, b, errors) for b in a.budgets],
    }
    if a.deep_budget:
        res = mcp.call_retry(
            "memory.query",
            {"project": a.project, "query": q["question"], "token_budget": a.deep_budget},
            errors,
            f"{q['id']} deep",
        )
        hits = res.get("hits", [])
        ranks = {}
        for i, h in enumerate(hits, start=1):
            src = source_of_title(h["title"])
            if src in gold and src not in ranks:
                ranks[src] = {"rank": i, "clue": h["clue"], "score": h["score"]}
        rec["deep"] = {
            "n_hits": len(hits),
            "omitted": res.get("omitted"),
            "gold_ranks": ranks,
            "best_rank": min((r["rank"] for r in ranks.values()), default=None),
        }
    return rec


# --------------------------------------------------------------------------- scoring


def auc(neg: list[float], pos: list[float]) -> float | None:
    """P(score_pos > score_neg), ties count half."""
    if not neg or not pos:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def best_threshold(neg: list[float], pos: list[float]) -> dict[str, Any] | None:
    """Threshold t: predict 'no evidence' when top1 < t. Maximise balanced accuracy."""
    if not neg or not pos:
        return None
    best = None
    for t in sorted(set(neg + pos + [max(neg + pos) + 1e-6])):
        tnr = sum(n < t for n in neg) / len(neg)
        tpr = sum(p >= t for p in pos) / len(pos)
        bal = (tnr + tpr) / 2
        if best is None or bal > best["balanced_acc"]:
            best = {"threshold": t, "neg_rejected": round(tnr, 3), "pos_kept": round(tpr, 3)}
            best["balanced_acc"] = round(bal, 3)
    return best


def dist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}
    return {
        "n": len(values),
        "min": round(min(values), 6),
        "p50": round(statistics.median(values), 6),
        "max": round(max(values), 6),
    }


def group_metrics(recs: list[dict[str, Any]], bi: int) -> dict[str, Any]:
    pos = [r for r in recs if r["positive"]]
    runs = [r["runs"][bi] for r in pos]
    n = len(runs)
    m: dict[str, Any] = {"n": len(recs), "n_pos": n}
    if not n:
        return m
    for k in HIT_KS:
        m[f"hit@{k}"] = rate(sum(1 for x in runs if x["gold_rank"] and x["gold_rank"] <= k), n)
    m["mrr"] = round(sum(1 / x["gold_rank"] for x in runs if x["gold_rank"]) / n, 3)
    m["l1_key"] = rate(sum(x["l1"] for x in runs), n)
    m["l2_drill_key"] = rate(sum(x["l2_drill"] for x in runs), n)
    m["l2_key"] = rate(sum(x["l2"] for x in runs), n)
    m["l2_all_keys"] = rate(sum(bool(x["l2_all_keys"]) for x in runs), n)
    deep = [r.get("deep", {}).get("best_rank") for r in pos]
    if any(d is not None for d in deep):
        m["deep_found"] = rate(sum(d is not None for d in deep), n)
        m["deep_hit@20"] = rate(sum(1 for d in deep if d and d <= 20), n)
    return m


def summarize(recs: list[dict[str, Any]], budgets: list[int]) -> dict[str, Any]:
    out: dict[str, Any] = {"n_questions": len(recs), "budgets": {}}
    cats = sorted({r["category"] for r in recs})
    langs = sorted({r["lang"] for r in recs})
    for bi, b in enumerate(budgets):
        runs = [r["runs"][bi] for r in recs]
        s: dict[str, Any] = {
            "overall": group_metrics(recs, bi),
            "by_category": {c: group_metrics([r for r in recs if r["category"] == c], bi) for c in cats},
            "by_lang": {g: group_metrics([r for r in recs if r["lang"] == g], bi) for g in langs},
            "latency_ms": {
                "query_p50": pct([x["latency_ms"] for x in runs], 50),
                "query_p95": pct([x["latency_ms"] for x in runs], 95),
                "drill_p50": pct([x["drill_latency_ms"] for x in runs if x["drill_clues"]], 50),
                "drill_p95": pct([x["drill_latency_ms"] for x in runs if x["drill_clues"]], 95),
            },
            "tokens": {
                "query_mean": round(statistics.mean(x["tokens_used"] for x in runs), 1),
                "drill_mean": round(statistics.mean(x["drill_tokens"] for x in runs), 1),
                "hits_mean": round(statistics.mean(x["n_hits"] for x in runs), 1),
                "drill_truncated": sum(x["drill_truncated"] for x in runs),
            },
        }
        temporal = [r["runs"][bi]["temporal"] for r in recs if "temporal" in r["runs"][bi]]
        if temporal:
            s["temporal"] = {
                "n": len(temporal),
                **{
                    f"{lv}_{k}": rate(sum(t[lv][k] for t in temporal), len(temporal))
                    for lv in ("l1", "l2")
                    for k in ("latest_present", "stale_first")
                },
            }
        neg = [r["runs"][bi]["top1_score"] or 0.0 for r in recs if not r["positive"]]
        pos = [r["runs"][bi]["top1_score"] or 0.0 for r in recs if r["positive"]]
        s["negative"] = {
            "evidence": dict(
                sorted(
                    {
                        e: sum(1 for r in recs if not r["positive"] and r["runs"][bi]["evidence"] == e)
                        for e in {r["runs"][bi]["evidence"] for r in recs}
                    }.items()
                )
            ),
            "top1_neg": dist(neg),
            "top1_pos": dist(pos),
            "auc_pos_gt_neg": auc(neg, pos),
            "best_threshold": best_threshold(neg, pos),
        }
        out["budgets"][str(b)] = s
    return out


def md_table(rows: dict[str, dict[str, Any]], cols: list[str]) -> list[str]:
    lines = ["| group | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for name, m in rows.items():
        lines.append(f"| {name} | " + " | ".join(str(m.get(c, "")) for c in cols) + " |")
    return lines


def render_md(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    cols = ["n_pos", "hit@1", "hit@3", "hit@5", "hit@10", "mrr", "l1_key", "l2_key", "l2_drill_key"]
    cols += ["deep_hit@20"]
    lines = [f"# Real-data retrieval eval — project `{meta['project']}`", ""]
    lines.append(f"Questions: {summary['n_questions']}. Drilldown: top-{meta['drill_top']} clues at ")
    lines[-1] += f"budget {meta['drill_budget']}. Run at {meta['started']}."
    for b, s in summary["budgets"].items():
        lines += ["", f"## Budget {b}", ""]
        rows = {"overall": s["overall"]}
        rows.update({f"cat:{k}": v for k, v in s["by_category"].items()})
        rows.update({f"lang:{k}": v for k, v in s["by_lang"].items()})
        lines += md_table(rows, cols)
        lines += ["", f"Latency ms: `{json.dumps(s['latency_ms'])}`", f"Tokens: `{json.dumps(s['tokens'])}`"]
        if "temporal" in s:
            lines.append(f"Temporal: `{json.dumps(s['temporal'])}`")
        lines.append(f"Negative: `{json.dumps(s['negative'])}`")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument("--server", required=True, help="base URL, e.g. http://127.0.0.1:8765")
    ap.add_argument("--project", required=True)
    ap.add_argument("--config-dir", type=Path, default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--budgets", default="1500,3000", help="comma-separated memory.query budgets")
    ap.add_argument("--drill-top", type=int, default=3)
    ap.add_argument("--drill-budget", type=int, default=4000)
    ap.add_argument("--deep-budget", type=int, default=32000, help="0 disables the deep-rank query")
    ap.add_argument("--gold-alias", action="append", default=[], help="PREFIX=REPLACEMENT")
    ap.add_argument("--overlay", type=Path, default=None, help="JSON {id: {extra fields}} merged in")
    ap.add_argument("--only", default=None, help="comma-separated question ids")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args(argv)
    a.budgets = [int(x) for x in a.budgets.split(",") if x.strip()]
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    questions = load_jsonl(a.questions)
    if a.overlay:
        extra = json.loads(a.overlay.read_text())
        for q in questions:
            q.update(extra.get(q["id"], {}))
    if a.only:
        wanted = set(a.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    aliases = [tuple(x.split("=", 1)) for x in a.gold_alias]
    token = load_token(
        a.config_dir or (Path(os.environ["HLM_CONFIG_DIR"]) if "HLM_CONFIG_DIR" in os.environ else None),
        a.server,
        a.device,
    )
    mcp = Mcp(a.server, token, a.timeout)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    recs = []
    with (a.out_dir / "results.jsonl").open("w") as fh:
        for i, q in enumerate(questions, start=1):
            rec = evaluate(mcp, a, q, aliases, errors)
            recs.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            r = rec["runs"][-1]
            print(
                f"[{i}/{len(questions)}] {q['id']} rank={r['gold_rank']} l1={int(r['l1'])} "
                f"l2={int(r['l2'])} {r['latency_ms']:.0f}ms",
                file=sys.stderr,
            )
    summary = summarize(recs, a.budgets)
    meta = {
        "project": a.project,
        "drill_top": a.drill_top,
        "drill_budget": a.drill_budget,
        "deep_budget": a.deep_budget,
        "started": started,
        "errors": errors,
    }
    (a.out_dir / "summary.json").write_text(json.dumps({"meta": meta, **summary}, indent=1) + "\n")
    (a.out_dir / "summary.md").write_text(render_md(summary, meta))
    print(json.dumps(summary["budgets"][str(a.budgets[-1])]["overall"]), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
