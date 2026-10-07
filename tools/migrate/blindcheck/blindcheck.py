#!/usr/bin/env python3
"""Blind check of a migrated project's memory (TEMPLATE §7, D-216 method), generic over the project.

    blindcheck.py extract --dir W --truthset T.jsonl --sha256 SEAL --project SLUG
    blindcheck.py ask     --dir W [--workers 2] [--cap 1.00] [--model M]
    blindcheck.py packets --dir W --truthset T.jsonl --sha256 SEAL [--seed N]
    (run_graders.sh W <grader-work-root>)
    blindcheck.py score   --dir W [--bar 0.80]

W is a PRIVATE work dir (0700). The steps are separate for isolation:
- `extract` verifies the truth set's sealed sha256 and writes ONLY qid/project/question to W/questions.jsonl.
- `ask` reads only questions.jsonl. Each question is asked ONCE through `memory.ask` on the user's
  configured `hlm` MCP server, by a headless `claude -p` relay. The raw tool_result is saved BY CODE from
  the stream-json output (never retyped by a model, D-216), and the relay's tool input must equal the
  question verbatim. A failure is a finding, not a retry.
  Relay isolation: `--restricted` (no user, project or local settings, so no hooks such as the SessionStart
  brief and no plugins; no code-running tools); only ToolSearch and the memory_ask tool are allowed; an empty
  scratch cwd (no CLAUDE.md, an unmapped folder); HLM_CAPTURE=off and HLM_BRIEF_DIGEST=off. MCP servers from
  the user's configuration stay loaded (the relay needs `hlm`), but no tool of theirs is allowed.
- `packets` joins the sealed gold and the saved answers under random codes (W/packets; W/key.json outside it).
- Two isolated graders see only packets/, READER-INSTRUCTIONS.md and their reader order (run_graders.sh).
- `score` takes the STRICTER grade on a split. Bar (D-216): correct >= 0.80 of the answerable questions,
  0 superseded_as_current, every negative abstains.

Truth-set rows (JSONL): {"id", "question", "gold", "category", "lang", "quotes": [{"file", "text"}],
"must_not_state_as_current": [...]}. `category: negative` marks a question the memory must not answer; any
other category (fact, temporal, temporal-superseded, procedure, lesson, layer, ...) is answerable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

TOOL = "mcp__hlm__memory_ask"
DENY = "Read Bash Glob Grep Edit Write NotebookEdit Agent Task WebFetch WebSearch Skill"
PER_ASK_RESERVE = 0.15
STRICT_ANSWERABLE = ["superseded_as_current", "contradiction", "incorrect", "correct"]
STRICT_NEGATIVE = ["fabricated", "abstention"]

INSTRUCTIONS = """# Blind grading: a migrated project's memory check
Each packet holds a question about a migrated project, the sealed gold (gold_answer, source_quotes,
must_not_state_as_current) and the memory system's answer. Grade from the packet alone: read ONLY the files
in this directory (packets/, this file, your reader order). Do NOT open anything outside this directory.
The answer may be in another language than the question; judge the content.
- answerable=true: grade in {correct, incorrect, superseded_as_current, contradiction}.
  - correct = the answer states the gold_answer's key facts and nothing contradicting it (extra correct detail
    is fine).
  - superseded_as_current = the answer presents any value listed in must_not_state_as_current, or another
    older value/state, as the CURRENT one (mentioning it clearly as history is fine).
  - contradiction = another statement that contradicts the gold.
  - incorrect = the key facts are missing or wrong, the answer abstains, or it is an error.
- answerable=false (negative): grade in {abstention, fabricated}. abstention = the answer says the memory does
  not hold it, or rejects the question's premise with evidence (pointing to related items is fine);
  fabricated = it asserts an answer as fact.
