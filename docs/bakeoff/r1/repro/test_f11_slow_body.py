"""F11: the request transaction (with FOR SHARE on the device row) is open while the route reads the body.
(a) a trusted device trickling a POST /mcp body blocks its own revocation indefinitely;
(b) unauthenticated POST /devices/register with a stalled body pins pool connections -> whole API stalls."""
import asyncio

from docs.bakeoff.r1.repro._app import app_client
from tests.integration._mcp_fixtures import ADMIN_TOKEN, MCP_HEADERS, bearer, create_project, trusted_device

POOL = 3


async def _stalled_body():
    yield b'{"jsonrpc":"2.0","id":1,'
    await asyncio.sleep(3600)
    yield b"}"


async def test_f11a_revoke_completes_while_device_trickles_body(db_dsn):
    async with app_client(db_dsn) as client:
        await create_project(client, "f11")
        did, tok = await trusted_device(client, "slowpoke", grants=[{"project": "f11", "role": "write"}])
        slow = asyncio.create_task(client.post("/mcp", content=_stalled_body(),
                                               headers={**MCP_HEADERS, **bearer(tok), "Content-Length": "1000000"}))
        await asyncio.sleep(0.5)
        try:
            r = await asyncio.wait_for(client.post(f"/admin/devices/{did}/revoke", json={}, headers=bearer(ADMIN_TOKEN)),
                                       timeout=5)
            print("revoke:", r.status_code, r.text[:200])
            ok = True
        except TimeoutError:
            ok = False
        slow.cancel()
        await asyncio.gather(slow, return_exceptions=True)
        assert ok, "admin revoke blocked >5 s behind the device's open FOR SHARE transaction (stalled body)"


async def test_f11b_unauthenticated_register_stall_pins_pool(db_dsn):
    async with app_client(db_dsn, pool_max_size=POOL, pool_min_size=1) as client:
        slow = [asyncio.create_task(client.post("/devices/register", content=_stalled_body(),
                                                headers={"Content-Type": "application/json"})) for _ in range(POOL)]
        await asyncio.sleep(0.5)
        print("pool stats:", client.app.state.pool.get_stats())
        try:
            r = await asyncio.wait_for(client.get("/admin/projects", headers=bearer(ADMIN_TOKEN)), timeout=5)
            ok = True
        except TimeoutError:
            ok = False
        for t in slow:
            t.cancel()
        await asyncio.gather(*slow, return_exceptions=True)
        assert ok, f"{POOL} unauthenticated stalled /devices/register bodies exhausted pool_max_size={POOL}"
