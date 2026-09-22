"""An MCP write must never acknowledge a failed outer transaction."""

import json

import psycopg
import pytest

from hlmemo.server.middleware import AuthMiddleware
from tests.integration._mcp_fixtures import (
    call_tool_raw,
    fact,
    mcp_rpc,
    running_app,
    write_args,
    writer_on,
)

pytestmark = pytest.mark.integration


async def test_mcp_commit_failure_rolls_back_before_ack(db_dsn, connect, monkeypatch) -> None:
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "commit-proof")
        args = write_args("commit-proof", [fact("Durable", "Only committed writes are acknowledged.")])
        original_commit = AuthMiddleware.commit_request

        async def fail_commit(self, conn):
            raise psycopg.OperationalError("injected outer commit failure")

        monkeypatch.setattr(AuthMiddleware, "commit_request", fail_commit)
        response = await mcp_rpc(client, token, "tools/call", {"name": "memory.write", "arguments": args})
        assert response.status_code == 503
        assert response.json()["code"] == "E_UNAVAILABLE"
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM events WHERE request_id = %s", (args["request_id"],)
            )
            assert await cur.fetchone() == (0,)
            cur = await conn.execute("SELECT count(*) FROM memory_versions WHERE title = 'Durable'")
            assert await cur.fetchone() == (0,)

        monkeypatch.setattr(AuthMiddleware, "commit_request", original_commit)
        result = await call_tool_raw(client, token, "memory.write", args)
        assert not result.get("isError"), result
        assert json.loads(result["content"][0]["text"])["replayed"] is False
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM events WHERE request_id = %s", (args["request_id"],)
            )
            assert await cur.fetchone() == (1,)
