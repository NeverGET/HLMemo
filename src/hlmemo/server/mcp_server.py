"""MCP server for the five HLMemo tools (PHASE0-SPEC §3, D-013, D-024 (6)).

Wire rule (D-024 (6)): every ``tools/call`` result is ``CallToolResult(content=[TextContent])`` —
exactly one text block carrying the canonical compact JSON (``json.dumps(..., ensure_ascii=False,
separators=(",", ":"), sort_keys=True)``), **no** ``structuredContent``; ``tools/list`` advertises
``inputSchema`` only. The text on the wire is byte-identical to what the service metered, so
``budget.used`` counts the exact bytes the client receives. Errors are results with
``isError: true`` whose single text block is ``{"code","message","retryable","details"}``.

The low-level ``mcp.server.lowlevel.Server`` is used (not the decorator-based ``MCPServer``) so
that the spec's JSON schemas go out verbatim and the error envelope is ours, not the SDK's
"Error executing tool …" wrapper.

How a tool handler obtains its transaction and identity
-------------------------------------------------------
``server/middleware.py`` gates every ``/mcp`` request before routing: it opens the request
transaction, resolves the bearer under ``FOR SHARE`` and exposes ``conn`` / ``auth`` on the ASGI
``scope["state"]``. The streamable-HTTP transport attaches that very Starlette ``Request`` to
``ServerRequestContext.request`` and awaits the tool before it answers the POST, so the handler
runs inside the gated request: ``request_binding()`` reads ``conn``/``auth`` from
``request.scope["state"]``. Pending/revoked devices never reach the session manager (the
middleware answers 403/401 for ``initialize``, ``tools/list`` and ``tools/call`` alike). Should
the session manager ever run outside that scope (state missing), the fallback opens its own
pooled transaction and re-resolves the bearer from the HTTP headers — same §2 rules.

Transport choice: stateless streamable HTTP with JSON responses. Every request is authorised on
its own (§2), so no server-side session state is needed, and a session id can never outlive or
cross a bearer. DNS-rebinding protection is disabled on purpose: the bearer is the gate and the
public host name is deployment-specific.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from psycopg import AsyncConnection
from psycopg import errors as pgerrors
from starlette.requests import Request

from hlmemo.auth.context import AuthContext
from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import resolve
from hlmemo.auth.tokens import parse_bearer
from hlmemo.config import get_settings
from hlmemo.core.budget import BudgetError, canonical
from hlmemo.core.clues import InvalidClue
from hlmemo.core.errors import ToolError
from hlmemo.server.tools import TOOL_BY_NAME, TOOLS
from hlmemo.server.tools.handlers import ReadHandler

log = logging.getLogger("hlmemo.server.mcp")

SERVER_NAME = "hlmemo"
INSTRUCTIONS = (
    "HLMemo: bi-temporal long-term memory. Call memory.query before acting, memory.drilldown to "
    "expand clues, memory.raw for provenance, memory.write to remember, memory.call_the_day to "
    "close a session. Every result is one JSON text block with a `budget` block; errors are "
    "`{code,message,retryable,details}` with isError=true."
)


def _package_version() -> str:
    try:
        return version("hlmemo")
    except PackageNotFoundError:  # pragma: no cover - editable/uninstalled checkout
        return "0.0.0"


# --------------------------------------------------------------------------- error envelope


def _jsonable(details: Any) -> dict[str, Any]:
    """Details must survive ``canonical()``: pydantic's ``ctx.error`` carries raw exceptions."""
    if not isinstance(details, dict):
        return {}
    return json.loads(json.dumps(details, ensure_ascii=False, default=str))


def error_envelope(err: BaseException) -> dict[str, Any]:
    """``{code, message, retryable, details}`` for any exception a tool may raise (§3 codes)."""
    if isinstance(err, ToolError | HlmError):
        return {
            "code": err.code,
            "message": err.message,
            "retryable": bool(err.retryable),
            "details": _jsonable(err.details),
        }
    if isinstance(err, InvalidClue):  # F01: a malformed clue is the caller's error, never retryable
        return {"code": "E_INVALID_ARG", "message": str(err)[:300], "retryable": False, "details": {}}
    if isinstance(err, BudgetError):
        env = err.as_error()
        env["details"] = _jsonable(env.get("details"))
        return env
    if isinstance(err, pgerrors.Error):
        details: dict[str, Any] = {}
        sqlstate = getattr(err, "sqlstate", None)
        if sqlstate:
            details["sqlstate"] = sqlstate
        return {"code": "E_UNAVAILABLE", "message": "database error", "retryable": True, "details": details}
    return {"code": "E_UNAVAILABLE", "message": "internal error", "retryable": True, "details": {}}


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    """Exactly one TextContent, never structuredContent (D-024 (6))."""
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=is_error)


def error_result(err: BaseException) -> types.CallToolResult:
    return text_result(canonical(error_envelope(err)), is_error=True)


# --------------------------------------------------------------------------- request binding


