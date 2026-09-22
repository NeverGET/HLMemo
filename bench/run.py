#!/usr/bin/env python3
"""HLMemo librarian-model bench.

Runs the four librarian background jobs (placement, contradiction, summarization,
risk_check) against candidate models on OpenRouter and scores JSON reliability,
correctness and cost.

    python run.py --models deepseek-v4-flash,gemini-flash --runs 1

--models accepts aliases from models.json or raw OpenRouter ids.
Outputs bench/results/<timestamp>.json (raw, per call) and .md (summary table).
"""
from __future__ import annotations

import argparse
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")

API = "https://openrouter.ai/api/v1"
TASK_FILES = {
    "placement": "t1_placement.json",
    "contradiction": "t2_contradiction.json",
    "summarization": "t3_summarization.json",
    "risk_check": "t4_risk_check.json",
}
TASK_COLS = {"placement": "T1", "contradiction": "T2", "summarization": "T3", "risk_check": "T4"}

# Monthly cost model: 60 jobs/day, 12K prompt + 1.5K completion tokens per job.
JOBS_PER_DAY, DAYS, JOB_IN_TOKENS, JOB_OUT_TOKENS = 60, 30, 12_000, 1_500

# FIXED system prompt: identical bytes for every call of every task, so provider
# prompt caching applies. Task-specific data goes only into the user message.
SYSTEM_PROMPT = """You are the Librarian, a background worker of HLMemo, a long-term memory system for coding agents.
You receive one job per request. Content is mixed English / Turkish / German; never translate ids.
Answer with ONE JSON object only. No prose, no markdown, no code fences, no comments.

JOB "placement" -> {"layer": "fact|episode|lesson|experience", "topic_id": "<one of the candidate topic_ids>", "importance": <int 1-10>, "stability": "stable|volatile"}
  layer: fact = a standing truth about the user/project/environment (preferences, identity, config, current state).
         episode = a specific dated event or occurrence (what happened, when).
         lesson = a rule or warning distilled from a mistake, failure or surprise ("never do X because Y").
         experience = procedural know-how: a procedure or approach that worked ("how-to").
  importance: 1 = trivia, 5 = useful, 8 = must not be forgotten, 10 = safety/identity critical.
  stability: stable = unlikely to change; volatile = likely to change soon (temporary state, plans).

JOB "contradiction" -> {"contradicts": <bool>, "supersedes": "A|B|none", "reason": "<short>"}
  contradicts = true only if A and B cannot both be true at the same time. Refinements, additions and unrelated facts do not contradict.
  supersedes = the fact with the LATER t_valid when they contradict (that one replaces the other); "none" if no contradiction.

JOB "summarization" -> {"summary": "<= 120 words", "clue_ids": ["<id>", "<id>", "<id>"]}
  clue_ids = exactly the 3 most important items (decisions, deadlines, incidents, money, health, identity) by id. Exclude trivia.

JOB "risk_check" -> {"warn": <bool>, "matched_lesson_ids": ["<id>", ...], "message": "<short, may be empty>"}
  warn = true only if at least one past lesson clearly applies to the described task. matched_lesson_ids = ids of every lesson that applies; [] if none.
"""

FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$", re.S)
LAYERS = {"fact", "episode", "lesson", "experience"}


# ---------------------------------------------------------------- prompts ---

def build_user_message(task: str, case: dict) -> str:
    if task == "placement":
        payload = {"memory": case["memory"], "candidates": case["_candidates"]}
    elif task == "contradiction":
        payload = {"A": case["a"], "B": case["b"]}
    elif task == "summarization":
        payload = {"items": case["items"]}
    elif task == "risk_check":
        payload = {"task": case["task"], "lessons": case["lessons"]}
    else:
        raise ValueError(task)
    return f'JOB: {task}\nINPUT: {json.dumps(payload, ensure_ascii=False)}'


# ------------------------------------------------------- parse + validate ---

def parse_json(text: str):
    """Strip ```json fences and parse. Returns (obj|None, error|None)."""
    if text is None:
        return None, "empty response"
    cleaned = FENCE_RE.sub("", text.strip()).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"json decode: {e.msg} at {e.pos}"
    if not isinstance(obj, dict):
        return None, "top-level JSON is not an object"
    return obj, None


