"""Final body-budget regression against real HTTP connections (D-041 / D1)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette

from hlmemo.config import get_settings
from hlmemo.server import middleware
from hlmemo.server.app import create_app
from hlmemo.server.middleware import AuthMiddleware
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    MCP_HEADERS,
    bearer,
    running_app,
    write_args,
    writer_on,
)

pytestmark = pytest.mark.integration


async def test_unknown_revoke_bearer_does_not_parse_spooled_body(db_dsn, monkeypatch):
    def reject_parsing(path: str, body: bytes) -> int | None:
        raise AssertionError("unknown bearer must be rejected before parsing the revoke target")

    monkeypatch.setattr(middleware, "_revoke_target", reject_parsing)
    async with running_app(db_dsn) as client:
        threshold = client.app.state.settings.request_body_spool_threshold_bytes
        response = await client.post(
            "/devices/revoke",
            json={"id": 1, "padding": "x" * (threshold + 1)},
            headers=bearer("hlm_" + "z" * 43),
        )
        assert response.status_code == 401, response.text


@asynccontextmanager
async def _real_server(db_dsn: str) -> AsyncIterator[tuple[Starlette, int]]:
    settings = get_settings(
        db_dsn=db_dsn,
        admin_token=ADMIN_TOKEN,
        registration_secret=None,
        trusted_proxy_ips="127.0.0.1/32",
    )
    app = create_app(settings, register_rate_limit=None)
    # Keep the ephemeral port bound until uvicorn takes ownership: no free-port race.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="warning",
                lifespan="on",
                proxy_headers=False,
                timeout_graceful_shutdown=5,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("HTTP server exited before startup")
                    await asyncio.sleep(0.01)
            yield app, listener.getsockname()[1]
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, 10)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def _auth_middleware(app: Starlette) -> AuthMiddleware:
    middleware = app.middleware_stack
    while middleware is not None:
        if isinstance(middleware, AuthMiddleware):
            return middleware
        middleware = getattr(middleware, "app", None)
    raise AssertionError("AuthMiddleware not found")


async def _trickle(port: int, client: int) -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        headers = (
            f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Authorization: Bearer x\r\nContent-Type: application/json\r\n"
            "Accept: application/json, text/event-stream\r\n"
            f"X-Forwarded-For: 203.0.113.{client + 1}\r\n"
            f"Content-Length: {4 * 1024 * 1024}\r\n\r\n"
        )
        writer.write(headers.encode())
        # Declare a large body, send one byte and leave the rest pending. The
        # server must reject from the headers without waiting for the next byte.
        writer.write(b"a")
        await writer.drain()
        async with asyncio.timeout(5):
            status = await reader.readline()
        return int(status.split()[1])
    finally:
        writer.close()
        with suppress(ConnectionError, OSError):
            await writer.wait_closed()


async def test_declared_length_tricklers_do_not_block_real_38mb_write(db_dsn):
    """70 invalid uploads get immediate 401s; a trusted 38.4 MB write still succeeds."""
    async with _real_server(db_dsn) as (app, port):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
            token = await writer_on(client, "final-body", "final-body-writer")
            budget = _auth_middleware(app).body_budget
            assert budget is not None
            assert budget.used == 0
            tasks = [asyncio.create_task(_trickle(port, i)) for i in range(70)]
            try:
                assert await asyncio.gather(*tasks) == [401] * 70
                assert budget.used == 0
                assert budget.clients == {}

                items = [{"kind": "fact", "title": f"Max {i}", "body": "😀" * 64000} for i in range(50)]
                wire = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "memory.write",
                            "arguments": write_args("final-body", items, token_budget=32000),
                        },
                    },
                    ensure_ascii=True,
                ).encode()
                assert 38_400_000 <= len(wire) < 38_410_000
                response = await client.post("/mcp", content=wire, headers={**MCP_HEADERS, **bearer(token)})
                assert response.status_code == 200, response.text[:300]
                result = response.json()["result"]
                assert not result.get("isError"), result
                assert len(json.loads(result["content"][0]["text"])["versions"]) == 50
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                async with asyncio.timeout(5):
                    while budget.used:  # noqa: ASYNC110 - observe server-side disconnect cleanup
                        await asyncio.sleep(0.01)
            assert budget.clients == {}
