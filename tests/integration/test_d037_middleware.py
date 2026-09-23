"""D-037: reserved revocation capacity, REST connection reuse and proxy registration."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from hlmemo.auth.errors import HlmError
from hlmemo.server.middleware import AuthMiddleware, RateLimiter
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, running_app, trusted_device
from tests.integration.test_request_lifetime import _scope

pytestmark = pytest.mark.integration


async def test_revoke_succeeds_ten_of_ten_under_device_request_flood(db_dsn, monkeypatch):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "2")
    monkeypatch.setenv("HLM_POOL_TIMEOUT_S", "0.2")
    monkeypatch.setenv("HLM_REQUEST_DB_TIMEOUT_S", "0.5")
    async with running_app(db_dsn) as client:
        successes = 0
        for attempt in range(10):
            device_id, token = await trusted_device(client, f"flood-{attempt}")
            occupied = asyncio.Event()
            active = 0

            async def route(scope, receive, send, occupied=occupied):
                nonlocal active
                active += 1
                if active == 2:
                    occupied.set()
                await scope["state"]["conn"].execute("SELECT pg_sleep(0.08)")
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            async def flood_request(token=token):
                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(message):
                    pass

                scope = _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {token}".encode())])
                await AuthMiddleware(route)(scope, receive, send)

            tasks = [asyncio.create_task(flood_request()) for _ in range(64)]
            try:
                await asyncio.wait_for(occupied.wait(), timeout=2)
                assert client.app.state.pool.get_stats()["requests_waiting"] >= 32
                # Both admin-token spellings retain reserved revocation capacity.
                path = f"/admin/devices/{device_id}/revoke" if attempt % 2 == 0 else "/devices/revoke"
                response = await asyncio.wait_for(
                    client.post(path, json={"id": device_id}, headers=bearer(ADMIN_TOKEN)), timeout=2
                )
                assert response.status_code == 200, (attempt, response.text)
                assert response.json()["device"]["status"] == "revoked"
                successes += 1
            finally:
                await asyncio.gather(*tasks)
        assert successes == 10


@pytest.mark.parametrize("alias", [False, True], ids=["admin-path", "body-target"])
async def test_self_revoke_saturated_gate_is_retryable_then_succeeds(db_dsn, monkeypatch, alias):
    """Self-revoke uses the short normal admission gate, then retries after capacity frees."""
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "2")
    monkeypatch.setenv("HLM_POOL_TIMEOUT_S", "0.05")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "saturated-self-revoke")
        path = "/devices/revoke" if alias else f"/admin/devices/{device_id}/revoke"
        pool = client.app.state.pool
        before_reserved = client.app.state.admin_pool.get_stats()["requests_num"]
        async with pool.connection(), pool.connection():
            response = await asyncio.wait_for(
                client.post(path, json={"id": device_id}, headers=bearer(token)), timeout=1
            )
            assert response.status_code == 503, response.text
            assert response.json()["retryable"] is True
            assert response.json()["code"] == "E_UNAVAILABLE"
            assert "pre-body authentication" in response.json()["message"]
        assert client.app.state.admin_pool.get_stats()["requests_num"] == before_reserved
        response = await client.post(path, json={"id": device_id}, headers=bearer(token))
        assert response.status_code == 200, response.text
        assert response.json()["device"]["status"] == "revoked"
        assert client.app.state.admin_pool.get_stats()["requests_num"] == before_reserved


@pytest.mark.parametrize("failure", ["domain", "timeout", "cancel"])
async def test_rest_failures_rollback_without_discarding_connection(db_dsn, monkeypatch, failure):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        pool = client.app.state.pool
        async with pool.connection() as conn:
            original_pid = conn.info.backend_pid
        returned = []
        original_putconn = pool.putconn

        async def putconn(conn):
            returned.append((conn.closed, conn.info.backend_pid))
            await original_putconn(conn)

        monkeypatch.setattr(pool, "putconn", putconn)
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def route(scope, receive, send):
            await scope["state"]["conn"].execute("SELECT 1")
            if failure == "domain":
                raise HlmError("E_INVALID_ARG", "bad REST argument")
            if failure == "timeout":
                raise TimeoutError()
            raise asyncio.CancelledError()

        async def send(message):
            messages.append(message)

        scope = _scope(
            client.app, "POST", "/devices/grant", [(b"authorization", f"Bearer {ADMIN_TOKEN}".encode())]
        )
        request = AuthMiddleware(route)(scope, receive, send)
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            await request
            assert messages[0]["status"] == (400 if failure == "domain" else 503)
        # Admission and authoritative REST transaction each return the same live
        # pooled connection; neither domain failures nor cancellation discard it.
        assert returned == [(False, original_pid), (False, original_pid)]
        async with pool.connection() as conn:
            assert conn.info.backend_pid == original_pid


async def test_registration_limiter_distinguishes_clients_only_behind_trusted_proxy(db_dsn):
    async with running_app(db_dsn) as client:
        client.app.state.register_limiter = RateLimiter(2)
        client.app.state.settings.trusted_proxy_ips = "172.18.0.0/16"

        async def register(proxy, forwarded):
            transport = httpx.ASGITransport(app=client.app, client=(proxy, 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as visitor:
                return await visitor.post(
                    "/devices/register",
                    json={
                        "name": f"proxy-{uuid.uuid4()}",
                        "fingerprint": str(uuid.uuid4()),
                        "client": "test",
                    },
                    headers={"X-Forwarded-For": forwarded},
                )

        for _ in range(2):
            assert (await register("172.18.0.5", "203.0.113.66")).status_code == 201
        assert (await register("172.18.0.5", "203.0.113.66")).status_code == 429
        assert (await register("172.18.0.5", "198.51.100.7")).status_code == 201
        for forwarded in ("192.0.2.1", "192.0.2.2"):
            assert (await register("203.0.113.88", forwarded)).status_code == 201
        assert (await register("203.0.113.88", "192.0.2.3")).status_code == 429
