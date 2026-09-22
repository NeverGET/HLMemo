"""N1: persistent MCP SSE streams, while finite acknowledgements wait for durability."""

from __future__ import annotations

import asyncio
import contextlib
import json

import psycopg
import pytest
from sse_starlette import EventSourceResponse

from hlmemo.server.middleware import AuthMiddleware
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    fact,
    running_app,
    write_args,
    writer_on,
)

pytestmark = pytest.mark.integration


def _scope(method, path, headers):
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
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }


@pytest.mark.parametrize("fail_commit", [False, True])
async def test_mcp_sse_commits_before_headers_and_streams(db_dsn, monkeypatch, fail_commit):
    """Drive the real GET transport; HTTPX ASGITransport itself buffers indefinite bodies."""
    async with running_app(db_dsn) as client:
        entered, release, disconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()
        messages = asyncio.Queue()
        commits = 0
        committed = False
        request_received = False
        original_commit = AuthMiddleware.commit_request

        async def commit(self, conn):
            nonlocal commits, committed
            commits += 1
            entered.set()
            await release.wait()
            if fail_commit:
                raise psycopg.OperationalError("injected SSE commit failure")
            await original_commit(self, conn)
            committed = True

        async def receive():
            nonlocal request_received
            if not request_received:
                request_received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start" and message["status"] == 200:
                assert committed
            messages.put_nowait(message)

        monkeypatch.setattr(AuthMiddleware, "commit_request", commit)
        monkeypatch.setattr(EventSourceResponse, "DEFAULT_PING_INTERVAL", 0.01)
        scope = _scope(
            "GET",
            "/mcp",
            [(b"authorization", f"Bearer {ADMIN_TOKEN}".encode()), (b"accept", b"text/event-stream")],
        )  # Deliberately no MCP-Protocol-Version: this reaches the persistent SSE path.
        task = asyncio.create_task(client.app(scope, receive, send))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            # Let the independent ping sender race a deliberately blocked commit.
            await asyncio.sleep(0.05)
            assert messages.empty()
            release.set()
            start = await asyncio.wait_for(messages.get(), timeout=2)
            assert start["type"] == "http.response.start"
            assert start["status"] == (503 if fail_commit else 200)
            body = await asyncio.wait_for(messages.get(), timeout=2)
            assert body["type"] == "http.response.body"
            if fail_commit:
                assert json.loads(body["body"])["code"] == "E_UNAVAILABLE"
                await asyncio.wait_for(task, timeout=2)
                assert messages.empty()
            else:
                assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
                assert b"ping" in body["body"] and body["more_body"]
                assert not task.done(), "headers/body must arrive while the SSE channel is still open"
            assert commits == 1
        finally:
            release.set()
            disconnect.set()
            try:
                await asyncio.wait_for(task, timeout=2)
            finally:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task


async def test_mcp_post_is_durable_when_first_ack_message_is_sent(db_dsn, connect):
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "finite-ack")
        args = write_args("finite-ack", [fact("Durable ack", "Committed before headers")])
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory.write", "arguments": args},
            }
        ).encode()
        messages = []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            if not messages:
                async with await connect() as conn:
                    row = await conn.execute(
                        "SELECT result FROM events WHERE request_id = %s", (args["request_id"],)
                    )
                    assert (await row.fetchone())[0] is not None
            messages.append(message)

        await client.app(
            _scope(
                "POST",
                "/mcp",
                [
                    (b"authorization", f"Bearer {token}".encode()),
                    (b"accept", b"application/json, text/event-stream"),
                    (b"content-type", b"application/json"),
                ],
            ),
            receive,
            send,
        )
        assert messages[0]["status"] == 200
        result = json.loads(b"".join(m.get("body", b"") for m in messages))["result"]
        assert not result.get("isError")
        assert json.loads(result["content"][0]["text"])["replayed"] is False


async def test_chunked_json_without_content_length_still_waits_for_commit(db_dsn, connect):
    async with running_app(db_dsn) as client:
        sent = []

        async def route(scope, receive, send):
            await scope["state"]["conn"].execute(
                "INSERT INTO projects (slug, name) VALUES ('chunked-ack', 'Chunked')"
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"ok":', "more_body": True})
            assert not sent
            await send({"type": "http.response.body", "body": b"true}", "more_body": False})
            assert not sent

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            async with await connect() as conn:
                cur = await conn.execute("SELECT count(*) FROM projects WHERE slug = 'chunked-ack'")
                assert await cur.fetchone() == (1,)
            sent.append(message)

        scope = _scope("POST", "/devices/register", [])
        scope["app"] = client.app
        await AuthMiddleware(route)(scope, receive, send)
        assert len(sent) == 3
        assert json.loads(b"".join(m.get("body", b"") for m in sent)) == {"ok": True}
