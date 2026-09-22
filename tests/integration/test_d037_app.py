"""D-037 readiness isolation, registration identity, and transport contract regressions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from hlmemo.config import get_settings
from hlmemo.core.write_models import WriteRequest, parse_request
from hlmemo.db.pool import create_pool
from hlmemo.server.tools import TOOL_BY_NAME
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    MCP_HEADERS,
    bearer,
    running_app,
    write_args,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("token", [None, ADMIN_TOKEN])
async def test_readiness_uses_independent_connection_under_pool_pressure(db_dsn, monkeypatch, token):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    monkeypatch.setattr("hlmemo.server.app._verify_models_blocking", lambda *_: {"ok": True})
    async with running_app(db_dsn) as client:
        async with client.app.state.pool.connection():
            response = await asyncio.wait_for(client.get("/ready", headers=bearer(token)), timeout=3)
        assert response.status_code == 200, response.text
        assert response.json()["checks"]["embed_config"]["ok"]


async def test_revoked_fingerprint_gets_fresh_pending_identity(db_dsn, connect):
    async with running_app(db_dsn) as client:
        body = {"name": "old-machine", "fingerprint": "same-machine", "client": "pytest/0"}
        old = (await client.post("/devices/register", json=body)).json()
        did = old["device"]["id"]
        response = await client.post(f"/admin/devices/{did}/revoke", headers=bearer(ADMIN_TOKEN), json={})
        assert response.status_code == 200
        response = await client.post("/devices/register", json={**body, "name": "new-machine"})
        assert response.status_code == 201, response.text
        new = response.json()
        assert new["device"]["id"] != did and new["device"]["status"] == "pending"
        assert new["token"] != old["token"]
        assert (await client.get("/devices/whoami", headers=bearer(old["token"]))).status_code == 401
        assert (await client.get("/devices/whoami", headers=bearer(new["token"]))).status_code == 403
        async with await connect() as conn:
            rows = await (
                await conn.execute(
                    "SELECT status, fingerprint FROM devices WHERE device_id IN (%s, %s) ORDER BY device_id",
                    (did, new["device"]["id"]),
                )
            ).fetchall()
            assert rows[0] == ("revoked", "same-machine")
            assert rows[1][0] == "pending" and rows[1][1].startswith("random:")


async def test_pool_has_pg17_transaction_timeout_safety_net(db_dsn):
    settings = get_settings(db_dsn=db_dsn, db_transaction_timeout_ms=19000)
    pool = create_pool(settings)
    await pool.open()
    try:
        async with pool.connection() as conn:
            if conn.info.server_version < 170000:
                pytest.skip("transaction_timeout requires PostgreSQL 17")
            value = await (await conn.execute("SHOW transaction_timeout")).fetchone()
            assert value == ("19s",)
    finally:
        await pool.close()


@pytest.mark.parametrize("ensure_ascii", [False, True])
async def test_contract_maximum_write_passes_sdk_transport(db_dsn, monkeypatch, ensure_ascii):
    """Exercise the real HTTP+SDK body cap, keeping expensive indexing outside this transport test."""
    accepted = []

    async def validate(conn, auth, arguments):
        request = parse_request(WriteRequest, arguments)
        accepted.append(len(request.items))
        return {"accepted": len(request.items)}

    spec = TOOL_BY_NAME["memory.write"]
    monkeypatch.setitem(TOOL_BY_NAME, spec.name, replace(spec, handler=validate))
    args = write_args("fx-main", [{"kind": "fact", "title": "Max", "body": "😀" * 64000}] * 50)
    wire = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "memory.write",
                "arguments": args,
            },
        },
        ensure_ascii=ensure_ascii,
    ).encode()
    assert len(wire) > 4 * 1024 * 1024
    async with running_app(db_dsn) as client:
        response = await client.post("/mcp", content=wire, headers={**MCP_HEADERS, **bearer(ADMIN_TOKEN)})
        assert response.status_code == 200, response.text[:300]
        assert json.loads(response.json()["result"]["content"][0]["text"]) == {"accepted": 50}
        assert accepted == [50]
        assert client.app.state.mcp.session_manager.max_request_body_size == 64 * 1024 * 1024
