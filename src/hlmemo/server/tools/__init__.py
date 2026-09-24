"""The HLMemo tools (PHASE0-SPEC §3, D-013; CC-4): name, description, input schema, handler.

``TOOLS`` is the single registry ``server/mcp_server.py`` advertises on ``tools/list`` and
dispatches ``tools/call`` against. New tools live in their own modules and register with one line.
An ``app_bound`` handler is called as ``handler(conn, auth, args, app=<Starlette app>)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hlmemo.core import export_service
from hlmemo.server.tools import handlers, query, risk, schemas
from hlmemo.server.tools.handlers import READ_SERVICE_AVAILABLE, Handler


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    app_bound: bool = False


TOOLS: tuple[ToolSpec, ...] = (
    query.tool_spec(ToolSpec),  # W2e: query/2 (`synthesize`), app-bound; no flag = Phase-0 path
    ToolSpec(
        "memory.drilldown",
        "Expand up to 20 clues (v<version_id> for a whole item, v<version_id>.<ordinal> for a chunk "
        "with its neighbours) into full text plus one-hop links, paginated by cursor.",
        schemas.DRILLDOWN_INPUT,
        handlers.memory_drilldown,
    ),
    ToolSpec(
        "memory.raw",
        "Provenance view of one memory version: temporal columns, source event, original payload "
        "item, links and chunks. Historical (superseded/expired) versions are returned.",
        schemas.RAW_INPUT,
        handlers.memory_raw,
    ),
    ToolSpec(
        "memory.write",
        "Append 1..50 memory items (new items or revisions with expected_version_id) in one "
        "transaction, idempotent per request_id. Returns the version ids and chunk counts.",
        schemas.WRITE_INPUT,
        handlers.memory_write,
    ),
    ToolSpec(
        "memory.call_the_day",
        "Close a session: one write batch with the session note (+ decisions), lessons and an "
        "optional project-card update. A session_id can be closed once per project.",
        schemas.CALL_THE_DAY_INPUT,
        handlers.memory_call_the_day,
    ),
    *risk.tool_specs(ToolSpec),  # W2d: memory.risk_check, memory.register_lesson
)

# Client-protocol tools (W1.5; Sol consult 40 #1): dispatched by tools/call for the `hlm` CLI but
# NEVER advertised on tools/list, so the agent tool surface (CC-4, G-SURF) does not grow.
CLIENT_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "hlm.export",
        "hlm CLI only: page every item of a project at one bi-temporal point (manifest or full view).",
        export_service.INPUT_SCHEMA,
        handlers.hlm_export,
    ),
)

TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in (*TOOLS, *CLIENT_TOOLS)}
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)
READ_TOOL_NAMES: frozenset[str] = frozenset({"memory.query", "memory.drilldown", "memory.raw", "hlm.export"})

__all__ = [
    "CLIENT_TOOLS",
    "READ_SERVICE_AVAILABLE",
    "READ_TOOL_NAMES",
    "TOOL_BY_NAME",
    "TOOL_NAMES",
    "TOOLS",
    "ToolSpec",
]
