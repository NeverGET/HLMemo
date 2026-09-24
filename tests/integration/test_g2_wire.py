"""G2 wire format (PHASE0-SPEC §3, D-024 (6)): one TextContent, no structuredContent, inputSchema
only, metered bytes == wire bytes, error envelope.

Raw JSON-RPC over `httpx.ASGITransport` asserts the exact bytes; the official `mcp` client SDK
(`Client` over `streamable_http_client`) confirms the protocol round-trip.

The app (with the MCP session manager's anyio task group) is entered inside each test body, not
in an async fixture: pytest-asyncio may tear a fixture down in another task, which anyio's cancel
scopes refuse.
"""

from __future__ import annotations

import contextlib
import json
import random
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from hlmemo.core.budget import BUDGET_MAX, BUDGET_MIN, Meter, canonical
from hlmemo.server.tools import READ_SERVICE_AVAILABLE, TOOL_BY_NAME, TOOL_NAMES
from tests.integration._mcp_fixtures import (
    bearer,
    call_tool_raw,
    fact,
    mcp_rpc,
    register,
    running_app,
    sdk_client,
    trusted_device,
    write_args,
    writer_on,
)

pytestmark = pytest.mark.integration

PROJECT = "g2-wire"
FIVE = {"memory.query", "memory.drilldown", "memory.raw", "memory.write", "memory.call_the_day"}
W2D = {"memory.risk_check", "memory.register_lesson"}  # W2d (CC-4); the name FIVE is kept for the diff
FIVE = FIVE | W2D
ADVERTISED = FIVE | {"memory.answer"}  # W2c (CC-4: 9 tools after Phase 4)
ENVELOPE_KEYS = {"code", "message", "retryable", "details"}
READ_TOOLS = ("memory.query", "memory.drilldown", "memory.raw")


