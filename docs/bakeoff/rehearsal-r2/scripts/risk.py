"""risk_check probe (+ optional lesson registration and a write) for the kill-switch test."""
import json, sys, uuid
sys.path.insert(0, ".")
from mcpc import Client
mode = sys.argv[1]
c = Client("r2-seed-mac")
TITLE = "r2 kill-switch lesson: restore.sh over the live database without a fresh backup"
if mode == "lesson":
    err, p, ms = c.call("memory.register_lesson", {"project": "r2-seed", "request_id": str(uuid.uuid4()),
        "mistake": TITLE + ". Every write since the last dump was lost.",
        "fix": "Always run deploy/backup/backup.sh right before restore.sh; never restore over live data outside a maintenance window.", "tags": ["r2-kill"]})
    print("register_lesson", "err" if err else "ok", json.dumps(p)[:200])
elif mode == "risk":
    err, p, ms = c.call("memory.risk_check", {"project": "r2-seed", "token_budget": 2048,
        "task": "Run deploy/backup/restore.sh on the production host now, over the live database, without taking a new backup first."})
    print(f"risk_check err={err} verdict={p.get('verdict')} judged={str(p.get('judged')).lower()} judge={p.get('judge')} reason={p.get('reason') or '-'} warnings={len(p.get('warnings') or [])} {ms:.0f}ms")
elif mode == "write":
    err, p, ms = c.write("r2-seed", [{"kind": "fact", "title": sys.argv[2], "body": f"Kill-switch probe write {sys.argv[2]}."}])
    print("write", sys.argv[2], "err" if err else f"v{p['versions'][0]['version_id']} event {p.get('event_id')}", f"{ms:.0f}ms")
