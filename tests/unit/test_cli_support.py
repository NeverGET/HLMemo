"""Shared fakes for the `hlm` CLI unit tests (no network, no database).

Every `test_cli_*.py` imports `_clean_tables` from here: it shadows the autouse DB fixture in
`tests/conftest.py`, so the CLI tests never touch the compose Postgres.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import types
from mcp.server import MCPServer
from typer.testing import CliRunner

ENVELOPE = {"code": "E_UNAVAILABLE", "message": "down", "retryable": True, "details": {}}


@pytest.fixture(autouse=True)
def _clean_tables():
    """Override of tests/conftest.py::_clean_tables — CLI tests need no database."""
    yield


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME/config dir/cwd in tmp; every HLM_* variable cleared."""
    for k in list(os.environ):
        if k.startswith("HLM_"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HLM_CONFIG_DIR", str(home / ".config" / "hlm"))
    monkeypatch.setenv("HLM_AGY_MCP_CONFIG", str(home / ".gemini" / "config" / "mcp_config.json"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def write_toml(
    path: Path, *, server: str = "http://127.0.0.1:8765/mcp", project: str | None = "hlmemo", **pre
) -> Path:
    lines = ["[client]", f'server_url = "{server}"', 'device_name = "testbox"']
    if project:
        lines.append(f'project = "{project}"')
    lines.append("[preflight]")
    for k, v in pre.items():
        lines.append(f"{k} = {json.dumps(v)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def error_result(
    code: str, message: str = "err", *, retryable: bool = False, **details: Any
) -> types.CallToolResult:
    env = {"code": code, "message": message, "retryable": retryable, "details": details}
    return types.CallToolResult(content=[types.TextContent(type="text", text=compact(env))], isError=True)


def fake_server(
    query_handler: Callable[[dict[str, Any]], Any] | None = None,
    close_handler: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[MCPServer, list[dict[str, Any]]]:
    """In-memory MCPServer exposing memory.query / memory.call_the_day; returns (server, recorded calls).

    A handler returns a dict (success JSON) or a `types.CallToolResult` (error envelope).
    """
    calls: list[dict[str, Any]] = []
    srv = MCPServer("hlmemo-fake")

    def _dispatch(name: str, handler, args: dict[str, Any]):  # noqa: ANN001
        calls.append({"tool": name, "args": args})
        if handler is None:
            out: Any = {"ok": True}
        else:
            out = handler(args)
        if isinstance(out, types.CallToolResult):
            return out
        return compact(out)

    @srv.tool(name="memory.query", structured_output=False)
    def _query(
        project: str,
        query: str,
        token_budget: int,
        kinds: list[str] | None = None,
        valid_at: str | None = None,
        known_at: str | None = None,
        include_archived: bool = False,
    ):
        raw = {
            "project": project,
            "query": query,
            "token_budget": token_budget,
            "kinds": kinds,
            "valid_at": valid_at,
            "known_at": known_at,
            "include_archived": include_archived,
        }
        args = {k: v for k, v in raw.items() if v not in (None, False)}
        return _dispatch("memory.query", query_handler, args)

    @srv.tool(name="memory.call_the_day", structured_output=False)
    def _close(
        project: str,
        request_id: str,
        session_id: str,
        client: str,
        notes: str,
        decisions: list[str] | None = None,
        lessons: list[dict[str, Any]] | None = None,
        card_update: dict[str, Any] | None = None,
        expected_versions: list[dict[str, Any]] | None = None,
        token_budget: int | None = None,
    ):
        raw = {
            "project": project,
            "request_id": request_id,
            "session_id": session_id,
            "client": client,
            "notes": notes,
            "decisions": decisions,
            "lessons": lessons,
            "card_update": card_update,
            "expected_versions": expected_versions,
            "token_budget": token_budget,
        }
        args = {k: v for k, v in raw.items() if v is not None}
        return _dispatch("memory.call_the_day", close_handler, args)

    return srv, calls


def query_ok(project: str = "hlmemo", limit: int = 3000) -> dict[str, Any]:
    return {
        "project": project,
        "as_of": {"valid_at": "2026-09-22T10:00:00Z", "known_at": "2026-09-22T10:00:00Z"},
        "device_class": "personal",
        "card": None,
        "hits": [
            {
                "clue": "v12",
                "kind": "lesson",
                "title": "pool discard",
                "preview": "INTRANS discard bug",
                "score": 0.9,
                "valid_from": "2026-09-01T00:00:00Z",
                "tags": ["db"],
                "device_scope": "all",
            }
        ],
        "omitted": 0,
        "evidence": "matched",
        "indexing_pending": False,
        "budget": {"limit": limit, "used": 120, "tokenizer": "o200k_base"},
    }


class FakeApi:
    """httpx.MockTransport-backed stand-in for the REST routes; records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def on(self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.routes[(method, path)] = handler

    def json(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.on(method, path, lambda _r: httpx.Response(status, json=body))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        h = self.routes.get((request.method, request.url.path))
        if h is None:
            return httpx.Response(
                404, json={"code": "E_NOT_FOUND", "message": "no route", "retryable": False}
            )
        return h(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def home() -> Path:
    """The isolated HOME of the current test (set by `_isolated_home`)."""
    return Path(os.environ["HOME"])


def runner() -> CliRunner:
    return CliRunner()


def test_support_module_has_no_tests() -> None:
    """Keeps pytest happy about collecting this module."""
    assert ENVELOPE["code"] == "E_UNAVAILABLE"