@contextlib.asynccontextmanager
async def wired(db_dsn: str) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Running app + a trusted device holding `write` on PROJECT (its token)."""
    async with running_app(db_dsn) as client:
        token = await writer_on(client, PROJECT)
        yield client, token


def _single_text(result: dict[str, Any]) -> dict[str, Any]:
    """D-024 (6) wire assertions; returns the parsed text block."""
    assert "structuredContent" not in result, result
    assert len(result["content"]) == 1, result
    block = result["content"][0]
    assert block["type"] == "text"
    parsed = json.loads(block["text"])
    assert block["text"] == canonical(parsed), "wire text must be the canonical compact serialisation"
    return parsed


async def _write_one(client, token: str, title: str = "Wire fact") -> dict[str, Any]:
    result = await call_tool_raw(
        client, token, "memory.write", write_args(PROJECT, [fact(title, "svc-qx7 reads APP_DB_DSN at boot.")])
    )
    assert not result.get("isError"), result
    return _single_text(result)


# --------------------------------------------------------------------------- tools/list


@pytest.mark.parametrize("tool", ["memory.write", "memory.call_the_day"])
async def test_verbatim_idempotency_over_mcp(db_dsn, connect, tool: str) -> None:
    """Defaults and nulls stay distinguishable across transport, hashing and persisted evidence."""
    async with wired(db_dsn) as (client, token):
        if tool == "memory.write":
            args = write_args(PROJECT, [fact("Verbatim", "Exact request evidence.")])
        else:
            args = {
                "project": PROJECT,
                "request_id": str(uuid.uuid4()),
                "session_id": str(uuid.uuid4()),
                "client": "pytest/0",
                "notes": "Exact close request evidence.",
            }
        original = await call_tool_raw(client, token, tool, args)
        assert not original.get("isError"), original
        replay = await call_tool_raw(client, token, tool, args)
        assert _single_text(replay)["replayed"] is True
        changed = {**args, "token_budget": 2000}
        conflict = await call_tool_raw(client, token, tool, changed)
        assert conflict.get("isError") is True
        assert _single_text(conflict)["code"] == "E_REQUEST_ID_CONFLICT"
        if tool == "memory.write":
            changed = {**args, "items": [{**args["items"][0], "valid_to": None}]}
            conflict = await call_tool_raw(client, token, tool, changed)
            assert _single_text(conflict)["code"] == "E_REQUEST_ID_CONFLICT"
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT payload->'request', payload_sha256 FROM events WHERE request_id = %s",
                (args["request_id"],),
            )
            request, digest = await cur.fetchone()
            from hlmemo.core.write_service import payload_sha256

            assert request == args
            assert digest == payload_sha256(args)


async def test_tools_list_has_five_tools_no_output_schema(db_dsn) -> None:
    async with wired(db_dsn) as (client, token):
        r = await mcp_rpc(client, token, "tools/list")
        assert r.status_code == 200, r.text
        tools = r.json()["result"]["tools"]
        assert {t["name"] for t in tools} == ADVERTISED == set(TOOL_NAMES)
        for t in tools:
            assert "outputSchema" not in t, t["name"]
            assert t["inputSchema"] == TOOL_BY_NAME[t["name"]].input_schema
            assert t["inputSchema"]["additionalProperties"] is False
            assert t["description"]
        # required inputs per §3
        req = {t["name"]: set(t["inputSchema"]["required"]) for t in tools}
        assert req["memory.query"] == {"project", "query", "token_budget"}
        assert req["memory.drilldown"] == {"project", "clue_ids", "token_budget"}
        assert req["memory.raw"] == {"project", "version_id", "token_budget"}
        assert req["memory.write"] == {"project", "request_id", "client", "items"}
        assert req["memory.call_the_day"] == {"project", "request_id", "session_id", "client", "notes"}
        assert req["memory.risk_check"] == {"project", "task", "token_budget"}
        assert req["memory.register_lesson"] == {"project", "request_id", "mistake", "fix"}
        assert req["memory.answer"] == {"project", "request_id", "question_id", "decision"}

        async with sdk_client(client.app, token) as sdk:  # type: ignore[attr-defined]
            listed = await sdk.list_tools()
            assert {t.name for t in listed.tools} == ADVERTISED
            assert all(t.output_schema is None for t in listed.tools)


# --------------------------------------------------------------------------- one text block


def _success_args(tool: str, seed: dict[str, Any]) -> dict[str, Any]:
    if tool == "memory.write":
        return write_args(PROJECT, [fact("Success fact", "svc-qx7 reads APP_DB_DSN and reports E4193.")])
    if tool == "memory.call_the_day":
        return {
            "project": PROJECT,
            "request_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "client": "pytest/0",
            "notes": "Session summary: wired the MCP server.",
            "decisions": ["D-024 wire rule applied"],
        }
    if tool == "memory.query":
        return {"project": PROJECT, "query": "svc-qx7 APP_DB_DSN", "token_budget": 2000}
    if tool == "memory.drilldown":
        return {"project": PROJECT, "clue_ids": [f"v{seed['version_id']}"], "token_budget": 2000}
    if tool == "memory.raw":
        return {"project": PROJECT, "version_id": seed["version_id"], "token_budget": 4000}
    if tool == "memory.risk_check":
        return {"project": PROJECT, "task": "rotate APP_DB_DSN on svc-qx7", "token_budget": 2000}
    if tool == "memory.register_lesson":
        return {"project": PROJECT, "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"}
    raise AssertionError(tool)


def _error_args(tool: str) -> tuple[dict[str, Any], str]:
    if tool == "memory.write":
        return write_args("no-such-project", [fact("x", "y")]), "E_FORBIDDEN_PROJECT"
    if tool == "memory.call_the_day":
        return (
            {
                "project": "no-such-project",
                "request_id": str(uuid.uuid4()),
                "session_id": str(uuid.uuid4()),
                "client": "pytest/0",
                "notes": "n",
            },
            "E_FORBIDDEN_PROJECT",
        )
    if tool == "memory.query":
        return {"project": PROJECT, "query": "q", "token_budget": 100}, "E_BUDGET_TOO_SMALL"
    if tool == "memory.drilldown":
        return {"project": PROJECT, "clue_ids": ["v1"], "token_budget": 100}, "E_BUDGET_TOO_SMALL"
    if tool == "memory.raw":
        return {"project": PROJECT, "version_id": 1, "token_budget": 100000}, "E_BUDGET_TOO_LARGE"
    if tool == "memory.risk_check":
        return {"project": PROJECT, "task": "t", "token_budget": 100}, "E_BUDGET_TOO_SMALL"
    if tool == "memory.register_lesson":
        return (
            {"project": "no-such-project", "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"},
            "E_FORBIDDEN_PROJECT",
        )
    raise AssertionError(tool)


@pytest.mark.parametrize("tool", sorted(FIVE))
async def test_single_text_block_no_structured_content(db_dsn, tool: str) -> None:
    async with wired(db_dsn) as (client, token):
        ack = await _write_one(client, token)
        seed = {"version_id": ack["versions"][0]["version_id"]}

        # error path: always a result (never a JSON-RPC error), isError=true, one text block
        args, expected_code = _error_args(tool)
        result = await call_tool_raw(client, token, tool, args)
        assert result["isError"] is True, result
        env = _single_text(result)
        assert set(env) == ENVELOPE_KEYS and env["code"] == expected_code, env

        # success path
        result = await call_tool_raw(client, token, tool, _success_args(tool, seed))
        parsed = _single_text(result)
        if result.get("isError"):
            if tool in READ_TOOLS and not READ_SERVICE_AVAILABLE and parsed["code"] == "E_UNAVAILABLE":
                pytest.xfail("read_service pending")
            raise AssertionError(f"{tool} failed: {parsed}")
        assert "isError" not in result or result["isError"] is False
        budget = parsed["budget"]
        assert budget["tokenizer"] == "o200k_base" and budget["used"] <= budget["limit"]
        assert Meter().count_text(result["content"][0]["text"]) == budget["used"]


# --------------------------------------------------------------------------- metering


async def test_metered_text_le_budget(db_dsn) -> None:
    """Meter recount of the exact wire text <= budget (and == budget.used) for 200 random budgets."""
    meter = Meter()
    rng = random.Random(20260922)
    budgets = [BUDGET_MIN, BUDGET_MAX, *(rng.randint(BUDGET_MIN, BUDGET_MAX) for _ in range(198))]
    seen_ok = seen_too_small = 0
    async with wired(db_dsn) as (client, token):
        for i, budget in enumerate(budgets):
            n_items = 1 + (i % 3)
            items = [
                fact(f"Metered {i}.{j}", f"svc-qx7 wrote item {i}.{j} to APP_DB_DSN.") for j in range(n_items)
            ]
            args = write_args(PROJECT, items, token_budget=budget)
            result = await call_tool_raw(client, token, "memory.write", args)
            text = result["content"][0]["text"]
            parsed = _single_text(result)
            if result.get("isError"):
                assert parsed["code"] == "E_BUDGET_TOO_SMALL" and parsed["details"]["min"] > budget, parsed
                seen_too_small += 1
                continue
            counted = meter.count_text(text)
            assert counted <= budget, (budget, counted)
            assert parsed["budget"] == {"limit": budget, "used": counted, "tokenizer": "o200k_base"}
            assert len(parsed["versions"]) == n_items
            seen_ok += 1
        assert seen_ok >= 190, (seen_ok, seen_too_small)

        if READ_SERVICE_AVAILABLE:
            for budget in (BUDGET_MIN, 512, 1024, 4096, BUDGET_MAX):
                args = {"project": PROJECT, "query": "svc-qx7 APP_DB_DSN", "token_budget": budget}
                result = await call_tool_raw(client, token, "memory.query", args)
                parsed = _single_text(result)
                assert not result.get("isError"), parsed
                counted = meter.count_text(result["content"][0]["text"])
                assert counted <= budget and parsed["budget"]["used"] == counted


# --------------------------------------------------------------------------- error envelope


async def test_tool_error_envelope(db_dsn) -> None:
    async with wired(db_dsn) as (client, token):

        async def err(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            result = await call_tool_raw(client, token, tool, args)
            assert result["isError"] is True, result
            env = _single_text(result)
            assert set(env) == ENVELOPE_KEYS, env
            assert isinstance(env["details"], dict) and isinstance(env["retryable"], bool)
            return env

        e = await err("memory.write", write_args("ghost", [fact("t", "b")]))
        assert (e["code"], e["retryable"]) == ("E_FORBIDDEN_PROJECT", False)

        e = await err(
            "memory.write", {"project": PROJECT, "request_id": "not-a-uuid", "client": "c", "items": []}
        )
        assert e["code"] == "E_INVALID_ARG"

        e = await err("memory.write", write_args(PROJECT, [fact("t", "b")], token_budget=100))
        assert (e["code"], e["details"]) == ("E_BUDGET_TOO_SMALL", {"min": 256})
        e = await err("memory.write", write_args(PROJECT, [fact("t", "b")], token_budget=40000))
        assert e["code"] == "E_BUDGET_TOO_LARGE"

        e = await err("memory.query", {"project": PROJECT, "query": "x", "token_budget": 255})
        assert e["code"] == "E_BUDGET_TOO_SMALL"

        e = await err("memory.nope", {})
        assert e["code"] == "E_INVALID_ARG" and e["details"] == {"tool": "memory.nope"}

        # role check: a read-only device cannot write
        _rid, reader = await trusted_device(client, "reader", grants=[{"project": PROJECT, "role": "read"}])
        result = await call_tool_raw(client, reader, "memory.write", write_args(PROJECT, [fact("t", "b")]))
        assert _single_text(result)["code"] == "E_FORBIDDEN_PROJECT"

        # status gate: a pending device never reaches the MCP session manager (HTTP 403, §2)
        _pid, pending = await register(client, "pending")
        init = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        }
        for method, params in (
            ("initialize", init),
            ("tools/list", None),
            ("tools/call", {"name": "memory.query", "arguments": {}}),
        ):
            r = await mcp_rpc(client, pending, method, params)
            assert (r.status_code, r.json()["code"]) == (403, "E_DEVICE_PENDING"), method
        r = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=bearer(None)
        )
        assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")


async def test_sdk_client_roundtrip(db_dsn) -> None:
    async with wired(db_dsn) as (client, token):
        async with sdk_client(client.app, token) as sdk:  # type: ignore[attr-defined]
            res = await sdk.call_tool(
                "memory.write", write_args(PROJECT, [fact("SDK fact", "svc-qx7 via SDK")])
            )
            assert res.is_error is False and res.structured_content is None
            assert len(res.content) == 1 and res.content[0].type == "text"
            ack = json.loads(res.content[0].text)
            assert ack["replayed"] is False and len(ack["versions"]) == 1

            res = await sdk.call_tool("memory.write", write_args("ghost", [fact("t", "b")]))
            assert res.is_error is True
            assert json.loads(res.content[0].text)["code"] == "E_FORBIDDEN_PROJECT"
