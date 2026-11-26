"""D-287: MCP wire names use ``_`` instead of ``.``.

Google's Antigravity CLI (agy 1.2.9) drops every tool whose name breaks the Gemini/OpenAI
function-name rule ``^[a-zA-Z0-9_-]{1,64}$`` (its log: ``tool name mcp_claude-sync_hlm_memory.query
violates ...``). The server therefore advertises ``memory_query`` on ``tools/list``, accepts both
``memory_query`` and the canonical ``memory.query`` on ``tools/call`` (older hooks and CLIs call the
dotted name), and keeps the canonical name everywhere behind the boundary (logs, owner check, read
dispatch). The DB is stubbed here, like ``test_owner_only_tools.py``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import types
from starlette.requests import Request

from hlmemo.auth.context import AuthContext, Role
from hlmemo.config import get_settings
from hlmemo.librarian import questions as lq
from hlmemo.server import mcp_server
from hlmemo.server.tools import (
    CLIENT_TOOLS,
    TOOL_BY_NAME,
    TOOL_BY_WIRE,
    TOOLS,
    WIRE_NAME_RE,
    advertised_tools,
    handlers,
    resolve_tool,
    wire_name,
)

OWNER = "o" * 64
AGENT = AuthContext(9, "ci", False, 1, {1: Role.READ}, client="agy/1.2.9")
GEMINI_RULE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # verbatim from the agy log, not our constant
DOTTED_TOOL = re.compile(r"\b(?:memory|hlm)\.[a-z_]+\b")
ASK_ON = SimpleNamespace(research_enabled=True, librarian_enabled=True, llm_mode="openrouter")
ASK_OFF = SimpleNamespace(research_enabled=False, librarian_enabled=True, llm_mode="openrouter")


class _Conn:
    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield


def _ctx(
    *,
    settings: Any = None,
    headers: dict[str, str] | None = None,
    read_deps: Any = None,
    auth: AuthContext = AGENT,
) -> Any:
    app = SimpleNamespace(state=SimpleNamespace(settings=settings or get_settings(), read_deps=read_deps))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "state": {"conn": _Conn(), "auth": auth},
        "app": app,
    }
    return SimpleNamespace(request=Request(scope))


async def _call(ctx: Any, name: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    res = await mcp_server.on_call_tool(ctx, types.CallToolRequestParams(name=name, arguments=arguments))
    return bool(res.is_error), json.loads(res.content[0].text)


async def _listed(settings: Any) -> list[str]:
    res = await mcp_server.on_list_tools(_ctx(settings=settings), None)
    return [t.name for t in res.tools]


# --------------------------------------------------------------------------- tools/list


@pytest.mark.parametrize("settings", [ASK_ON, ASK_OFF], ids=["ask-on", "ask-off"])
async def test_tools_list_names_match_the_gemini_function_name_rule(settings: Any) -> None:
    names = await _listed(settings)
    assert names, "tools/list is empty"
    assert all(GEMINI_RULE.fullmatch(n) for n in names), names
    assert names == [wire_name(t.name) for t in advertised_tools(settings)]
    assert "memory_query" in names and "memory.query" not in names
    assert WIRE_NAME_RE.pattern == GEMINI_RULE.pattern


async def test_memory_ask_keeps_its_listing_condition() -> None:
    assert "memory_ask" in await _listed(ASK_ON)
    assert "memory_ask" not in await _listed(ASK_OFF)


async def test_client_tools_stay_unlisted_under_both_names() -> None:
    names = set(await _listed(ASK_ON))
    for t in CLIENT_TOOLS:
        assert t.name not in names and wire_name(t.name) not in names


def test_every_name_maps_one_to_one() -> None:
    specs = (*TOOLS, *CLIENT_TOOLS)
    assert len({wire_name(t.name) for t in specs}) == len(specs) == len(TOOL_BY_WIRE) == len(TOOL_BY_NAME)
    for t in specs:
        assert resolve_tool(t.name) is t and resolve_tool(wire_name(t.name)) is t
    assert resolve_tool("memory-query") is None and resolve_tool("memory.query ") is None


def test_model_facing_strings_name_tools_in_their_wire_form() -> None:
    """The instructions and every advertised description name tools a model can actually call."""
    texts = {"instructions": mcp_server.INSTRUCTIONS}
    texts.update({t.name: t.description + json.dumps(t.input_schema) for t in TOOLS})
    for where, text in texts.items():
        assert not DOTTED_TOOL.findall(text), (where, DOTTED_TOOL.findall(text))
    assert "memory_query" in mcp_server.INSTRUCTIONS and "memory_drilldown" in mcp_server.INSTRUCTIONS


# --------------------------------------------------------------------------- tools/call


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []

    async def _read(name: str, conn: Any, ctx: Any, args: dict[str, Any], deps: Any) -> dict[str, Any]:
        calls.append((name, deps))
        return {"read": name}

    monkeypatch.setattr(handlers, "_read", _read)
    return calls


@pytest.mark.parametrize("name", ["memory_drilldown", "memory.drilldown"])
async def test_wire_and_dotted_names_reach_the_same_read_handler(
    name: str, reads: list[tuple[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    deps = object()
    caplog.set_level(logging.INFO, logger="hlmemo.server.mcp")
    is_error, body = await _call(_ctx(read_deps=deps), name, {"project": "demo"})
    assert not is_error and body == {"read": "drilldown"}
    # the read branch (READ_TOOL_NAMES) is chosen by the canonical name: deps reach the handler
    assert reads == [("drilldown", deps)]
    # the api log keeps the canonical name (the G7 smoke scripts grep it)
    lines = [r.getMessage() for r in caplog.records if "tools/call" in r.getMessage()]
    assert lines and all(line.startswith("mcp tools/call memory.drilldown ") for line in lines), lines


@pytest.mark.parametrize("name", ["memory_qeury", "memory", "", "hlm_export.x", "memory.query.x"])
async def test_an_unknown_name_is_invalid_arg(name: str) -> None:
    is_error, body = await _call(_ctx(), name, {})
    assert is_error and body["code"] == "E_INVALID_ARG" and body["retryable"] is False


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def review_list(conn: Any, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
        calls.append(ctx.device_id)
        return {"project": args["project"], "pending": {"total": 0, "by_kind": {}}, "questions": []}

    monkeypatch.setattr(lq, "review_list", review_list)
    return calls


@pytest.mark.parametrize("name", ["hlm_questions", "hlm.questions"])
async def test_the_owner_only_check_holds_under_both_names(name: str, review: list[int]) -> None:
    settings = get_settings(owner_token=OWNER)
    for headers in ({}, {"User-Agent": "hlm-cli"}, {"X-HLM-Owner-Token": "x" * 64}):
        is_error, body = await _call(_ctx(settings=settings, headers=headers), name, {"project": "demo"})
        assert is_error and body["code"] == "E_FORBIDDEN", (headers, body)
        assert body["message"].startswith("hlm.questions is an owner-only client tool"), body
    assert review == []  # refused before any read
    owner = _ctx(settings=settings, headers={"X-HLM-Owner-Token": OWNER})
    is_error, body = await _call(owner, name, {"project": "demo"})
    assert not is_error and body["project"] == "demo" and review == [9]


@pytest.mark.parametrize("name", ["memory_ask", "memory.ask"])
async def test_a_disabled_memory_ask_is_refused_under_both_names(name: str) -> None:
    is_error, body = await _call(_ctx(settings=ASK_OFF), name, {"question": "why?"})
    assert is_error and body["code"] == "E_UNAVAILABLE" and body["details"]["reason"] == "disabled"