def validate(task: str, obj: dict, case: dict) -> str | None:
    """Return schema violation string or None if valid."""
    def need(k, typ):
        if k not in obj:
            return f"missing key {k}"
        if not isinstance(obj[k], typ) or (typ is int and isinstance(obj[k], bool)):
            return f"key {k} has type {type(obj[k]).__name__}"
        return None

    if task == "placement":
        for k, t in (("layer", str), ("topic_id", str), ("importance", int), ("stability", str)):
            if (e := need(k, t)):
                return e
        if obj["layer"] not in LAYERS:
            return f"layer not in enum: {obj['layer']!r}"
        if obj["stability"] not in {"stable", "volatile"}:
            return f"stability not in enum: {obj['stability']!r}"
        if not 1 <= obj["importance"] <= 10:
            return f"importance out of range: {obj['importance']}"
        if obj["topic_id"] not in case["candidates"]:
            return f"topic_id not a candidate: {obj['topic_id']!r}"
    elif task == "contradiction":
        for k, t in (("contradicts", bool), ("supersedes", str), ("reason", str)):
            if (e := need(k, t)):
                return e
        if obj["supersedes"] not in {"A", "B", "none"}:
            return f"supersedes not in enum: {obj['supersedes']!r}"
    elif task == "summarization":
        for k, t in (("summary", str), ("clue_ids", list)):
            if (e := need(k, t)):
                return e
        if not all(isinstance(x, str) for x in obj["clue_ids"]):
            return "clue_ids contains non-string"
        known = {i["id"] for i in case["items"]}
        if unknown := [x for x in obj["clue_ids"] if x not in known]:
            return f"clue_ids unknown: {unknown}"
    elif task == "risk_check":
        for k, t in (("warn", bool), ("matched_lesson_ids", list), ("message", str)):
            if (e := need(k, t)):
                return e
        if not all(isinstance(x, str) for x in obj["matched_lesson_ids"]):
            return "matched_lesson_ids contains non-string"
        known = {l["id"] for l in case["lessons"]}
        if unknown := [x for x in obj["matched_lesson_ids"] if x not in known]:
            return f"matched_lesson_ids unknown: {unknown}"
    return None


# ---------------------------------------------------------------- scoring ---

def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def score(task: str, obj: dict, case: dict, task_cfg: dict) -> tuple[float, dict]:
    g = case["gold"]
    if task == "placement":
        d = {
            "layer_ok": obj["layer"] == g["layer"],
            "topic_ok": obj["topic_id"] == g["topic_id"],
            "importance_ok": abs(obj["importance"] - g["importance"]) <= 2,
            "stability_ok": obj["stability"] == g["stability"],  # recorded, not scored
        }
        return float(d["layer_ok"] and d["topic_ok"] and d["importance_ok"]), d
    if task == "contradiction":
        d = {"contradicts_ok": obj["contradicts"] == g["contradicts"], "supersedes_ok": obj["supersedes"] == g["supersedes"]}
        return float(d["contradicts_ok"] and d["supersedes_ok"]), d
    if task == "summarization":
        words = len(obj["summary"].split())
        j = jaccard(obj["clue_ids"], g["clue_ids"])
        length_ok = words <= task_cfg.get("max_words", 120)
        return round(0.7 * j + 0.3 * float(length_ok), 4), {"jaccard": j, "words": words, "length_ok": length_ok}
    if task == "risk_check":
        warn_ok = obj["warn"] == g["warn"]
        j = jaccard(obj["matched_lesson_ids"], g["matched_lesson_ids"])
        return round(0.5 * float(warn_ok) + 0.5 * j, 4), {"warn_ok": warn_ok, "jaccard": j}
    raise ValueError(task)


# ------------------------------------------------------------ OpenRouter ---

