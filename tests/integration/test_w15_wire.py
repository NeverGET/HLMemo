"""``hlm import`` / ``hlm export`` over the real MCP wire (ASGI app + the official SDK client).

``hlm.export`` is dispatched but never advertised (CC-4): ``tools/list`` still has the five agent
tools, while the CLI's session (``MemoryClient.session``) calls it and ``memory.write`` with the
W1.5 item fields through the same transport the CLI uses.
"""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest
from mcp.client.streamable_http import streamable_http_client

from hlmemo.cli.mcp_client import MemoryClient
from hlmemo.core.budget import Meter
from hlmemo.importers import exportfmt
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.importers.runner import run_export
from tests.integration._import_fixtures import copy_fixture, scalar
from tests.integration._mcp_fixtures import bearer, running_app, sdk_client, writer_on

pytestmark = pytest.mark.integration


async def test_export_tool_is_unlisted_but_callable_and_the_cli_session_imports(
    db_dsn, connect, tmp_path
) -> None:
    root = copy_fixture(tmp_path)
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "wire-fx")
        async with sdk_client(client.app, token) as sdk:
            listed = await sdk.list_tools()
            assert sorted(t.name for t in listed.tools) == [
                "memory.call_the_day",
                "memory.drilldown",
                "memory.query",
                "memory.raw",
                "memory.register_lesson",
                "memory.risk_check",
                "memory.write",
            ]
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://test", headers=bearer(token)
        )
        async with http:
            memory = MemoryClient(
                "http://test/mcp",
                token,
                timeout_s=30,
                target=streamable_http_client("http://test/mcp", http_client=http),
            )
            async with memory.session() as call:
                parsed = parse_source("markdown", [root / "docs"], base=root, tz=UTC)
                rep = await import_async(
                    call, source="markdown", parsed=parsed, project="wire-fx", dry_run=False, meter=Meter()
                )
                assert rep["writes"]["written"] == 9 and not rep["writes"]["failed"]
                out = await run_export(call, "wire-fx", tmp_path / "export")
    assert out["items"] == 10 and (tmp_path / "export" / "CARD.md").is_file()
    texts = [p.read_text() for p in (tmp_path / "export").rglob("*.md")]
    assert sum(1 for t in texts if t.startswith("---\nhlm_export: 1\n")) == 10
    assert any(exportfmt.is_index(t) for t in texts)
    assert await scalar(connect, "SELECT count(*) FROM memory_versions WHERE source IS NOT NULL") == 9
