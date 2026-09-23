"""Pre-body admission is short, bounded and never replaces transaction authorization."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout

from hlmemo.auth.tokens import hash_token
from tests.integration._mcp_fixtures import ADMIN_TOKEN, bearer, register, running_app, trusted_device
from tests.integration.test_final_body import _auth_middleware

pytestmark = pytest.mark.integration


async def _request(app, receive, *, token=None, path="/mcp", method="POST", headers=()):
    messages = []
    raw_headers = [(b"content-type", b"application/json"), *headers]
    if token is not None:
        raw_headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("203.0.113.50", 12345),
        "server": ("test", 80),
    }

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


@pytest.mark.parametrize("identity", ["missing", "unknown", "pending", "revoked"])
async def test_burst_then_trickle_without_trusted_bearer_never_reads_body(db_dsn, connect, identity):
    async with running_app(db_dsn) as client:
        await client.get("/health")
        token = None if identity == "missing" else "hlm_" + "z" * 43
        if identity in ("pending", "revoked"):
            did, token = await register(client, f"gate-{identity}")
            if identity == "revoked":
                async with await connect() as conn:
                    await conn.execute("UPDATE devices SET status = 'revoked' WHERE device_id = %s", (did,))
                    await conn.commit()
        middleware = _auth_middleware(client.app)
        receives = 0

        async def burst_then_trickle():
            nonlocal receives
            receives += 1
            if receives == 1:
                return {"type": "http.request", "body": b"x" * (64 * 1024 * 1024), "more_body": True}
            await asyncio.sleep(29)
            return {"type": "http.request", "body": b"x", "more_body": True}

        async with asyncio.timeout(3):
            responses = await asyncio.gather(
                *(_request(client.app, burst_then_trickle, token=token) for _ in range(4))
            )
        assert [response[0]["status"] for response in responses] == [
            403 if identity == "pending" else 401
        ] * 4
        assert receives == 0
        assert middleware.body_budget.used == 0
        assert middleware.body_budget.clients == {}
        assert middleware.body_readers == {}


async def test_gate_one_short_select_releases_connection_before_body(db_dsn, monkeypatch):
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(client, "gate-instrumented")
        pool = client.app.state.pool
        original_connection = pool.connection
        leases = []
        active = 0

        class RecordingConnection:
            def __init__(self, conn, lease):
                self.conn = conn
                self.lease = lease

            def __getattr__(self, name):
                return getattr(self.conn, name)

            async def execute(self, query, params=None, **kwargs):
                self.lease["queries"].append((query, params))
                return await self.conn.execute(query, params, **kwargs)

        @asynccontextmanager
        async def instrumented_connection(*args, **kwargs):
            nonlocal active
            lease = {"timeout": kwargs.get("timeout"), "queries": []}
            leases.append(lease)
            async with original_connection(*args, **kwargs) as conn:
                active += 1
                try:
                    yield RecordingConnection(conn, lease)
                finally:
                    active -= 1
            lease["transaction_status"] = conn.info.transaction_status

        monkeypatch.setattr(pool, "connection", instrumented_connection)
        receives = 0

        async def receive():
            nonlocal receives
            receives += 1
            assert active == 0, "pre-body gate must return its lease before reading"
            assert len(leases) == 1, "only the short admission lease may run before the body"
            lease = leases[0]
            assert 0 < lease["timeout"] <= 0.25
            assert lease["transaction_status"] == TransactionStatus.IDLE
            selects = [
                (query, params)
                for query, params in lease["queries"]
                if query.lstrip().upper().startswith("SELECT")
            ]
            assert len(selects) == 1
            query, params = selects[0]
            assert all(
                column in query.lower()
                for column in ("device_id", "status", "token_generation", "token_sha256")
            )
            assert "FOR " not in query.upper()
            assert "LOCK" not in query.upper()
            assert params == (hash_token(token),)
            assert any("statement_timeout" in query and "250ms" in query for query, _ in lease["queries"])
            assert client.app.state.pool.get_stats()["pool_available"] >= 1
            return {"type": "http.request", "body": b"{}", "more_body": False}

        response = await _request(client.app, receive, token=token, path="/unknown-gate-test")
        assert response[0]["status"] == 404
        assert receives == 1
        assert len(leases) == 2, "the authoritative request transaction acquires its own later lease"
        assert active == 0


async def test_revoke_between_gate_and_transaction_is_rejected(db_dsn, connect, monkeypatch):
    async with running_app(db_dsn) as client:
        did, token = await trusted_device(client, "gate-revoked-during-body")
        from hlmemo.server import middleware

        original_resolve = middleware.resolve
        resolves = []

        async def recording_resolve(*args, **kwargs):
            resolves.append(args[1])
            return await original_resolve(*args, **kwargs)

        monkeypatch.setattr(middleware, "resolve", recording_resolve)

        async def receive():
            # A row lock in the admission lease would block this update. The final
            # transaction must re-read the revoked status after the body completes.
            async with await connect() as conn:
                await conn.execute("SET LOCAL lock_timeout = '250ms'")
                await conn.execute("UPDATE devices SET status = 'revoked' WHERE device_id = %s", (did,))
                await conn.commit()
            return {"type": "http.request", "body": b"{}", "more_body": False}

        response = await _request(client.app, receive, token=token)
        assert response[0]["status"] == 401
        assert resolves == [token]
        assert b"revoked" in response[1]["body"]
        assert _auth_middleware(client.app).body_budget.used == 0


async def test_saturated_gate_is_retryable_and_admin_uses_reserved_pool(db_dsn, monkeypatch):
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(client, "gate-saturated")
        calls = []

        @asynccontextmanager
        async def saturated_connection(*args, **kwargs):
            calls.append(kwargs)
            raise PoolTimeout("normal pool saturated")
            yield  # pragma: no cover - context manager protocol

        monkeypatch.setattr(client.app.state.pool, "connection", saturated_connection)

        async def receive():
            raise AssertionError("saturated gate must reject before reading body")

        response = await _request(client.app, receive, token=token)
        assert response[0]["status"] == 503
        assert json.loads(response[1]["body"])["retryable"] is True
        assert len(calls) == 1
        assert 0 < calls[0]["timeout"] <= 0.25
        admin = await client.post(
            "/admin/projects", json={"slug": "gate-admin", "name": "Gate admin"}, headers=bearer(ADMIN_TOKEN)
        )
        assert admin.status_code == 201, admin.text
        assert len(calls) == 1, "reserved admin request must never acquire the saturated normal pool"
        assert _auth_middleware(client.app).body_budget.used == 0


async def test_gate_waiters_share_per_client_concurrency_cap(db_dsn, monkeypatch):
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(client, "gate-concurrent")
        started = 0
        all_started = asyncio.Event()
        blocked = asyncio.Event()

        @asynccontextmanager
        async def blocked_connection(*args, **kwargs):
            nonlocal started
            started += 1
            if started == 16:
                all_started.set()
            await blocked.wait()
            raise PoolTimeout("normal pool saturated")
            yield  # pragma: no cover - context manager protocol

        monkeypatch.setattr(client.app.state.pool, "connection", blocked_connection)

        async def receive():
            raise AssertionError("waiting admission requests cannot consume a body")

        tasks = [asyncio.create_task(_request(client.app, receive, token=token)) for _ in range(16)]
        try:
            async with asyncio.timeout(3):
                await all_started.wait()
                response = await _request(client.app, receive, token=token)
            assert response[0]["status"] == 429
            assert started == 16

            async def admin_receive():
                return {
                    "type": "http.request",
                    "body": b'{"slug":"same-peer-admin","name":"Same peer admin"}',
                    "more_body": False,
                }

            # The exact same ASGI peer must retain bounded reserved admin
            # admission even while its normal gate slots are all occupied.
            admin_response = await _request(
                client.app, admin_receive, token=ADMIN_TOKEN, path="/admin/projects"
            )
            assert admin_response[0]["status"] == 201
            assert started == 16, "reserved admin must not touch the blocked normal gate"
            assert _auth_middleware(client.app).body_budget.used == 0
        finally:
            blocked.set()
            await asyncio.gather(*tasks)
        assert _auth_middleware(client.app).body_readers == {}


async def test_trusted_slow_link_outlives_idle_window_with_progress(db_dsn):
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(client, "gate-slow-link")
        client.app.state.settings.request_body_timeout_s = 0.15
        chunks = iter([(b"x" * 4096, True)] * 9 + [(b"end", False)])
        receives = 0

        async def receive():
            nonlocal receives
            await asyncio.sleep(0.04)
            receives += 1
            body, more = next(chunks)
            return {"type": "http.request", "body": body, "more_body": more}

        response = await _request(client.app, receive, token=token, path="/slow-link-gate-test")
        assert response[0]["status"] == 404
        assert receives == 10
        assert _auth_middleware(client.app).body_budget.used == 0


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize(
    ("path", "method", "identity"),
    [
        ("/health", "GET", "missing"),
        ("/ready", "GET", "missing"),
        ("/admin/projects", "POST", "admin"),
        ("/health", "GET", "pending"),
        ("/health", "GET", "unknown"),
        ("/ready", "GET", "unknown"),
    ],
)
async def test_public_and_reserved_admin_bodies_have_64kib_cap(db_dsn, declared, path, method, identity):
    async with running_app(db_dsn) as client:
        token = {"missing": None, "admin": ADMIN_TOKEN, "unknown": "hlm_" + "z" * 43}.get(identity)
        if identity == "pending":
            _did, token = await register(client, "public-cap-pending")
        receives = 0

        async def receive():
            nonlocal receives
            receives += 1
            return {"type": "http.request", "body": b"x" * 65537, "more_body": False}

        response = await _request(
            client.app,
            receive,
            token=token,
            path=path,
            method=method,
            headers=[(b"content-length", b"65537")] if declared else (),
        )
        assert response[0]["status"] == 413
        assert receives == (0 if declared else 1)
        assert _auth_middleware(client.app).body_budget.used == 0
        assert _auth_middleware(client.app).body_budget.clients == {}
