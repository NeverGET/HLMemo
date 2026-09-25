"""Light production e2e after the R3 deploy (step 4b): MCP initialize + tools/list, one memory.write and
one memory.query of a marker on the gates project, one memory.risk_check. Token from the isolated hlm
config (mcpc.token: never argv or output). HLM_URL selects the server.

    uv run --project REPO python e2e_light.py DEVICE PROJECT
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rehearsal-r3", "scripts"))
import mcpc  # noqa: E402

device, project = sys.argv[1], sys.argv[2]
c = mcpc.Client(device)
r = c.rpc("tools/list", {})
tools = sorted(t["name"] for t in r.json()["result"]["tools"])
print(f"mcp initialize + tools/list: http {r.status_code}, {len(tools)} tools: {tools}")
marker = f"r3-prod-e2e-{uuid.uuid4().hex[:8]}"
err, p, ms = c.write(project, [{"kind": "fact", "title": marker,
                                "body": f"R3 production e2e marker {marker}: 805f4cd deployed with the R3 env."}],
                     client="r3-prod-e2e")
vid = None if err else (p.get("versions") or [{}])[0].get("version_id")
print(f"memory.write err={err} version_id={vid} {ms:.0f} ms")
time.sleep(5)  # embed worker
err, p, ms = c.query(project, f"R3 production e2e marker {marker}", budget=1024)
print(f"memory.query err={err} marker_found={marker in json.dumps(p)} {ms:.0f} ms")
err, p, ms = c.call("memory.risk_check", {
    "project": project, "token_budget": 2048,
    "task": "Run deploy/backup/restore.sh on the production host over the live database without a fresh backup."})
print(f"memory.risk_check err={err} verdict={p.get('verdict')} judged={p.get('judged')} judge={p.get('judge')} "
      f"reason={p.get('reason') or '-'} warnings={len(p.get('warnings') or [])} {ms:.0f} ms")
