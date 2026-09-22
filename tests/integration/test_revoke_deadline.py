"""A queued revoke outwaits bounded requests and cannot be starved by new readers."""

from __future__ import annotations

import asyncio

import pytest

from hlmemo.server import devices
from hlmemo.server.middleware import AuthMiddleware
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, running_app, trusted_device
from tests.integration.test_request_lifetime import _finish, _scope

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("finish_normally", [True, False], ids=["normal-finish", "request-deadline"])
async def test_revoke_outwaits_active_request_and_blocks_new_readers(
    db_dsn, connect, monkeypatch, finish_normally
):
    monkeypatch.setenv("HLM_DB_LOCK_TIMEOUT_MS", "50")
    monkeypatch.setenv("HLM_REQUEST_DB_TIMEOUT_S", "0.5")
    monkeypatch.setenv("HLM_POOL_MIN_SIZE", "4")
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "4")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "bounded-active-reader")
        active_entered = asyncio.Event()
        release_active = asyncio.Event()
        revoke_lock_started = asyncio.Event()
        revoke_pid = None
        route_entries = 0
        active_messages = []
        later_messages = []
        original_lock = devices.lock_device_access

        async def lock_device_access(conn, target_id, *, exclusive=False):
            nonlocal revoke_pid
            if target_id == device_id and exclusive:
                revoke_pid = conn.info.backend_pid
                revoke_lock_started.set()
            return await original_lock(conn, target_id, exclusive=exclusive)

        monkeypatch.setattr(devices, "lock_device_access", lock_device_access)

        async def route(scope, receive, send):
            nonlocal route_entries
            route_entries += 1
            active_entered.set()
            await release_active.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send_active(message):
            active_messages.append(message)

        async def send_later(message):
            later_messages.append(message)

        def scope():
            return _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {token}".encode())])

        active = asyncio.create_task(AuthMiddleware(route)(scope(), receive, send_active))
        revoke = None
        later = None
        try:
            await asyncio.wait_for(active_entered.wait(), timeout=2)
            revoke = asyncio.create_task(
                client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN))
            )
            await asyncio.wait_for(revoke_lock_started.wait(), timeout=2)
            # Observe a real DB lock wait before scheduling more readers; task scheduling
            # alone does not prove that PostgreSQL has queued the exclusive lock.
            async with await connect() as observer:
                async with asyncio.timeout(0.25):
                    while True:
                        cur = await observer.execute(
                            "SELECT cardinality(pg_blocking_pids(%s)) > 0", (revoke_pid,)
                        )
                        if (await cur.fetchone())[0]:
                            break
                        await asyncio.sleep(0.005)

            later = asyncio.create_task(AuthMiddleware(route)(scope(), receive, send_later))
            # Three times the ordinary lock_timeout: the revoke must still be waiting,
            # while a later share locker must fail instead of bypassing the queued revoke.
            await asyncio.sleep(0.15)
            assert not revoke.done(), "revocation used the shorter ordinary lock timeout"
            assert route_entries == 1, "a new share locker bypassed the waiting revocation"
            await asyncio.wait_for(later, timeout=1)
            assert later_messages[0]["status"] == 503
            if finish_normally:
                release_active.set()
            response = await asyncio.wait_for(revoke, timeout=2)
            assert response.status_code == 200, response.text
            assert response.json()["device"]["status"] == "revoked"
            await asyncio.wait_for(active, timeout=1)
            assert active_messages[0]["status"] == (200 if finish_normally else 503)
            rejected = await client.get("/devices/whoami", headers=bearer(token))
            assert rejected.status_code == 401, rejected.text
            assert rejected.json()["code"] == "E_AUTH"
            # Revoke's larger LOCAL timeouts must not persist in the pool.
            async with client.app.state.pool.connection() as conn:
                cur = await conn.execute("SHOW lock_timeout")
                assert await cur.fetchone() == ("50ms",)
        finally:
            release_active.set()
            for task in (active, revoke, later):
                if task is not None:
                    await _finish(task)
