"""Import-burst companion: 3 timed query callers for --duration s plus spaced ~6 kB writes
(priority-3 librarian jobs) while `hlm import` (priority 6) runs. Token from the isolated config."""
import argparse, asyncio, json, os, random, statistics, sys, time, uuid
import httpx
sys.path.insert(0, ".")
import mcpc
WORDS = ("deploy review migration cache latency budget session card lesson retry queue worker index backup "
         "restore token grant project device policy archive summary decision note sprint release config").split()
QUERIES = ["librarian observer role proposals", "deploy.sh accept compose change", "risk_check judge fallback",
           "G-L3 query latency under write load", "backup restore drill", "device mint over ssh", "Sol review findings",
           "import markdown provenance", "skeleton project card", "budget reservation spend guard", "gin fastupdate off",
           "DF cache stale while revalidate", "two tier librarian luna pro", "W0a access hardening one-way door"]
def p95(v): return statistics.quantiles(v, n=100, method="inclusive")[94]
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device"); ap.add_argument("--project"); ap.add_argument("--qproject")
    ap.add_argument("--duration", type=float, default=240); ap.add_argument("--write-start", type=float, default=30)
    ap.add_argument("--write-every", type=float, default=20); ap.add_argument("--writes", type=int, default=10)
    a = ap.parse_args()
    h = {"Authorization": f"Bearer {mcpc.token(a.device)}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=mcpc.URL, verify=os.environ["SSL_CERT_FILE"], timeout=60) as c:
        r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "burst", "version": "1"}}})
        h["MCP-Protocol-Version"] = r.json()["result"]["protocolVersion"]
        errors = []
        async def call(name, args):
            r = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            res = r.json().get("result") or {} if r.status_code == 200 else {}
            if r.status_code != 200 or res.get("isError"):
                errors.append(f"{name} {r.status_code}"); return None
            return json.loads(res["content"][0]["text"])
        t0 = time.monotonic(); lat = []; writes = []
        async def caller(i):
            k = i
            while time.monotonic() - t0 < a.duration:
                s = time.perf_counter()
                await call("memory.query", {"project": a.qproject, "query": QUERIES[k % len(QUERIES)], "token_budget": 2000})
                lat.append((time.perf_counter() - s) * 1000); k += 3
        async def writer():
            await asyncio.sleep(a.write_start)
            for i in range(a.writes):
                rng = random.Random(5000 + i)
                body = "\n".join(f"burst-{i}-{k}: " + " ".join(rng.choice(WORDS) for _ in range(12)) + "." for k in range(60))
                s = time.time()
                ack = await call("memory.write", {"project": a.project, "request_id": str(uuid.uuid4()), "client": "r2-rehearsal/burst",
                                                 "items": [{"kind": "fact", "title": f"burst concurrent write {i}", "body": body}]})
                writes.append({"i": i, "t": round(s, 3), "vid": (ack or {}).get("versions", [{}])[0].get("version_id")})
                await asyncio.sleep(a.write_every)
        start = time.time()
        await asyncio.gather(writer(), *(caller(i) for i in range(3)))
    print(json.dumps({"t_start": round(start, 3), "t_end": round(time.time(), 3), "queries": len(lat), "q_p50": round(statistics.median(lat), 1),
                      "q_p95": round(p95(lat), 1), "q_max": round(max(lat), 1), "errors": len(errors), "writes": writes}))
asyncio.run(main())
