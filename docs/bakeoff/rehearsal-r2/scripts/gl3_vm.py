"""G-L3 on the 2 vCPU VM, from the Mac through Caddy: 3 query callers timed DURING N acknowledged
~6 kB memory.write calls (librarian LIVE: each write enqueues a librarian_write job in the api).
Token from the isolated hlm config (never argv/printed)."""
import argparse, asyncio, json, random, statistics, sys, time, uuid
import httpx
sys.path.insert(0, ".")
import mcpc

WORDS = ("deploy review migration cache latency budget session card lesson retry queue worker index backup "
         "restore token grant project device policy archive summary decision note sprint release config "
         "Karte Dienst Speicher Abfrage Sitzung Entscheidung proje karar oturum kayıt yedek sürüm ayar").split()
QUERIES = [
    "how do we restore a backup after a failed migration", "worker queue retry backoff", "svc-qx7 APP_DB_DSN",
    "E4193 retry with backoff", "which port does the Dienst run on", "device grant revoke policy",
    "release config session card", "Speicher Abfrage Karte", "yedek sürüm ayar kayıt", "latency budget index",
    "archive summary decision note", "sprint release lesson", "token grant project", "deploy review cache",
    "oturum karar proje", "Sitzung Entscheidung", "migration lock timeout", "note-17-3", "Load note 42",
    "backup restore drill marker", "librarian observer proposals", "pre-upgrade retention snapshots",
]

def neutral(i):
    rng = random.Random(i)
    return "\n".join(f"note-{i}-{k}: " + " ".join(rng.choice(WORDS) for _ in range(12)) + "." for k in range(60))

def identifier(i):
    return "\n".join(f"Load note {i}.{k}: svc-qx7 reads APP_DB_DSN, retries E4193 with backoff; Karte {k} güncellendi,"
                     f" der Dienst läuft auf Port {8000 + k}." for k in range(60))

def p95(v): return statistics.quantiles(v, n=100, method="inclusive")[94] if len(v) > 1 else (v[0] if v else float("nan"))

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True); ap.add_argument("--project", required=True)
    ap.add_argument("--n", type=int, default=100); ap.add_argument("--callers", type=int, default=3)
    ap.add_argument("--body", choices=("neutral", "identifier"), default="neutral"); ap.add_argument("--run", default="gl3")
    ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args()
    make = neutral if a.body == "neutral" else identifier
    tok = mcpc.token(a.device)
    import os
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=mcpc.URL, verify=os.environ["SSL_CERT_FILE"], timeout=60,
                                 limits=httpx.Limits(max_connections=8, max_keepalive_connections=8)) as c:
        r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "gl3-vm", "version": "1"}}})
        r.raise_for_status(); h["MCP-Protocol-Version"] = r.json()["result"]["protocolVersion"]
        errors = []
        async def call(name, args):
            r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            if r.status_code != 200:
                errors.append(f"{name} http {r.status_code}"); return None
            res = r.json().get("result") or {}
            if res.get("isError"):
                errors.append(f"{name} isError {res['content'][0]['text'][:120]}"); return None
            return json.loads(res["content"][0]["text"])
        q = lambda k: call("memory.query", {"project": a.project, "query": QUERIES[k % len(QUERIES)], "token_budget": 2000})
        for k in range(20):
            await q(k)
        quiet = []
        async def quiet_caller(i):
            for k in range(i, 60, a.callers):
                t0 = time.perf_counter(); await q(k); quiet.append((time.perf_counter() - t0) * 1000)
        await asyncio.gather(*(quiet_caller(i) for i in range(a.callers)))
        lat, write_ms, done = [], [], asyncio.Event()
        async def caller(i):
            k = i
            while not done.is_set():
                t0 = time.perf_counter(); await q(k); lat.append((time.perf_counter() - t0) * 1000); k += a.callers
        acked = 0
        async def writer():
            nonlocal acked
            for i in range(a.offset, a.offset + a.n):
                t0 = time.perf_counter()
                ack = await call("memory.write", {"project": a.project, "request_id": str(uuid.uuid4()), "client": "r2-rehearsal/gl3",
                                                 "items": [{"kind": "fact", "title": f"{a.run} note {i}", "body": make(i)}]})
                write_ms.append((time.perf_counter() - t0) * 1000)
                if ack and ack.get("versions"): acked += 1
            done.set()
        t_start = time.time()
        await asyncio.gather(writer(), *(caller(i) for i in range(a.callers)))
        t_end = time.time()
    out = {"run": a.run, "body": a.body, "writes_acked": acked, "writes": a.n, "body_bytes": len(make(0).encode()),
           "t_start": round(t_start, 3), "t_end": round(t_end, 3), "window_s": round(t_end - t_start, 1),
           "queries_during": len(lat), "q_p50": round(statistics.median(lat), 1), "q_p95": round(p95(lat), 1), "q_max": round(max(lat), 1),
           "write_p50": round(statistics.median(write_ms), 1), "write_p95": round(p95(write_ms), 1), "write_max": round(max(write_ms), 1),
           "quiet_n": len(quiet), "quiet_p50": round(statistics.median(quiet), 1), "quiet_p95": round(p95(quiet), 1),
           "errors": len(errors), "error_samples": errors[:5]}
    print(json.dumps(out))

asyncio.run(main())