class Client:
    def __init__(self, key: str, pricing: dict, reasoning: str, timeout: int):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/hlmemo/bench",
            "X-Title": "HLMemo librarian bench",
        })
        self.pricing = pricing      # model id -> {prompt, completion} USD per token
        self.reasoning = reasoning  # "low" | "off" | "default"
        self.timeout = timeout

    def price_for(self, model_id: str) -> dict | None:
        if model_id in self.pricing:
            return self.pricing[model_id]
        try:  # lazily fetch catalogue pricing for unknown ids
            r = self.s.get(f"{API}/models", timeout=60)
            for m in r.json().get("data", []):
                p = m.get("pricing", {})
                self.pricing[m["id"]] = {
                    "prompt": float(p.get("prompt") or 0),
                    "completion": float(p.get("completion") or 0),
                    "cache_read": float(p["input_cache_read"]) if p.get("input_cache_read") else None,
                    "supports_response_format": "response_format" in (m.get("supported_parameters") or []),
                }
        except Exception:
            pass
        return self.pricing.get(model_id)

    def chat(self, model_id: str, user_msg: str, use_rf: bool) -> dict:
        body = {
            "model": model_id,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
            "temperature": 0,
            "seed": 42,
            "max_tokens": 1024,
            "usage": {"include": True},
        }
        if use_rf:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning == "low":
            body["reasoning"] = {"effort": "low"}
        elif self.reasoning == "off":
            body["reasoning"] = {"enabled": False}

        rec = {"used_response_format": use_rf, "reasoning_param": body.get("reasoning"), "attempts": 0, "error": None}
        dropped_rf = dropped_reasoning = False
        for attempt in range(1, 6):  # max 5 tries
            rec["attempts"] = attempt
            t0 = time.perf_counter()
            try:
                r = self.s.post(f"{API}/chat/completions", json=body, timeout=self.timeout)
            except requests.RequestException as e:
                rec["error"] = f"transport: {e}"
                time.sleep(2 * attempt)
                continue
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            if r.status_code == 400:
                msg = r.text.lower()
                # Parameter-unsupported fallbacks: drop response_format, then reasoning.
                if "response_format" in body and ("response_format" in msg or "json" in msg) and not dropped_rf:
                    body.pop("response_format"); dropped_rf = True
                    rec["used_response_format"] = False; rec["rf_fallback_reason"] = r.text[:300]
                    continue
                if "reasoning" in body and "reasoning" in msg and not dropped_reasoning:
                    body.pop("reasoning"); dropped_reasoning = True
                    rec["reasoning_param"] = None; rec["reasoning_fallback_reason"] = r.text[:300]
                    continue
                rec["error"] = f"http 400: {r.text[:300]}"
                return rec
            if r.status_code in (408, 429, 500, 502, 503, 504):
                rec["error"] = f"http {r.status_code}: {r.text[:200]}"
                time.sleep(2 ** (attempt - 1) if r.status_code == 429 else 3 * attempt)  # 429: 1s,2s,4s,8s
                continue
            if r.status_code != 200:
                rec["error"] = f"http {r.status_code}: {r.text[:300]}"
                return rec
            data = r.json()
            if "error" in data and not data.get("choices"):
                rec["error"] = f"api error: {json.dumps(data['error'])[:300]}"
                time.sleep(3 * attempt)
                continue
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            usage = data.get("usage", {}) or {}
            # OpenRouter reports mid-stream upstream failures as finish_reason "error"
            # (0 tokens, truncated content). That is a provider fault, not a model JSON fault: retry.
            if choice.get("finish_reason") == "error" or choice.get("error"):
                rec["provider_errors"] = rec.get("provider_errors", 0) + 1
                rec["error"] = f"provider error: {json.dumps(choice.get('error') or data.get('error') or msg.get('content'))[:300]}"
                time.sleep(3 * attempt)
                continue
            rec.update({
                "error": None,
                "content": msg.get("content"),
                "finish_reason": choice.get("finish_reason"),
                "provider": data.get("provider"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                "reasoning_present": bool(msg.get("reasoning")) or bool(msg.get("reasoning_details")),
                "cost_usd": usage.get("cost"),
                "cost_source": "openrouter_usage" if usage.get("cost") is not None else None,
            })
            if rec["cost_usd"] is None:
                p = self.price_for(model_id)
                if p:
                    rec["cost_usd"] = (rec["prompt_tokens"] or 0) * p["prompt"] + (rec["completion_tokens"] or 0) * p["completion"]
                    rec["cost_source"] = "catalogue_pricing"
            return rec
        return rec


# ------------------------------------------------------------------ main ---

def load_models_json() -> dict:
    p = HERE / "models.json"
    return json.loads(p.read_text()) if p.exists() else {}


def resolve_model(name: str, models_json: dict) -> tuple[str, dict]:
    if name in models_json:
        return models_json[name]["id"], models_json[name]
    for alias, v in models_json.items():
        if v.get("id") == name:
            return name, v
    return name, {}


def load_tasks(only: set[str] | None) -> list[tuple[str, dict]]:
    out = []
    for task, fn in TASK_FILES.items():
        if only and task not in only:
            continue
        cfg = json.loads((HERE / "tasks" / fn).read_text())
        if task == "placement":  # expand candidate ids into {topic_id, summary}
            for c in cfg["cases"]:
                c["_candidates"] = [{"topic_id": t, "summary": cfg["topics"][t]} for t in c["candidates"]]
        out.append((task, cfg))
    return out


# Shared progress: every finished call lands here so a SIGTERM/SIGINT (or a crash) still
# yields a results file for whatever completed, and a checkpoint is written every 25 calls.
_PROGRESS = {"calls": [], "lock": threading.Lock(), "ctx": None, "checkpoint": None}


def _record(rec: dict):
    with _PROGRESS["lock"]:
        _PROGRESS["calls"].append(rec)
        n = len(_PROGRESS["calls"])
        if _PROGRESS["ctx"] and n % 25 == 0:
            args, models, models_json, client = _PROGRESS["ctx"]
            ck = HERE / "results" / "checkpoint.json"
            ck.write_text(json.dumps({"partial": True, "calls": _PROGRESS["calls"]}, ensure_ascii=False))
            _PROGRESS["checkpoint"] = ck


def _on_signal(signum, frame):
    print(f"\n!! signal {signum}: writing partial results for {len(_PROGRESS['calls'])} completed calls", flush=True)
    if _PROGRESS["ctx"]:
        args, models, models_json, client = _PROGRESS["ctx"]
        with _PROGRESS["lock"]:
            finish(args, models, models_json, client, list(_PROGRESS["calls"]), partial=True)
    os._exit(1)


class SpendGuard:
    def __init__(self, cap: float):
        self.cap, self.total, self.lock, self.stop = cap, 0.0, threading.Lock(), False

    def add(self, usd: float) -> bool:
        with self.lock:
            self.total += usd
            if self.cap and self.total > self.cap and not self.stop:
                self.stop = True
                print(f"!! max spend ${self.cap} exceeded (${self.total:.4f}); stopping all models.", flush=True)
            return self.stop


def run_model(name: str, args, models_json: dict, client: "Client", tasks, guard: SpendGuard) -> list[dict]:
    model_id, meta = resolve_model(name, models_json)
    p = client.price_for(model_id) or {}
    use_rf = p.get("supports_response_format", True)
    print(f"== {name} -> {model_id}  (response_format={'on' if use_rf else 'off'}, reasoning={args.reasoning})", flush=True)
    calls: list[dict] = []
    for task, cfg in tasks:
        cases = cfg["cases"][: args.limit] if args.limit else cfg["cases"]
        for run_i in range(args.runs):
            for case in cases:
                if guard.stop:
                    return calls
                user_msg = build_user_message(task, case)
                rec = {"model": name, "model_id": model_id, "task": task, "case_id": case["id"], "run": run_i,
                       "prompt_chars": len(SYSTEM_PROMPT) + len(user_msg)}
                if args.dry_run:
                    rec.update({"dry_run": True, "parse_ok": None, "score": None})
                    calls.append(rec)
                    continue
                resp = client.chat(model_id, user_msg, use_rf)
                rec.update(resp)
                rec["infra_error"] = bool(resp.get("error"))
                obj, perr = parse_json(resp.get("content")) if not resp.get("error") else (None, resp["error"])
                verr = validate(task, obj, case) if obj is not None else None
                rec["parse_ok"] = obj is not None
                rec["schema_ok"] = obj is not None and verr is None
                # json_fail counts only model output faults; HTTP/infra faults (429 etc. after retries) are infra_error
                rec["json_fail"] = not rec["infra_error"] and not (rec["parse_ok"] and rec["schema_ok"])
                rec["fail_reason"] = perr or verr
                rec["parsed"] = obj
                if rec["json_fail"] or rec["infra_error"]:
                    rec["score"], rec["detail"] = 0.0, {}
                else:
                    rec["score"], rec["detail"] = score(task, obj, case, cfg)
                calls.append(rec)
                _record(rec)
                flag = "ERR" if rec["infra_error"] else ("OK " if not rec["json_fail"] else "BAD")
                print(f"  [{name}] {flag} {TASK_COLS[task]} {case['id']} run{run_i} score={rec['score']:.2f} "
                      f"{rec.get('latency_ms', '?')}ms tok={rec.get('prompt_tokens')}/{rec.get('completion_tokens')}"
                      f"{' r=' + str(rec['reasoning_tokens']) if rec.get('reasoning_tokens') else ''} "
                      f"${(rec.get('cost_usd') or 0):.5f}" + (f"  <- {rec['fail_reason']}" if rec["json_fail"] else ""), flush=True)
                guard.add(rec.get("cost_usd") or 0.0)
    return calls


def run(args) -> Path:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and not args.dry_run:
        sys.exit("OPENROUTER_API_KEY not set (expected in ../.env)")
    models_json = load_models_json()
    pricing = {
        v["id"]: {
            "prompt": (v.get("prompt_usd_per_m") or 0) / 1e6,
            "completion": (v.get("completion_usd_per_m") or 0) / 1e6,
            "cache_read": (v["cache_read_usd_per_m"] / 1e6) if v.get("cache_read_usd_per_m") is not None else None,
            "supports_response_format": v.get("supports_response_format", True),
        }
        for v in models_json.values() if v.get("found")
    }
    client = Client(key or "", pricing, args.reasoning, args.timeout)
    tasks = load_tasks(set(args.tasks.split(",")) if args.tasks else None)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    guard = SpendGuard(args.max_spend)
    _PROGRESS["ctx"] = (args, models, models_json, client)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    reused: dict[str, list[dict]] = {}
    if args.reuse:
        prev = json.loads(Path(args.reuse).read_text())
        rerun = set(args.rerun.split(",")) if args.rerun else set()
        for c in prev["calls"]:
            if c["model"] in models and c["model"] not in rerun:
                reused.setdefault(c["model"], []).append(c)
        print(f"reusing {sum(len(v) for v in reused.values())} calls for {sorted(reused)} from {args.reuse}", flush=True)
    todo = [m for m in models if m not in reused]

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        per_model = dict(zip(todo, ex.map(lambda n: run_model(n, args, models_json, client, tasks, guard), todo)))
    calls = [c for m in models for c in (reused.get(m) or per_model.get(m) or [])]
    ck = HERE / "results" / "checkpoint.json"
    if ck.exists():
        ck.unlink()
    return finish(args, models, models_json, client, calls)


def finish(args, models, models_json, client, calls, partial: bool = False) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ("-partial" if partial else "")
    outdir = HERE / "results"
    outdir.mkdir(exist_ok=True)
    summary = summarize(models, models_json, client, calls)
    raw = {
        "timestamp": ts, "partial": partial, "args": vars(args), "system_prompt": SYSTEM_PROMPT,
        "cost_model": {"jobs_per_day": JOBS_PER_DAY, "days": DAYS, "in_tokens": JOB_IN_TOKENS, "out_tokens": JOB_OUT_TOKENS},
        "summary": summary, "calls": calls,
    }
    (outdir / f"{ts}.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    md = render_md(ts, args, summary)
    (outdir / f"{ts}.md").write_text(md)
    print("\n" + md)
    print(f"raw -> {outdir / (ts + '.json')}")
    return outdir / f"{ts}.md"


def summarize(models, models_json, client, calls) -> list[dict]:
    rows = []
    for name in models:
        model_id, meta = resolve_model(name, models_json)
        mc = [c for c in calls if c["model"] == name and not c.get("dry_run")]
        row = {"model": name, "model_id": model_id, "calls": len(mc)}
        for task, col in TASK_COLS.items():
            tc = [c for c in mc if c["task"] == task and not c.get("infra_error")]
            row[col] = round(sum(c["score"] for c in tc) / len(tc), 4) if tc else None
        # variance: |run0 score - run1 score| per case, averaged per task, then over tasks
        task_vars = []
        for task in TASK_COLS:
            by_case: dict[str, dict[int, float]] = {}
            for c in mc:
                if c["task"] == task:
                    by_case.setdefault(c["case_id"], {})[c["run"]] = c["score"]
            diffs = [abs(r[0] - r[1]) for r in by_case.values() if 0 in r and 1 in r]
            if diffs:
                task_vars.append(sum(diffs) / len(diffs))
        row["variance"] = round(sum(task_vars) / len(task_vars), 4) if task_vars else None
        row["infra_error"] = sum(1 for c in mc if c.get("infra_error"))
        scored = [c for c in mc if not c.get("infra_error")]
        row["json_fail"] = sum(1 for c in mc if c["json_fail"])
        row["json_fail_rate"] = round(row["json_fail"] / len(scored), 4) if scored else None
        fails = [c for c in mc if c["json_fail"]]
        row["json_fail_examples"] = [
            {"case": f"{TASK_COLS[c['task']]}/{c['case_id']}/run{c['run']}", "reason": c.get("fail_reason"),
             "raw": (c.get("content") or "")[:200]} for c in fails[:2]
        ]
        lat = [c["latency_ms"] for c in mc if c.get("latency_ms") is not None]
        row["avg_latency_ms"] = round(sum(lat) / len(lat)) if lat else None
        row["total_cost_usd"] = round(sum(c.get("cost_usd") or 0 for c in mc), 6)
        row["prompt_tokens"] = sum(c.get("prompt_tokens") or 0 for c in mc)
        row["completion_tokens"] = sum(c.get("completion_tokens") or 0 for c in mc)
        row["reasoning_tokens"] = sum(c.get("reasoning_tokens") or 0 for c in mc)
        row["cached_tokens"] = sum(c.get("cached_tokens") or 0 for c in mc)
        row["avg_completion_tokens"] = round(row["completion_tokens"] / len(mc)) if mc else None
        row["cache_hit_ratio"] = round(row["cached_tokens"] / row["prompt_tokens"], 4) if row["prompt_tokens"] else 0.0
        row["reasoning_honored"] = (
            "n/a" if not mc or mc[0].get("reasoning_param") is None
            else ("yes(0 reasoning tok)" if row["reasoning_tokens"] == 0 else f"partial({row['reasoning_tokens']} reasoning tok)")
        )
        row["response_format_used"] = all(c.get("used_response_format") for c in mc) if mc else None
        row["provider_errors_retried"] = sum(c.get("provider_errors") or 0 for c in mc)
        p = client.price_for(model_id) or {}
        if p:
            m_in = JOBS_PER_DAY * DAYS * JOB_IN_TOKENS
            m_out = JOBS_PER_DAY * DAYS * JOB_OUT_TOKENS
            row["est_monthly_usd"] = round(m_in * p["prompt"] + m_out * p["completion"], 2)
            if p.get("cache_read") is not None and row["cache_hit_ratio"] > 0:
                hit = row["cache_hit_ratio"]
                row["est_monthly_usd_cached"] = round(
                    m_in * (1 - hit) * p["prompt"] + m_in * hit * p["cache_read"] + m_out * p["completion"], 2)
            else:
                row["est_monthly_usd_cached"] = None  # no cache hits observed / no cache pricing
        else:
            row["est_monthly_usd"] = row["est_monthly_usd_cached"] = None
        rows.append(row)
    return rows


def fmt(v, pct=False):
    if v is None:
        return "-"
    return f"{v * 100:.0f}%" if pct else str(v)


def render_md(ts, args, summary) -> str:
    lines = [
        f"# Librarian bench {ts}" + (" (PARTIAL: interrupted, only completed calls)" if "partial" in ts else ""), "",
        f"models=`{args.models}` runs={args.runs} reasoning={args.reasoning} tasks={args.tasks or 'all'}"
        + (f" limit={args.limit}" if args.limit else "") + (f" reuse={Path(args.reuse).name} rerun={args.rerun}" if args.reuse else ""), "",
        f"Monthly estimate = {JOBS_PER_DAY} jobs/day x {DAYS} days x ({JOB_IN_TOKENS} in + {JOB_OUT_TOKENS} out tokens). "
        "'list' = catalogue price; 'cached' = same, with the OpenRouter-observed cache-hit ratio of this run billed at the model's cache-read price ('-' = no cache hits observed).", "",
        "| model | id | T1 | T2 | T3 | T4 | variance | JSON-fail | infra_error | avg latency | avg out tok (reasoning) | cache hit | total cost USD | monthly list | monthly cached | reasoning param |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary:
        lines.append(
            f"| {r['model']} | `{r['model_id']}` | {fmt(r['T1'], True)} | {fmt(r['T2'], True)} | {fmt(r['T3'], True)} | {fmt(r['T4'], True)} "
            f"| {fmt(r['variance'], True)} | {r['json_fail']}/{r['calls'] - r['infra_error']} ({fmt(r['json_fail_rate'], True)}) | {r['infra_error']} | {fmt(r['avg_latency_ms'])} ms "
            f"| {fmt(r['avg_completion_tokens'])} ({r['reasoning_tokens']}) | {fmt(r['cache_hit_ratio'], True)} "
            f"| {r['total_cost_usd']:.4f} | {fmt(r['est_monthly_usd'])} | {fmt(r['est_monthly_usd_cached'])} | {r['reasoning_honored']} |"
        )
    lines += ["",
              "T1 = exact layer+topic_id and |importance diff|<=2. T2 = exact contradicts+supersedes. "
              "T3 = 0.7*Jaccard(clue_ids) + 0.3*length<=120w. T4 = 0.5*warn exact + 0.5*Jaccard(ids). "
              "variance = |run1 - run2| per case, averaged per task then over T1-T4 (needs --runs 2). "
              "JSON-fail = unparseable after fence strip OR schema violation (scored 0). "
              "infra_error = HTTP 429/5xx/transport failure after 5 retries (1/2/4/8s backoff); excluded from task scores and JSON-fail rate. "
              "avg out tok = completion tokens per call incl. reasoning; (n) = total reasoning tokens reported by usage.",
              "", "## JSON-fail examples", ""]
    any_fail = False
    for r in summary:
        for ex in r["json_fail_examples"][:1]:
            any_fail = True
            raw = ex["raw"].replace("\n", "\\n").replace("|", "\\|")
            lines.append(f"- **{r['model']}** ({ex['case']}, {ex['reason']}): `{raw or '<empty>'}`")
    if not any_fail:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", required=True, help="comma-separated aliases from models.json or raw OpenRouter ids")
    ap.add_argument("--runs", type=int, default=1, help="repetitions per case")
    ap.add_argument("--tasks", default="", help="comma-separated subset: placement,contradiction,summarization,risk_check")
    ap.add_argument("--limit", type=int, default=0, help="only first N cases per task (smoke)")
    ap.add_argument("--reasoning", choices=["low", "off", "default"], default="low",
                    help="reasoning param sent to OpenRouter (low -> {effort:low}, off -> {enabled:false}, default -> none)")
    ap.add_argument("--max-spend", type=float, default=0.5, help="abort when accumulated cost exceeds this (USD); 0 = no cap")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--parallel", type=int, default=4, help="models evaluated concurrently (calls within a model stay sequential)")
    ap.add_argument("--dry-run", action="store_true", help="build prompts, no API calls")
    ap.add_argument("--reuse", default="", help="raw results .json: copy calls of models already in it instead of re-running them")
    ap.add_argument("--rerun", default="", help="with --reuse: comma-separated models to run again anyway")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
