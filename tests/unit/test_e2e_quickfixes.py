"""e2e 2026-09-24 quick fixes (docs/status/E2E-PROD-REPORT.md §6/§7), the DB-free parts:

* #6 the risk judge sees the best-matching window of a long lesson (``risk_judge.lesson_text``);
* #2 the isolation rule for ``policy.librarian_cross_project = exclude`` (``isolated_scope``);
* #10 ``hlm import/export``: no "not listed by server" warning, no ``tools/list`` per call;
* #9 the realdata harness client keeps ONE connection and initializes once.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp import types
from mcp.server.lowlevel import Server

from hlmemo.cli.mcp_client import UNLISTED_TOOLS, MemoryClient
from hlmemo.librarian import risk_judge as rj
from hlmemo.librarian.candidates import isolated_scope, relation_allowed

ROOT = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------- #6 judge window
TITLE = "Operations handbook lessons"
RULE = (
    "Never run alembic downgrade on the production database: the 0007 downgrade drops the "
    "source_key index and every import afterwards duplicates items."
)
FILLER = [
    f"Paragraph {i}: rotate the log files weekly, archive dashboards monthly and keep the on-call "
    f"rota in the shared calendar so that handovers stay predictable for the team ({i})."
    for i in range(16)
]


def _long_lesson() -> tuple[str, int]:
    body = "\n\n".join([f"# {TITLE}", *FILLER[:14], RULE, *FILLER[14:]]) + "\n"
    return body, body.index(RULE)


def test_short_lessons_are_sent_whole_as_before() -> None:
    body = "## Mistake\nUsed bash -s over ssh.\n\n## Fix\nUse ssh -n."
    assert rj.lesson_text("Used bash -s over ssh.", body, [(0, len(body))], "ssh") == body
    assert rj.lesson_text("Other title", body) == f"Other title\n{body}"


def test_long_lesson_without_spans_keeps_the_first_characters() -> None:
    body, at = _long_lesson()
    text = rj.lesson_text(TITLE, body)
    assert len(text) == rj.LESSON_TEXT_CHARS + 2 and text.endswith(" …") and RULE not in text
    assert at > 2400  # the pre-fix judge never saw the rule


def test_long_lesson_sends_the_matching_window() -> None:
    """The rule sits past char 2,400; the deterministic stage matched the chunk holding it."""
    body, at = _long_lesson()
    chunk = (at - 900, min(len(body), at + len(RULE) + 700))  # a ~1,800-char chunk around the rule
    task = "run alembic downgrade on the production database to undo 0007"
    text = rj.lesson_text(TITLE, body, [chunk, (0, 1500)], task)
    assert text.startswith(f"{TITLE}\n… ") and RULE in text
    assert len(text) <= rj.LESSON_TEXT_CHARS + 2
    assert FILLER[0] not in text  # not the body's start


def test_windows_fill_the_room_in_body_order() -> None:
    body, at = _long_lesson()
    spans = [(at, at + len(RULE)), (200, 520)]  # best first; both fit
    text = rj.lesson_text(TITLE, body, spans, "alembic downgrade")
    first, second = body[200:520].strip(), RULE
    assert first in text and second in text and text.index(first) < text.index(second)
    assert "\n…\n" in text and len(text) <= rj.LESSON_TEXT_CHARS + 2
    # overlapping chunk spans are merged, never repeated
    again = rj.lesson_text(TITLE, body, [(at - 100, at + 50), (at - 50, at + len(RULE))], "alembic")
    assert again.count(RULE[:40]) == 1


# --------------------------------------------------------------------------- #2 isolation rule
def test_isolation_rule_both_directions_and_multi_project_items() -> None:
    """Sol 54 #1: one rule for every pairing; T=3 is excluded."""
    x = {3}
    assert relation_allowed({1, 2}, x) and relation_allowed({3}, x) and relation_allowed({1}, x)
    assert not relation_allowed({1, 3}, x)  # A with T, or A with a multi-project [A, T] item
    allowed = [1, 2, 3, 4]
    assert isolated_scope([1], allowed, set()) == allowed
    assert isolated_scope([1], allowed, x) == [1, 2, 4]  # another project never reaches T (or [A,T])
    assert isolated_scope([3], allowed, x) == [3]  # T sees only items lying entirely in T
    assert isolated_scope([1, 3], allowed, x) == []  # a subject spanning T and A: no candidate
    assert isolated_scope([1, 2], allowed, x) == [1, 2, 4]
    assert isolated_scope([3], [1, 2], x) == []  # (home not readable: nothing)


