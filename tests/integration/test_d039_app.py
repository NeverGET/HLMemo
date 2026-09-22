"""A public readiness flood opens one bounded DB connection and never leases traffic pools."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from hlmemo.server import app as server
from tests.integration._mcp_fixtures import running_app

pytestmark = pytest.mark.integration


async def test_500_public_readiness_probes_share_one_connection_and_leave_pools_usable(db_dsn, monkeypatch):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("HLM_TRUSTED_PROXY_IPS", "")
    monkeypatch.setattr(server, "_verify_models_blocking", lambda *_: {"ok": True})
    real_connect = server.AsyncConnection.connect
    calls = active = peak = 0

    @asynccontextmanager
    async def tracked_connection(conn):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            async with conn:
                yield conn
        finally:
            active -= 1

    async def connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return tracked_connection(await real_connect(*args, **kwargs))

    # Replace only app.py's connector; actual pool connections keep their normal implementation.
    monkeypatch.setattr(server, "AsyncConnection", SimpleNamespace(connect=connect))
    async with running_app(db_dsn) as client:
        pool, admin_pool = client.app.state.pool, client.app.state.admin_pool
        async with pool.connection() as normal, admin_pool.connection() as admin1, admin_pool.connection():
            before = [p.get_stats().get("requests_num", 0) for p in (pool, admin_pool)]
            responses = await asyncio.gather(*(client.get("/ready") for _ in range(500)))
            assert all(response.status_code == 200 for response in responses)
            assert calls == peak == 1 and active == 0
            assert [p.get_stats().get("requests_num", 0) for p in (pool, admin_pool)] == before
            assert (await (await normal.execute("SELECT 1")).fetchone()) == (1,)
            assert (await (await admin1.execute("SELECT 1")).fetchone()) == (1,)
            # Explicitly expire a completed cache window and repeat the same public flood.
            client.app.state.readiness_probe.expires_at = 0
            responses = await asyncio.gather(*(client.get("/ready") for _ in range(500)))
            assert all(response.status_code == 200 for response in responses)
            assert calls == 2 and peak == 1 and active == 0
        # Pool availability remains intact after the flood, for normal and reserved requests.
        async with pool.connection() as normal, admin_pool.connection() as admin:
            assert (await (await normal.execute("SELECT 1")).fetchone()) == (1,)
            assert (await (await admin.execute("SELECT 1")).fetchone()) == (1,)
