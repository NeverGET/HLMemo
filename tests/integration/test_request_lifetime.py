"""G-B: untrusted body/network time must never own a DB connection or device lock."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace

import anyio
import pytest
from psycopg.pq import TransactionStatus

from hlmemo.server.middleware import AuthMiddleware
from hlmemo.server.tools import TOOL_BY_NAME
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    bearer,
    mcp_rpc,
    running_app,
    trusted_device,
)

pytestmark = pytest.mark.integration


def _scope(app, method, path, headers=()):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "app": app,
    }


async def _finish(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


@pytest.mark.parametrize("content_length", [None, b"1"])
@pytest.mark.parametrize("size", [8, 9])
async def test_actual_body_byte_limit_precedes_pool_checkout(db_dsn, monkeypatch, content_length, size):
    """Check actual chunks even when Content-Length is absent or underreports the body."""
    monkeypatch.setenv("HLM_REQUEST_MAX_BODY_BYTES", "8")
    async with running_app(db_dsn) as client:
        reads = 0
        checkouts = 0
        routed = False
        messages = []
        chunks = [b"abc", b"x" * (size - 3)]
        original_getconn = client.app.state.pool.getconn

        async def getconn(*args, **kwargs):
            nonlocal checkouts
            checkouts += 1
            assert reads == 2, "pool checkout preceded the final request-body chunk"
            return await original_getconn(*args, **kwargs)

        async def receive():
            nonlocal reads
            chunk = chunks[reads]
            reads += 1
            return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

        async def route(scope, receive, send):
            nonlocal routed
            routed = True
            body = bytearray()
            while True:
                message = await receive()
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            assert bytes(body) == b"".join(chunks)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            messages.append(message)

        monkeypatch.setattr(client.app.state.pool, "getconn", getconn)
        headers = [(b"content-length", content_length)] if content_length is not None else []
        await AuthMiddleware(route)(_scope(client.app, "POST", "/devices/register", headers), receive, send)
        assert messages[0]["status"] == (200 if size == 8 else 413)
        assert routed == (size == 8)
        assert (checkouts > 0) == (size == 8)


@pytest.mark.parametrize("authenticated", [False, True])
async def test_total_body_deadline_never_checks_out_connection(db_dsn, monkeypatch, authenticated):
    """Even ongoing progress cannot extend the separate generous overall body cap."""
    monkeypatch.setenv("HLM_REQUEST_BODY_TIMEOUT_S", "0.1")
    monkeypatch.setenv("HLM_REQUEST_BODY_TOTAL_TIMEOUT_S", "0.06")
    async with running_app(db_dsn) as client:
        messages = []
        reads = 0

        async def getconn(*args, **kwargs):
            pytest.fail("a trickling request acquired a DB connection")

        async def receive():
            nonlocal reads
            await asyncio.sleep(0.02)
            reads += 1
            return {"type": "http.request", "body": b"x", "more_body": reads < 20}

        async def route(scope, receive, send):
            pytest.fail("a body that missed its deadline reached routing")

        async def send(message):
            messages.append(message)

        monkeypatch.setattr(client.app.state.pool, "getconn", getconn)
        headers = [(b"authorization", f"Bearer {ADMIN_TOKEN}".encode())] if authenticated else []
        path = "/mcp" if authenticated else "/devices/register"
        await asyncio.wait_for(
            AuthMiddleware(route)(_scope(client.app, "POST", path, headers), receive, send), timeout=1
        )
        assert messages[0]["status"] == 408
        assert reads < 20


async def test_idle_sse_releases_single_pool_slot_and_revocation_is_effective(db_dsn, monkeypatch):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "idle-stream")
        started = asyncio.Event()
        disconnect = asyncio.Event()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                started.set()

        scope = _scope(
            client.app,
            "GET",
            "/mcp",
            [(b"authorization", f"Bearer {token}".encode()), (b"accept", b"text/event-stream")],
        )
        stream = asyncio.create_task(client.app(scope, receive, send))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert not stream.done()
            assert client.app.state.pool.get_stats()["pool_available"] == 1
            revoke = await asyncio.wait_for(
                client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN)),
                timeout=2,
            )
            assert revoke.status_code == 200, revoke.text
            response = await asyncio.wait_for(mcp_rpc(client, token, "tools/list"), timeout=2)
            assert response.status_code == 401
            assert response.json()["code"] == "E_AUTH"
        finally:
            disconnect.set()
            await _finish(stream)


async def test_stalled_finite_response_releases_connection_before_first_send(db_dsn, monkeypatch, connect):
    """A slow receiver cannot retain the single pool slot after a durable finite response."""
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "slow-receiver")
        entered_send = asyncio.Event()
        release_send = asyncio.Event()
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def route(scope, receive, send):
            await scope["state"]["conn"].execute(
                "INSERT INTO projects (slug, name) VALUES ('slow-response', 'Durable')"
            )
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

        async def send(message):
            entered_send.set()
            await release_send.wait()
            messages.append(message)

        scope = _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {token}".encode())])
        request = asyncio.create_task(AuthMiddleware(route)(scope, receive, send))
        try:
            await asyncio.wait_for(entered_send.wait(), timeout=2)
            assert not request.done()
            async with await connect() as conn:
                row = await conn.execute("SELECT count(*) FROM projects WHERE slug = 'slow-response'")
                assert await row.fetchone() == (1,), "response started before its write was committed"
            revoke = await asyncio.wait_for(
                client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN)),
                timeout=2,
            )
            assert revoke.status_code == 200, revoke.text
            release_send.set()
            await asyncio.wait_for(request, timeout=2)
            assert messages[0]["status"] == 200
            assert json.loads(messages[1]["body"]) == {"ok": True}
        finally:
            release_send.set()
            await _finish(request)


async def test_cancellation_during_pool_return_preserves_connection(db_dsn, monkeypatch):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        returning = asyncio.Event()
        release_return = asyncio.Event()
        returned = []
        messages = []
        pool = client.app.state.pool
        original_putconn = pool.putconn

        async def putconn(conn):
            returning.set()
            await release_return.wait()
            assert conn.info.transaction_status == TransactionStatus.IDLE
            await original_putconn(conn)
            returned.append(conn)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def route(scope, receive, send):
            await scope["state"]["conn"].execute("SELECT 1")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            messages.append(message)

        monkeypatch.setattr(pool, "putconn", putconn)
        scope = _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {ADMIN_TOKEN}".encode())])
        request = asyncio.create_task(AuthMiddleware(route)(scope, receive, send))
        try:
            await asyncio.wait_for(returning.wait(), timeout=2)
            request.cancel("client disconnected")
            # Let cancellation interrupt release while putconn itself remains suspended.
            await asyncio.sleep(0)
            release_return.set()
            with pytest.raises(asyncio.CancelledError, match="client disconnected"):
                await asyncio.wait_for(request, timeout=2)
            assert len(returned) == 1, "the same connection must be returned exactly once"
            assert not messages, "cancelled response was flushed to the disconnected client"
            assert pool.get_stats()["pool_available"] == 1
            async with pool.connection() as conn:
                assert conn.info.transaction_status == TransactionStatus.IDLE
            response = await asyncio.wait_for(
                client.get("/admin/projects", headers=bearer(ADMIN_TOKEN)), timeout=2
            )
            assert response.status_code == 200, response.text
        finally:
            release_return.set()
            await _finish(request)


@pytest.mark.parametrize(
    ("configured", "expected"),
    [((2000, 10000, 5000), ("2s", "10s", "5s")), ((321, 654, 987), ("321ms", "654ms", "987ms"))],
)
async def test_pool_applies_database_timeout_safety_nets(db_dsn, monkeypatch, configured, expected):
    names = (
        ("HLM_DB_LOCK_TIMEOUT_MS", "lock_timeout"),
        ("HLM_DB_STATEMENT_TIMEOUT_MS", "statement_timeout"),
        ("HLM_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "idle_in_transaction_session_timeout"),
    )
    for (env_name, _), milliseconds in zip(names, configured, strict=True):
        monkeypatch.setenv(env_name, str(milliseconds))
    async with running_app(db_dsn) as client:
        async with client.app.state.pool.connection() as conn:
            for (_, setting), expected_value in zip(names, expected, strict=True):
                cur = await conn.execute(f"SHOW {setting}")
                assert await cur.fetchone() == (expected_value,)


async def test_request_db_deadline_rolls_back_and_releases_device_lock(db_dsn, monkeypatch, connect):
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "db-deadline")
        client.app.state.settings.request_db_timeout_s = 0.1
        messages = []
        entered = asyncio.Event()
        never = asyncio.Event()

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def route(scope, receive, send):
            await scope["state"]["conn"].execute(
                "INSERT INTO projects (slug, name) VALUES ('timed-out-route', 'Uncommitted')"
            )
            entered.set()
            await never.wait()

        async def send(message):
            messages.append(message)

        scope = _scope(client.app, "POST", "/mcp", [(b"authorization", f"Bearer {token}".encode())])
        request = asyncio.create_task(AuthMiddleware(route)(scope, receive, send))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(request, timeout=2)
            assert messages[0]["status"] == 503
            assert json.loads(messages[1]["body"])["code"] == "E_UNAVAILABLE"
            async with await connect() as conn:
                cur = await conn.execute("SELECT count(*) FROM projects WHERE slug = 'timed-out-route'")
                assert await cur.fetchone() == (0,)
            # Discarding the aborted connection schedules an asynchronous replacement.
            client.app.state.settings.request_db_timeout_s = 2
            revoke = await asyncio.wait_for(
                client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN)),
                timeout=3,
            )
            assert revoke.status_code == 200, revoke.text
        finally:
            await _finish(request)


async def test_timed_out_mcp_handler_cannot_reuse_connection_after_response(db_dsn, monkeypatch, connect):
    """Session-manager work may outlive its HTTP task; its connection must be retired."""
    monkeypatch.setenv("HLM_POOL_MAX_SIZE", "1")
    async with running_app(db_dsn) as client:
        device_id, token = await trusted_device(client, "late-handler")
        client.app.state.settings.request_db_timeout_s = 0.1
        entered = asyncio.Event()
        finish_handler = asyncio.Event()
        finished = asyncio.Event()
        borrowed = []
        closed_on_finish = []

        async def slow_handler(conn, auth, arguments):
            borrowed.append(conn)
            await conn.execute("INSERT INTO projects (slug, name) VALUES ('late-handler', 'Uncommitted')")
            entered.set()
            # The SDK owns this task. Hold its cleanup past the outer HTTP deadline
            # to expose accidental reuse by an unrelated request in the single-slot pool.
            with anyio.CancelScope(shield=True):
                await finish_handler.wait()
            closed_on_finish.append(conn.closed)
            finished.set()
            return {}

        spec = TOOL_BY_NAME["memory.write"]
        monkeypatch.setitem(TOOL_BY_NAME, "memory.write", replace(spec, handler=slow_handler))
        request = asyncio.create_task(
            mcp_rpc(client, token, "tools/call", {"name": "memory.write", "arguments": {}})
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            response = await asyncio.wait_for(asyncio.shield(request), timeout=2)
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "E_UNAVAILABLE"
            assert borrowed[0].closed, "a detached MCP handler still owns a live connection"
            assert not finished.is_set()
            async with await connect() as conn:
                cur = await conn.execute("SELECT count(*) FROM projects WHERE slug = 'late-handler'")
                assert await cur.fetchone() == (0,)
            # Replacement is asynchronous; prove availability by completing a fresh request.
            client.app.state.settings.request_db_timeout_s = 2
            revoke = await asyncio.wait_for(
                client.post(f"/admin/devices/{device_id}/revoke", json={}, headers=bearer(ADMIN_TOKEN)),
                timeout=3,
            )
            assert revoke.status_code == 200, revoke.text
            finish_handler.set()
            await asyncio.wait_for(finished.wait(), timeout=2)
            assert closed_on_finish == [True]
            healthy = await asyncio.wait_for(
                client.get("/admin/projects", headers=bearer(ADMIN_TOKEN)), timeout=3
            )
            assert healthy.status_code == 200, healthy.text
        finally:
            finish_handler.set()
            await _finish(request)
