"""Quiet (no-write) memory.query latency probe for the G-L3 diagnosis: the gl3 query mix, N rounds x 3
callers, per-query latencies; prints p50/p95/max overall and the slowest queries. Runs on the Mac
(through the lima port forward) or INSIDE the VM (python3 stdlib only; token on stdin, never argv).

    python3 quiet_probe.py URL PROJECT [ROUNDS] < token      (CA: env SSL_CERT_FILE, or INSECURE=1 on the VM loopback)
"""
import json, os, ssl, statistics, sys, threading, time, urllib.request

QUERIES = [
    "how do we restore a backup after a failed migration", "worker queue retry backoff", "svc-qx7 APP_DB_DSN",
    "E4193 retry with backoff", "which port does the Dienst run on", "device grant revoke policy",
    "release config session card", "Speicher Abfrage Karte", "yedek sürüm ayar kayıt", "latency budget index",
    "archive summary decision note", "sprint release lesson", "token grant project", "deploy review cache",
    "oturum karar proje", "Sitzung Entscheidung", "migration lock timeout", "note-17-3", "Load note 42",
    "backup restore drill marker", "librarian observer proposals", "pre-upgrade retention snapshots",
]
url, project = sys.argv[1].rstrip("/"), sys.argv[2]
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
token = sys.stdin.readline().strip()
ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))
if os.environ.get("INSECURE") == "1":
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
H = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

def post(body, hdr):
    req = urllib.request.Request(url + "/mcp", data=json.dumps(body).encode(), headers=hdr, method="POST")
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())

init = post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
             "clientInfo": {"name": "quiet-probe", "version": "1"}}}, H)
H["MCP-Protocol-Version"] = init["result"]["protocolVersion"]
q = lambda text: post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory.query",
                       "arguments": {"project": project, "query": text, "token_budget": 2000}}}, H)
for text in QUERIES:  # warm-up
    q(text)
lat = []
def caller(i):
    for r in range(rounds):
        for k in range(i, len(QUERIES), 3):
            t0 = time.perf_counter(); q(QUERIES[k]); lat.append(((time.perf_counter() - t0) * 1000, QUERIES[k]))
ts = [threading.Thread(target=caller, args=(i,)) for i in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
v = sorted(x for x, _ in lat)
p95 = statistics.quantiles(v, n=100, method="inclusive")[94]
print(json.dumps({"n": len(v), "p50": round(statistics.median(v), 1), "p95": round(p95, 1), "max": round(v[-1], 1),
                  "slowest": [(round(ms), t) for ms, t in sorted(lat, reverse=True)[:6]]}, ensure_ascii=False))
