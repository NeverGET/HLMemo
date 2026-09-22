"""D-039: unauthorized revoke floods cannot occupy reserved admin connections."""

from __future__ import annotations

import asyncio

import pytest

from hlmemo.server.middleware import AuthMiddleware
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, running_app, trusted_device
from tests.integration.test_request_lifetime import _scope

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("alias", [False, True], ids=["admin-path", "body-target"])
async def test_cross_device_and_junk_revoke_flood_cannot_block_admin_ten_of_ten(db_dsn, monkeypatch, alias):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "2")
    monkeypatch.setenv("HLM_POOL_TIMEOUT_S", "0.08")
    monkeypatch.setenv("HLM_REQUEST_DB_TIMEOUT_S", "1")
    async with running_app(db_dsn) as client:
        successes = 0
        for attempt in range(10):
            device_id, token = await trusted_device(client, f"d039-flood-{attempt}")
            occupied = asyncio.Event()
            release = asyncio.Event()
            active = 0

            async def route(scope, receive, send, occupied=occupied, release=release):
                nonlocal active
                active += 1
                if active == 2:
                    occupied.set()
                await release.wait()
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(message):
                pass

            middleware = AuthMiddleware(route)
            holders = [
                asyncio.create_task(
                    middleware(
                        _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {token}".encode())]),
                        receive,
                        send,
                    )
                )
                for _ in range(2)
            ]
            flood = []
            revoke = None
            try:
                await asyncio.wait_for(occupied.wait(), 2)
                reserved_requests = client.app.state.admin_pool.get_stats()["requests_num"]
                flood = [
                    asyncio.create_task(
                        client.post(
                            "/devices/revoke" if alias else "/admin/devices/1/revoke",
                            json={"id": 1} if alias else {},
                            headers=bearer(token if i % 2 else "junk"),
                        )
                    )
                    for i in range(40)
                ]
                # Both normal slots hold D's shared lock. An old path-based reserved
                # lease would now wait exclusively on D and starve the real admin.
                await asyncio.sleep(0.02)
                assert client.app.state.admin_pool.get_stats()["requests_num"] == reserved_requests
                revoke = asyncio.create_task(
                    client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN))
                )
                await asyncio.sleep(0.12)
                assert not revoke.done(), "admin should wait for D's active requests, not fail pool checkout"
                release.set()
                response = await asyncio.wait_for(revoke, 2)
                assert response.status_code == 200, (attempt, response.text)
                assert response.json()["device"]["status"] == "revoked"
                successes += 1
            finally:
                release.set()
                await asyncio.gather(*holders, *flood, return_exceptions=True)
                if revoke is not None:
                    await asyncio.gather(revoke, return_exceptions=True)
        assert successes == 10


@pytest.mark.parametrize("alias", [False, True], ids=["admin-path", "body-target"])
async def test_concurrent_self_revokes_serialize_without_share_lock_upgrade(db_dsn, alias):
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "concurrent-self-revoke")
        path = "/devices/revoke" if alias else f"/admin/devices/{device_id}/revoke"
        responses = await asyncio.gather(
            *(client.post(path, json={"id": device_id}, headers=bearer(token)) for _ in range(2))
        )
        assert sorted(response.status_code for response in responses) == [200, 401]
