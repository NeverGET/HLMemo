"""Review 101 HIGH (Astra 1): ``hlm.questions`` was hidden from ``tools/list`` but any agent could
CALL it by name with its device bearer and a project ``read`` grant. It is now owner-only: the MCP
dispatch (``on_call_tool``) refuses it unless the request carries the server's owner client token
in ``X-HLM-Owner-Token`` — a credential an agent's MCP client never sends (only the bearer fixed at
registration) and that ``User-Agent`` / ``x-hlm-client`` cannot stand in for.

This reproduces the review's exact chain (agent ``AuthContext`` → ``on_call_tool`` →
``hlm_questions`` → ``review_list``) with the DB stubbed; the wire version on a real DB is in
``tests/integration/test_review_cli.py``.
"""

from __future__ import annotations

import contextlib
import json
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
from hlmemo.server.tools import CLIENT_TOOLS, TOOLS, advertised_tools

OWNER = "o" * 64
AGENT = AuthContext(9, "ci", False, 1, {1: Role.READ}, client="codex/1")


class _Conn:
    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield


def _ctx(auth: AuthContext, headers: dict[str, str], *, owner_token: str | None) -> Any:
    settings = get_settings(owner_token=owner_token)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, read_deps=None))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "state": {"conn": _Conn(), "auth": auth},
        "app": app,
    }
    return SimpleNamespace(request=Request(scope))


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def review_list(conn: Any, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
        calls.append({"device": ctx.device_id, **args})
        return {"project": args["project"], "pending": {"total": 0, "by_kind": {}}, "questions": []}

    monkeypatch.setattr(lq, "review_list", review_list)
    return calls


async def _call(ctx: Any) -> tuple[bool, dict[str, Any]]:
    params = types.CallToolRequestParams(name="hlm.questions", arguments={"project": "demo"})
    res = await mcp_server.on_call_tool(ctx, params)
    return bool(res.is_error), json.loads(res.content[0].text)


async def test_an_agent_call_to_hlm_questions_is_refused(listed: list[dict[str, Any]]) -> None:
    """The review's reproducer: a ci device with project READ, client codex/1, standard tools/call."""
    agent_headers = [
        {"User-Agent": "codex/1"},
        # the client labels are caller-chosen: claiming to be the hlm CLI changes nothing
        {"User-Agent": "hlm-cli", "x-hlm-client": "hlm-cli/1"},
        {"User-Agent": "hlm-cli", "X-HLM-Owner-Token": "x" * 64},  # a wrong token
        {"User-Agent": "hlm-cli", "X-HLM-Owner-Token": ""},
    ]
    for headers in agent_headers:
        is_error, body = await _call(_ctx(AGENT, headers, owner_token=OWNER))
        assert is_error and body["code"] == "E_FORBIDDEN", (headers, body)
        assert "owner-only" in body["message"] and OWNER not in json.dumps(body)
    assert listed == []  # refused before any read


async def test_owner_only_fails_closed_without_a_usable_server_token(listed: list[dict[str, Any]]) -> None:
    for configured in (None, "", "s" * 31):  # unset, empty, too short: refused for everyone
        presented = configured or OWNER
        is_error, body = await _call(_ctx(AGENT, {"X-HLM-Owner-Token": presented}, owner_token=configured))
        assert is_error and body["code"] == "E_FORBIDDEN", configured
    assert listed == []


async def test_the_owner_client_passes_and_the_device_grants_still_apply(
    listed: list[dict[str, Any]],
) -> None:
    is_error, body = await _call(_ctx(AGENT, {"X-HLM-Owner-Token": OWNER}, owner_token=OWNER))
    assert not is_error and body["project"] == "demo"
    assert listed == [{"device": 9, "project": "demo"}]  # the handler still sees the device's grants


def test_hlm_questions_stays_unadvertised_and_is_the_only_owner_only_tool() -> None:
    assert "hlm.questions" not in {t.name for t in advertised_tools(get_settings())}
    assert "hlm.questions" not in {t.name for t in TOOLS}
    assert {t.name for t in (*TOOLS, *CLIENT_TOOLS) if t.owner_only} == {"hlm.questions"}