def test_window_positions_survive_casefolding() -> None:
    """Sol 54 #5: ``ß`` → ``ss`` and ``İ`` → ``i̇`` change lengths under casefold; the window is
    chosen on ORIGINAL positions (an unmapped match would drift ~2 chars per such character)."""
    pad = "Straße İzmir Großhändler. " * 40  # 120 length-changing characters before the rule
    body = f"# {TITLE}\n\n" + "\n\n".join(FILLER[:10]) + "\n\n" + pad + "\n\n" + RULE + "\n\n" + pad + "\n"
    at = body.index(RULE)
    chunk = (at - len(pad) - 2, at + len(RULE) + len(pad))
    text = rj.lesson_text(TITLE, body, [chunk], "alembic downgrade production database 0007 source_key")
    assert RULE in text and len(text) <= rj.LESSON_TEXT_CHARS + 2
    folded, origin = rj._folded("aßİb")
    assert folded == "assi̇b" and origin == [0, 1, 1, 2, 2, 3]


# --------------------------------------------------------------------------- #10 unlisted tools
def _fake_server(listings: list[int]) -> Server[Any]:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        listings.append(1)
        return types.ListToolsResult(
            tools=[types.Tool(name="memory.query", description="q", inputSchema={"type": "object"})]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        text = json.dumps({"tool": params.name})
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=False)

    return Server("hlmemo-fake-unlisted", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def test_unlisted_client_tool_is_not_validated(caplog: pytest.LogCaptureFixture) -> None:
    assert "hlm.export" in UNLISTED_TOOLS
    listings: list[int] = []
    client = MemoryClient.in_memory(_fake_server(listings))
    caplog.set_level(logging.DEBUG)
    async with client.session() as call:
        for _ in range(3):
            assert await call("hlm.export", {"project": "p"}) == {"tool": "hlm.export"}
        assert listings == []  # no tools/list round trip per call (it was one per call)
        assert await call("memory.query", {}) == {"tool": "memory.query"}
    assert listings == [1]  # a listed tool is still validated against the listing
    assert not [r for r in caplog.records if "not listed" in r.getMessage()]
    assert await client.call_async("hlm.export", {}) == {"tool": "hlm.export"}
    assert not [r for r in caplog.records if "not listed" in r.getMessage()]


# --------------------------------------------------------------------------- #9 realdata client
@pytest.fixture(scope="module")
def ic():  # noqa: ANN201
    path = ROOT / "eval" / "realdata" / "import_corpus.py"
    spec = importlib.util.spec_from_file_location("import_corpus_keepalive", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Mcp(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive
    state: dict[str, Any] = {}

    def setup(self) -> None:
        super().setup()
        self.state["connections"] += 1

    def log_message(self, *args: Any) -> None:  # quiet
        return

    def do_POST(self) -> None:  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.state["methods"].append(req["method"])
        if "id" not in req:  # a notification
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.state.get("fail"):
            data = json.dumps({"code": "E_UNAVAILABLE", "message": "busy", "retryable": True}).encode()
            status = 503
        else:
            if req["method"] == "initialize":
                result: dict[str, Any] = {"protocolVersion": "2025-03-26", "capabilities": {}}
            else:
                text = json.dumps({"ok": True, "n": len(self.state["methods"])})
                result = {"content": [{"type": "text", "text": text}]}
            data = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}).encode()
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if self.state.pop("close_next", False):
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():  # noqa: ANN201
    _Mcp.state = {"connections": 0, "methods": []}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Mcp)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, _Mcp.state
    httpd.shutdown()
    httpd.server_close()


def test_realdata_client_keeps_one_connection_and_initializes_once(ic, server) -> None:  # noqa: ANN001
    httpd, state = server
    with ic.Mcp(f"http://127.0.0.1:{httpd.server_address[1]}", "tok", 5.0) as mcp:
        for _ in range(5):
            assert mcp.call("memory.query", {"q": "x"})["ok"] is True
        assert state["methods"] == ["initialize", "notifications/initialized", *["tools/call"] * 5]
        assert state["connections"] == 1 and mcp.connections == 1
        # the server closes the connection after a response: the next call reconnects, once,
        # without re-initializing (the production server is stateless)
        state["close_next"] = True
        mcp.call("memory.query", {})
        mcp.call("memory.query", {})
        assert state["connections"] == 2 and mcp.connections == 2
        assert state["methods"].count("initialize") == 1
        # an HTTP error keeps its envelope (retryable 503)
        state["fail"] = True
        with pytest.raises(ic.ToolError) as err:
            mcp.call("memory.query", {})
        assert (err.value.status, err.value.code, err.value.retryable) == (503, "E_UNAVAILABLE", True)
