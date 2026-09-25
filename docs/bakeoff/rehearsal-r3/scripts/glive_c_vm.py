"""G-LIVE-C on the VM, through the DEPLOYED production wiring (D-094/D-098, D-099 criterion 2).

The same fixture (tests/fixtures/risk: 50-entry library, 80 cases = 40 positive / 40 negative, sha256
pinned by SHA256SUMS), the same world shape and the same scoring/pass rule as
tests/integration/test_wf_glive_c_wiring.py, but nothing runs in-process: the world is written through
MCP (memory.register_lesson / memory.write) and every case is a real `memory.risk_check` call to the
api container on the 2 vCPU VM, whose llm.env carries the D-094 mapping. The operator forces the
PRIMARY unavailable in the api only (api.env: HLM_LLM_BASE_URL=http://127.0.0.1:9/v1, which only the
primary reads), so every judged verdict must come from HLM_FALLBACK_PROFILE__RISK_JUDGE.

  seed: python glive_c_vm.py seed --repo REPO --loader rk-loader --reader-id N --out world.json
        (projects rk-main / rk-shell / rk-secret and the grants are created with hlm_ops.sh first)
  run:  python glive_c_vm.py run --repo REPO --reader rk-reader --world world.json --reps 4 --out DIR

Pass (G-LIVE-C): catch >= 0.85 and false-warn <= 0.10 on EVERY rep, and every judged verdict is
`ok_fallback` (the task fallback), none `ok` (the dead primary). Tokens come from the isolated hlm
config (mcpc.token), never argv or logs. Outputs are aggregates only (no provider text)."""
import argparse, collections, hashlib, json, os, statistics, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpc import Client

MAIN, SHELL, SECRET, GLOBAL = "rk-main", "rk-shell", "rk-secret", "hlm-global"
PROJECT_OF = {"main": MAIN, "main-work": MAIN, "shell": SHELL, "secret": SECRET, "global": GLOBAL}
LESSON_TAG = "rk:"
CATCH_MIN, FALSE_WARN_MAX = 0.85, 0.10


def pinned(repo, name):
    d = os.path.join(repo, "tests", "fixtures", "risk")
    raw = open(os.path.join(d, name), "rb").read()
    pins = dict(reversed(l.split()) for l in open(os.path.join(d, "SHA256SUMS")).read().splitlines() if l)
    digest = hashlib.sha256(raw).hexdigest()
    assert pins.get(name) == digest, f"{name}: sha256 {digest} != pinned {pins.get(name)}"
    return json.loads(raw)


def seed(a):
    sys.path.insert(0, os.path.join(a.repo, "src"))
    try:  # the release's own title/body shaping for experience entries (pure functions)
        from hlmemo.core.lesson_service import lesson_body, lesson_title as lt
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"cannot import hlmemo.core.lesson_service from {a.repo}/src: {exc}")
    lib = pinned(a.repo, "library.json")
    c = Client(a.loader)
    world = {}
    for les in lib["lessons"]:
        project = PROJECT_OF[les["project"]]
        scope = {"main-work": "class:work"}.get(les["project"], "all")
        if les.get("scope") == "self":
            scope = f"device:{a.reader_id}"
        tags = [*les.get("tags", []), LESSON_TAG + les["id"]]
        if les.get("kind", "lesson") == "lesson":
            args = {"project": project, "request_id": str(uuid.uuid4()), "mistake": les["mistake"],
                    "fix": les["fix"], "tags": tags, "device_scope": scope}
            if les.get("context"):
                args["context"] = les["context"]
            err, p, ms = c.call("memory.register_lesson", args)
            if err:
                raise SystemExit(f"register_lesson {les['id']} failed: {json.dumps(p)[:300]}")
            vid, lid = p["version_id"], p["logical_id"]
        else:
            err, p, ms = c.write(project, [{"kind": les["kind"], "title": lt(les["mistake"]),
                                            "body": lesson_body(les["mistake"], les["fix"], les.get("context")),
                                            "tags": tags, "device_scope": scope, "stability": "stable"}], client="r3-glive-c/seed")
            if err:
                raise SystemExit(f"write {les['id']} failed: {json.dumps(p)[:300]}")
            vid, lid = p["versions"][0]["version_id"], p["versions"][0]["logical_id"]
        world[les["id"]] = {"version_id": vid, "logical_id": lid, "project": project, "scope": scope}
    for fact in lib["facts"]:
        err, p, ms = c.write(PROJECT_OF[fact["project"]], [{"kind": "fact", "title": fact["title"], "body": fact["body"]}],
                             client="r3-glive-c/seed")
        if err:
            raise SystemExit(f"fact {fact.get('id')} failed: {json.dumps(p)[:300]}")
    json.dump({"lessons": world, "reader_id": a.reader_id}, open(a.out, "w"), indent=1)
    print(f"seeded {len(world)} library entries + {len(lib['facts'])} facts -> {a.out}")


