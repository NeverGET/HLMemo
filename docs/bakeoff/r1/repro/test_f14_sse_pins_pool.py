"""F14: each open GET /mcp event stream keeps its pooled DB connection for the stream's lifetime."""
import asyncio

from docs.bakeoff.r1.repro._app import app_client
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, writer_on

POOL = 3


async def test_f14_idle_sse_streams_release_db_connections(db_dsn):
    async with app_client(db_dsn, pool_max_size=POOL, pool_min_size=1) as client:
        tok = await writer_on(client, "f14")
        hdr = {"Accept": "text/event-stream", **bearer(tok)}
        streams = [asyncio.create_task(client.get("/mcp", headers=hdr)) for _ in range(POOL)]
        await asyncio.sleep(1.0)
        done = [t for t in streams if t.done()]
        for t in done:
            r = t.result()
            print("GET /mcp finished early:", r.status_code, r.headers.get("content-type"), r.text[:200])
        print("pool stats:", client.app.state.pool.get_stats())
        try:
            r = await asyncio.wait_for(client.get("/admin/projects", headers=bearer(ADMIN_TOKEN)), timeout=5)
            print("admin call:", r.status_code)
            ok = True
        except TimeoutError:
            ok = False
        for t in streams:
            t.cancel()
        await asyncio.gather(*streams, return_exceptions=True)
        assert ok, f"{POOL} idle SSE streams exhausted pool_max_size={POOL}: an admin request hung >5 s"
