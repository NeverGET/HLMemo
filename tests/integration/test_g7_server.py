"""G7 server round-trip over the MCP wire (PHASE0-SPEC §3, §7; D-021 self-project smoke).

write -> query -> drilldown -> raw through `/mcp`, exactly as a CLI client would drive it.
The read half depends on `hlmemo.core.read_service`; until it lands the two tests xfail
(strict=False) so they turn green automatically once the module is importable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hlmemo.core.budget import Meter, canonical
from hlmemo.server.tools import READ_SERVICE_AVAILABLE
from tests.integration._mcp_fixtures import call_tool_raw, fact, running_app, write_args, writer_on

pytestmark = [
    pytest.mark.integration,
    pytest.mark.xfail(not READ_SERVICE_AVAILABLE, strict=False, reason="read_service pending"),
]

ITEMS = [
    fact("Retry policy", "svc-qx7 retries APP_DB_DSN connections three times with backoff.", tags=["ops"]),
    fact("Error E4193", "E4193 means the pgvector extension is missing on the target database."),
    fact("Deploy note", "The API is deployed with docker compose; migrations run before the listener opens."),
]


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    assert "structuredContent" not in result and len(result["content"]) == 1
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert not result.get("isError"), parsed
    assert text == canonical(parsed)
    assert Meter().count_text(text) == parsed["budget"]["used"] <= parsed["budget"]["limit"]
    return parsed


async def _roundtrip(client, token: str, project: str) -> None:
    ack = _ok(
        await call_tool_raw(client, token, "memory.write", write_args(project, ITEMS, token_budget=2000))
    )
    assert [v["index"] for v in ack["versions"]] == [0, 1, 2]
    version_ids = {v["version_id"] for v in ack["versions"]}

    q = _ok(
        await call_tool_raw(
            client,
            token,
            "memory.query",
            {"project": project, "query": "svc-qx7 APP_DB_DSN", "token_budget": 2000},
        )
    )
    assert q["project"] == project and q["evidence"] == "matched", q
    assert q["hits"], q
    hit_versions = {int(h["clue"][1:].split(".")[0]) for h in q["hits"]}
    assert hit_versions & version_ids, (hit_versions, version_ids)
    top = q["hits"][0]
    assert top["title"] == "Retry policy"

    d = _ok(
        await call_tool_raw(
            client,
            token,
            "memory.drilldown",
            {"project": project, "clue_ids": [top["clue"]], "token_budget": 2000},
        )
    )
    assert len(d["items"]) == 1 and d["items"][0]["clue"] == top["clue"]
    assert "svc-qx7 retries" in d["items"][0]["text"]

    vid = int(top["clue"][1:].split(".")[0])
    raw = _ok(
        await call_tool_raw(
            client, token, "memory.raw", {"project": project, "version_id": vid, "token_budget": 4000}
        )
    )
    assert raw["version_id"] == vid and raw["kind"] == "fact"
    assert project in raw["project_ids"]
    assert raw["payload_item"]["title"] == "Retry policy"
    assert raw["chunks"] and "svc-qx7 retries" in raw["chunks"][0]["text"]
    assert raw["source_event"]["request_id"] == ack["request_id"]


async def test_write_query_drilldown_raw_roundtrip(db_dsn) -> None:
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "g7-roundtrip")
        await _roundtrip(client, token, "g7-roundtrip")


async def test_self_project_smoke(db_dsn) -> None:
    """D-021: project `hlmemo` — write -> query -> drilldown (-> raw) on the self-project."""
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "hlmemo", name="hlmemo-dev")
        await _roundtrip(client, token, "hlmemo")