Output one JSON line per code in your reader order: {"code","grade","why"} ("why" <= 30 words) to
grades-<n>.jsonl. Finish with a self-check: every code exactly once, valid grades.
"""


def _write(path: Path, obj: object) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1))


def _truth(path: Path, sha: str) -> list[dict]:
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != sha:
        sys.exit(f"SEAL BROKEN: the truth set hashes to {got}, not the sealed {sha}")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _workdir(path: str) -> Path:
    w = Path(path).expanduser().resolve()
    w.mkdir(parents=True, exist_ok=True, mode=0o700)
    return w


# --------------------------------------------------------------------------- extract
def cmd_extract(a: argparse.Namespace) -> None:
    w = _workdir(a.dir)
    rows = _truth(Path(a.truthset).expanduser(), a.sha256)
    qs = [{"qid": f"{a.project}:{d['id']}", "project": a.project, "question": d["question"]} for d in rows]
    _write(w / "questions.jsonl", "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in qs))
    print(f"seal ok {a.sha256[:8]}; questions {len(qs)}")


# --------------------------------------------------------------------------- ask
def _prompt(project: str, question: str) -> str:
    args = {"project": project, "question": question}
    return (
        f"You are a relay. Load the tool {TOOL} with ToolSearch (query 'select:{TOOL}'; if the hlm server "
        "is still connecting, wait a few seconds and search again). Then call it EXACTLY ONCE with EXACTLY "
        "these arguments, copying every character verbatim:\n"
        + json.dumps(args, ensure_ascii=False)
        + "\nDo not call any other hlm tool and do not call it twice. Do not repeat or summarise the result. "
        "After the call, reply with the single word: done"
    )


def _ask_one(q: dict, outdir: Path, relay_cwd: str, model: str) -> dict:
    out = outdir / (q["qid"].replace(":", "__") + ".json")
    if out.exists():
        return {"qid": q["qid"], "status": "exists"}
    env = dict(os.environ, MCP_TOOL_TIMEOUT="180000", HLM_CAPTURE="off", HLM_BRIEF_DIGEST="off")
    cmd = [
        "claude",
        "-p",
        _prompt(q["project"], q["question"]),
        # no user/project/local settings (no hooks such as the SessionStart brief, no plugins), no code tools
        "--restricted",
        "--allowedTools",
        "ToolSearch",
        TOOL,
        "--disallowedTools",
        DENY,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "6",
    ]
    t0 = time.time()
    p = subprocess.run(
        cmd, cwd=relay_cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=400
    )
    calls: list[dict] = []
    results: list[dict] = []
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for c in content:
            if c.get("type") == "tool_use" and c.get("name") == TOOL:
                calls.append({"id": c.get("id"), "input": c.get("input")})
            if c.get("type") == "tool_result" and any(c.get("tool_use_id") == k["id"] for k in calls):
                body = c.get("content")
                text = (
                    body
                    if isinstance(body, str)
                    else "".join(x.get("text", "") for x in body or [] if isinstance(x, dict))
                )
                results.append({"is_error": c.get("is_error", False), "text": text})
    inp = (calls[0]["input"] or {}) if calls else {}
    mismatch = not calls or inp.get("question") != q["question"] or inp.get("project") != q["project"]
    raw = None
    if results and not results[0]["is_error"]:
        try:
            raw = json.loads(results[0]["text"])
        except ValueError:
            raw = None
    cost = float(((raw or {}).get("meta") or {}).get("cost_usd") or 0.0)
    status = (
        "no_call"
        if not calls
        else "relay_mismatch"
        if mismatch
        else "multi_call"
        if len(calls) > 1
        else "tool_error"
        if results and results[0]["is_error"]
        else "ok"
        if raw is not None
        else "no_result"
    )
    rec = {
        **q,
        "wall_s": round(time.time() - t0, 1),
        "n_calls": len(calls),
        "status": status,
        "raw": raw,
        "error": results[0]["text"][:500] if results and results[0]["is_error"] else None,
        "cost_usd": cost,
        "relay_rc": p.returncode,
    }
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return {"qid": q["qid"], "status": status, "wall_s": rec["wall_s"], "cost_usd": cost}


def cmd_ask(a: argparse.Namespace) -> None:
    w = _workdir(a.dir)
    qs = [
        json.loads(x) for x in (w / "questions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    if not all(set(q) == {"qid", "project", "question"} for q in qs):
        sys.exit("questions.jsonl must hold qid/project/question only (run `extract`)")
    outdir = w / "answers"
    outdir.mkdir(exist_ok=True, mode=0o700)
    relay_cwd = tempfile.mkdtemp(prefix="hlm-relay-")
    lock = threading.Lock()
    state = {"spent": 0.0, "in_flight": 0, "not_asked": []}
    queue = list(qs)
    log = (w / "ask.log").open("a")

    def worker() -> None:
        while True:
            with lock:
                if not queue:
                    return
                if state["spent"] + PER_ASK_RESERVE * (state["in_flight"] + 1) > a.cap:
                    state["not_asked"].extend(x["qid"] for x in queue)
                    queue.clear()
                    return
                q = queue.pop(0)
                state["in_flight"] += 1
            try:
                r = _ask_one(q, outdir, relay_cwd, a.model)
            except Exception as exc:  # noqa: BLE001 - asked once: record and continue, never retry
                r = {"qid": q["qid"], "status": "exception", "error": repr(exc)[:300]}
            with lock:
                state["in_flight"] -= 1
                state["spent"] += float(r.get("cost_usd") or 0.0)
                r["spent_total"] = round(state["spent"], 6)
                line = json.dumps(r, ensure_ascii=False)
                print(line, flush=True)
                log.write(line + "\n")
                log.flush()

    threads = [threading.Thread(target=worker) for _ in range(max(1, min(a.workers, 2)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _write(
        w / "spend.json",
        {"spent_usd": round(state["spent"], 6), "cap_usd": a.cap, "not_asked": state["not_asked"]},
    )
    print(json.dumps({"spent_usd": round(state["spent"], 6), "not_asked": state["not_asked"]}))


# --------------------------------------------------------------------------- packets
def cmd_packets(a: argparse.Namespace) -> None:
    w = _workdir(a.dir)
    qs = {
        q["qid"]: q
        for q in (json.loads(x) for x in (w / "questions.jsonl").read_text().splitlines() if x.strip())
    }
    project = next(iter(qs.values()))["project"] if qs else ""
    gold = {f"{project}:{d['id']}": d for d in _truth(Path(a.truthset).expanduser(), a.sha256)}
    (w / "packets").mkdir(exist_ok=True, mode=0o700)
    rng = random.Random(a.seed)
    alphabet = "ABCDEFGJKMNPQRSTUVWXYZ23456789"
    key: dict[str, dict] = {}
    for qid, g in sorted(gold.items()):
        ans_p = w / "answers" / (qid.replace(":", "__") + ".json")
        a_rec = json.loads(ans_p.read_text()) if ans_p.exists() else {"status": "not_asked"}
        raw = a_rec.get("raw") or {}
        code = "".join(rng.choice(alphabet) for _ in range(5))
        while code in key:
            code = "".join(rng.choice(alphabet) for _ in range(5))
        negative = g.get("category") == "negative"
        _write(
            w / "packets" / f"{code}.json",
            {
                "question": g["question"],
                "category": g.get("category"),
                "answerable": not negative,
                "gold_answer": g.get("gold"),
                "must_not_state_as_current": g.get("must_not_state_as_current") or [],
                "source_quotes": [q.get("text") for q in (g.get("quotes") or []) if isinstance(q, dict)],
                "answer": {
                    "text": raw.get("answer"),
                    "abstained": raw.get("abstained"),
                    "confidence": raw.get("confidence"),
                    "source_paths": [
                        s.get("path")
                        for s in (raw.get("primary") or []) + (raw.get("related") or [])
                        if isinstance(s, dict)
                    ],
                    "status": a_rec.get("status"),
                    "error": a_rec.get("error"),
                },
            },
        )
        key[code] = {
            "qid": qid,
            "answerable": not negative,
            "category": g.get("category"),
            "lang": g.get("lang"),
        }
    _write(w / "key.json", key)
    codes = sorted(key)
    for i in (1, 2):
        order = codes[:]
        random.Random(a.seed + i).shuffle(order)
        _write(w / f"reader-order-{i}.txt", "\n".join(order) + "\n")
    _write(w / "READER-INSTRUCTIONS.md", INSTRUCTIONS)
    print(f"packets {len(codes)} negatives {sum(1 for v in key.values() if not v['answerable'])}")


# --------------------------------------------------------------------------- score
def _grades(w: Path, i: int, key: dict) -> dict:
    rows = [json.loads(x) for x in (w / f"grades-{i}.jsonl").read_text().splitlines() if x.strip()]
    if sorted(r["code"] for r in rows) != sorted(key):
        sys.exit(f"grades-{i}.jsonl: codes do not match the key exactly once")
    for r in rows:
        valid = STRICT_ANSWERABLE if key[r["code"]]["answerable"] else STRICT_NEGATIVE
        if r.get("grade") not in valid:
            sys.exit(f"grades-{i}.jsonl: invalid grade {r.get('grade')!r} for {r['code']}")
    return {r["code"]: r for r in rows}


def score(key: dict, g1: dict, g2: dict, bar: float) -> dict:
    tot = {
        "n_ans": 0,
        "correct": 0,
        "superseded": 0,
        "contradiction": 0,
        "n_neg": 0,
        "abstain": 0,
        "disagree": 0,
    }
    fails: list[str] = []
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_q = []
    for code, k in sorted(key.items(), key=lambda kv: kv[1]["qid"]):
        g = [g1[code]["grade"], g2[code]["grade"]]
        order = STRICT_ANSWERABLE if k["answerable"] else STRICT_NEGATIVE
        final = next(x for x in order if x in g)
        ok = final in ("correct", "abstention")
        per_q.append(
            {
                "qid": k["qid"],
                "lang": k.get("lang"),
                "category": k.get("category"),
                "grader_1": g[0],
                "grader_2": g[1],
                "final": final,
            }
        )
        by_lang[str(k.get("lang"))][0] += ok
        by_lang[str(k.get("lang"))][1] += 1
        by_cat[str(k.get("category"))][0] += ok
        by_cat[str(k.get("category"))][1] += 1
        tot["disagree"] += g[0] != g[1]
        if k["answerable"]:
            tot["n_ans"] += 1
            tot["correct"] += final == "correct"
            tot["superseded"] += final == "superseded_as_current"
            tot["contradiction"] += final == "contradiction"
        else:
            tot["n_neg"] += 1
            tot["abstain"] += final == "abstention"
        if not ok:
            fails.append(f"{k['qid']}={final}" + ("" if g[0] == g[1] else f" (split {g[0]}/{g[1]})"))
    rate = tot["correct"] / tot["n_ans"] if tot["n_ans"] else 0.0
    passed = rate >= bar and tot["superseded"] == 0 and tot["abstain"] == tot["n_neg"]
    return {
        **tot,
        "correct_rate": round(rate, 3),
        "bar": bar,
        "PASS": passed,
        "fails": fails,
        "by_lang_ok": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_lang.items())},
        "by_category_ok": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_cat.items())},
        "per_question": per_q,
    }


def cmd_score(a: argparse.Namespace) -> None:
    w = _workdir(a.dir)
    key = json.loads((w / "key.json").read_text())
    packets = {p.stem for p in (w / "packets").glob("*.json")}
    if packets != set(key):
        sys.exit("key.json and packets/ hold different codes: re-run `packets` and grade again")
    out = score(key, _grades(w, 1, key), _grades(w, 2, key), a.bar)
    _write(w / "result.json", out)
    print(
        f"correct {out['correct']}/{out['n_ans']} ({out['correct_rate']:.2f}) superseded {out['superseded']} "
        f"contradictions {out['contradiction']} negatives abstained {out['abstain']}/{out['n_neg']} "
        f"disagreements {out['disagree']} -> {'PASS' if out['PASS'] else 'FAIL'}"
    )
    print("by lang:", out["by_lang_ok"], "| by category:", out["by_category_ok"])
    print("fails:", "; ".join(out["fails"]) or "none")
    sys.exit(0 if out["PASS"] else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--dir", required=True)
    e.add_argument("--truthset", required=True)
    e.add_argument("--sha256", required=True)
    e.add_argument("--project", required=True)
    k = sub.add_parser("ask")
    k.add_argument("--dir", required=True)
    k.add_argument("--workers", type=int, default=2)
    k.add_argument("--cap", type=float, default=1.00, help="memory.ask spend cap in USD (server-side cost)")
    k.add_argument("--model", default=os.environ.get("RELAY_MODEL", "sonnet"))
    p = sub.add_parser("packets")
    p.add_argument("--dir", required=True)
    p.add_argument("--truthset", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--seed", type=int, default=20261007)
    s = sub.add_parser("score")
    s.add_argument("--dir", required=True)
    s.add_argument("--bar", type=float, default=0.80)
    a = ap.parse_args()
    {"extract": cmd_extract, "ask": cmd_ask, "packets": cmd_packets, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