def score_case(case, warned, lessons):
    gold = set(case["gold"])
    if gold:
        return {"positive": True, "caught": warned and bool(gold & set(lessons))}
    return {"positive": False, "false_warn": warned}


def rates(rows):
    pos = [s for s in rows if s["positive"]]; neg = [s for s in rows if not s["positive"]]
    return {"catch": sum(s["caught"] for s in pos) / max(1, len(pos)),
            "false_warn": sum(s["false_warn"] for s in neg) / max(1, len(neg)), "n_pos": len(pos), "n_neg": len(neg)}


def p(xs, q):
    return statistics.quantiles(xs, n=100, method="inclusive")[q - 1] if len(xs) > 1 else (xs[0] if xs else None)


def run(a):
    cases = pinned(a.repo, "cases.json")["cases"]
    if a.cases:  # smoke only (harness check); the gate always runs all 80
        cases = cases[: a.cases]
    world = json.load(open(a.world))["lessons"]
    lesson_of_version = {v["version_id"]: k for k, v in world.items()}
    c = Client(a.reader, timeout=30)
    reps, lat = [], []
    for rep in range(a.reps):
        rows = []
        for case in cases:
            err, out, ms = c.call("memory.risk_check", {"project": MAIN, "task": case["task"], "token_budget": 4000})
            lat.append(ms)
            if err:
                rows.append({"id": case["id"], "split": case["split"], "status": f"error:{json.dumps(out)[:80]}",
                             **score_case(case, False, [])}); continue
            lessons = [lesson_of_version.get(int(w["clue"][1:].split(".", 1)[0])) for w in out.get("warnings") or []]
            s = score_case(case, out.get("verdict") == "warn", lessons)
            status = out["judge"] if out.get("judged") else f"retrieval_only:{out.get('reason')}"
            rows.append({"id": case["id"], "split": case["split"], "status": status, **s})
        judged = [x for x in rows if x["status"] in ("ok", "ok_fallback")]
        entry = {"rep": rep, **rates(rows), "judged_only": rates(judged),
                 "statuses": dict(collections.Counter(x["status"] for x in rows)),
                 "misses": [x["id"] for x in rows if x["positive"] and not x["caught"]],
                 "false_warns": [x["id"] for x in rows if not x["positive"] and x["false_warn"]]}
        reps.append(entry)
        print(f"rep {rep}: catch={entry['catch']:.3f} false_warn={entry['false_warn']:.3f} statuses={entry['statuses']}", flush=True)
    statuses = collections.Counter()
    for e in reps:
        statuses.update(e["statuses"])
    passed = (min(e["catch"] for e in reps) >= CATCH_MIN and max(e["false_warn"] for e in reps) <= FALSE_WARN_MAX
              and statuses.get("ok", 0) == 0 and statuses.get("ok_fallback", 0) > 0)
    report = {"fixture": "tests/fixtures/risk (sha256 pinned)", "reps": reps,
              "catch_min": min(e["catch"] for e in reps), "catch_mean": statistics.mean(e["catch"] for e in reps),
              "false_warn_max": max(e["false_warn"] for e in reps), "false_warn_mean": statistics.mean(e["false_warn"] for e in reps),
              "statuses_total": dict(statuses), "judged_by_primary_ok": statuses.get("ok", 0),
              "risk_check_ms_p50": p(lat, 50), "risk_check_ms_p95": p(lat, 95), "pass": passed}
    os.makedirs(a.out, exist_ok=True)
    json.dump(report, open(os.path.join(a.out, "glive_c_vm.json"), "w"), indent=1, sort_keys=True)
    print(json.dumps({k: report[k] for k in ("catch_min", "catch_mean", "false_warn_max", "false_warn_mean",
                                              "statuses_total", "risk_check_ms_p50", "risk_check_ms_p95", "pass")}))
    print("RESULT G-LIVE-C-VM " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("seed"); s.add_argument("--repo", required=True); s.add_argument("--loader", required=True)
    s.add_argument("--reader-id", type=int, required=True); s.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("--repo", required=True); r.add_argument("--reader", required=True)
    r.add_argument("--world", required=True); r.add_argument("--reps", type=int, default=4); r.add_argument("--out", required=True)
    r.add_argument("--cases", type=int, default=0, help="smoke: first N cases only (0 = all)")
    a = ap.parse_args()
    sys.exit(seed(a) if a.mode == "seed" else run(a))