@contextlib.asynccontextmanager
async def request_binding(
    ctx: ServerRequestContext[Any, Any],
) -> AsyncIterator[tuple[AsyncConnection, AuthContext]]:
    """Yield ``(conn, auth)`` for this tool call — see the module docstring."""
    request = ctx.request
    if not isinstance(request, Request):
        raise HlmError("E_AUTH", "tool calls are only served over the authenticated HTTP transport")
    state = request.scope.get("state") or {}
    conn = state.get("conn")
    auth = state.get("auth")
    if conn is not None and auth is not None:
        yield conn, auth
        return

    # Fallback: outside the middleware's transaction. Same §2 rules, own transaction.
    log.warning("mcp tool call without request-scoped tx; re-resolving bearer from headers")
    pool = request.app.state.pool
    bearer = parse_bearer(request.headers.get("authorization"))
    client = request.headers.get("x-hlm-client") or request.headers.get("user-agent") or "unknown/0"
    async with pool.connection() as own:
        try:
            auth, _row = await resolve(own, bearer, client=client)
            yield own, auth
        except BaseException:
            await own.rollback()
            raise
        await own.commit()


# --------------------------------------------------------------------------- handlers


def _client_of(ctx: ServerRequestContext[Any, Any]) -> str:
    request = ctx.request
    if isinstance(request, Request):
        return request.headers.get("x-hlm-client") or request.headers.get("user-agent") or "unknown/0"
    return "unknown/0"


async def on_list_tools(
    ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    log.info("mcp tools/list client=%s", _client_of(ctx))
    return types.ListToolsResult(
        tools=[types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema) for t in TOOLS]
    )


async def on_call_tool(
    ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
) -> types.CallToolResult:
    spec = TOOL_BY_NAME.get(params.name)
    if spec is None:
        return error_result(ToolError("E_INVALID_ARG", f"unknown tool {params.name!r}", tool=params.name))
    arguments = params.arguments
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return error_result(ToolError("E_INVALID_ARG", "arguments must be a JSON object"))
    # One INFO line per call (tool, device, client, outcome, ms): the G7 smoke scripts and the
    # gate report read tool names from the api log. Never log arguments (they may hold secrets).
    t0 = time.perf_counter()
    device_id: int | None = None
    outcome = "ok"
    try:
        async with request_binding(ctx) as (conn, auth):
            device_id = auth.device_id
            # Savepoint inside the request transaction: a failing tool leaves the connection
            # usable and the middleware still commits the (read-only) outer transaction.
            async with conn.transaction():
                if params.name in {"memory.query", "memory.drilldown", "memory.raw"}:
                    # Never enter the standalone service's lazy model path from HTTP.
                    deps = ctx.request.app.state.read_deps
                    if deps is None:
                        raise RuntimeError("read dependencies are not initialized")
                    result = await cast(ReadHandler, spec.handler)(conn, auth, dict(arguments), deps=deps)
                elif spec.app_bound:  # W2d: handlers that need the app (shared deps, risk judge)
                    result = await cast(Any, spec.handler)(conn, auth, dict(arguments), app=ctx.request.app)
                else:
                    result = await spec.handler(conn, auth, dict(arguments))
    except (ToolError, HlmError, BudgetError, InvalidClue) as err:
        outcome = getattr(err, "code", type(err).__name__)
        return error_result(err)
    except pgerrors.Error as err:
        outcome = "E_UNAVAILABLE"
        log.warning("tool %s: database error %s", params.name, getattr(err, "sqlstate", None))
        return error_result(err)
    except Exception as err:  # noqa: BLE001 - every failure must become an isError result
        outcome = "crash"
        log.exception("tool %s crashed", params.name)
        return error_result(err)
    finally:
        log.info(
            "mcp tools/call %s device=%s client=%s outcome=%s ms=%d",
            params.name,
            device_id if device_id is not None else "-",
            _client_of(ctx),
            outcome,
            int((time.perf_counter() - t0) * 1000),
        )
    return text_result(canonical(result))


# --------------------------------------------------------------------------- construction


def build_server() -> Server[Any]:
    return Server(
        SERVER_NAME,
        version=_package_version(),
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


@dataclass(slots=True)
class McpEndpoint:
    """What ``server/app.py`` mounts: the ASGI handler for ``/mcp`` and the session-manager lifespan."""

    server: Server[Any]
    session_manager: StreamableHTTPSessionManager
    asgi: StreamableHTTPASGIApp

    def run(self):  # noqa: ANN201 - the SDK's own context-manager type
        """Lifespan context: ``async with endpoint.run(): ...`` (one call per manager instance)."""
        return self.session_manager.run()


def create_mcp_endpoint(
    *, json_response: bool = True, stateless: bool = True, max_request_body_size: int | None = None
) -> McpEndpoint:
    server = build_server()
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
        max_request_body_size=max_request_body_size or get_settings().request_max_body_bytes,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return McpEndpoint(server=server, session_manager=manager, asgi=StreamableHTTPASGIApp(manager))


__all__ = [
    "INSTRUCTIONS",
    "SERVER_NAME",
    "McpEndpoint",
    "build_server",
    "create_mcp_endpoint",
    "error_envelope",
    "error_result",
    "on_call_tool",
    "on_list_tools",
    "request_binding",
    "text_result",
]
