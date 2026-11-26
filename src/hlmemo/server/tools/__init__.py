"""The HLMemo tools (PHASE0-SPEC §3, D-013; CC-4): name, description, input schema, handler.

``TOOLS`` is the single registry ``server/mcp_server.py`` advertises on ``tools/list`` and
dispatches ``tools/call`` against. New tools live in their own modules and register with one line.
An ``app_bound`` handler is called as ``handler(conn, auth, args, app=<Starlette app>)``.

Names (D-287): ``ToolSpec.name`` is the canonical dotted name (``memory.query``) used everywhere
inside the server (logs, meters, events, budgets, audit, error ``tool=`` fields). Only the MCP
boundary differs: ``tools/list`` advertises ``wire_name(name)`` (``memory_query``), because the
Gemini/OpenAI function-name rule ``^[a-zA-Z0-9_-]{1,64}$`` rejects dots (agy 1.2.9 drops every dotted
tool), and ``tools/call`` accepts either form (``resolve_tool``), so older hooks and CLIs that call
``memory.query`` keep working.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hlmemo.core import export_service
from hlmemo.server.tools import answer, ask, handlers, projects, query, questions, risk, schemas
from hlmemo.server.tools.handlers import READ_SERVICE_AVAILABLE, Handler


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    app_bound: bool = False
    #: ``listed(settings)``: advertise the tool on tools/list only when true (None: always)
    listed: Callable[[Any], bool] | None = None
    #: dispatched only with the owner client capability (``mcp_server.require_owner_client``): the
    #: device bearer every agent on that device also holds is not enough
    owner_only: bool = False

    def advertised(self, settings: Any) -> bool:
        return self.listed is None or bool(self.listed(settings))


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
        "transaction, idempotent per request_id. Returns the version ids and chunk counts. "
        "If an item corrects a memory you read (not just adds to it), give it updates: item = that "
        "memory's clue (e.g. v123.0), old_span = the outdated text quoted verbatim, mode revise or "
        "supersede; a revise replacement must occur verbatim in body. Results: applied, linked, "
        "rejected.",
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
    ToolSpec(answer.NAME, answer.DESCRIPTION, answer.INPUT_SCHEMA, answer.memory_answer),  # W2c
    ask.tool_spec(ToolSpec),  # D-136: the research librarian (listed while HLM_RESEARCH_ENABLED + LLM)
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
    # `hlm review`: the open librarian questions with what a reviewer needs (read-only, owner-only)
    ToolSpec(
        questions.NAME,
        questions.DESCRIPTION,
        questions.INPUT_SCHEMA,
        questions.hlm_questions,
        owner_only=True,
    ),
    # D-274: an account member creates <account>-... projects (`hlm project create`)
    ToolSpec(projects.NAME, projects.DESCRIPTION, projects.INPUT_SCHEMA, projects.hlm_project_create),
)

TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in (*TOOLS, *CLIENT_TOOLS)}
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)

#: the function-name rule of the Gemini and OpenAI tool APIs (agy, D-287): every wire name matches it
WIRE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def wire_name(name: str) -> str:
    """The name ``tools/list`` advertises for the canonical ``name`` (D-287): ``.`` becomes ``_``."""
    return name.replace(".", "_")


def _wire_map(specs: tuple[ToolSpec, ...]) -> dict[str, ToolSpec]:
    """``{wire name: spec}``; refuses (at import) a wire name that breaks the rule or that collides
    with another tool's wire or canonical name, so a call can never reach the wrong handler."""
    out: dict[str, ToolSpec] = {}
    for t in specs:
        if TOOL_BY_NAME.get(t.name) is not t:
            raise RuntimeError(f"tool name {t.name!r} is registered twice")
        w = wire_name(t.name)
        if not WIRE_NAME_RE.fullmatch(w):
            raise RuntimeError(f"tool wire name {w!r} does not match {WIRE_NAME_RE.pattern}")
        clash = out.get(w) or TOOL_BY_NAME.get(w)
        if clash is not None and clash is not t:
            raise RuntimeError(f"tool wire name {w!r} of {t.name!r} collides with {clash.name!r}")
        out[w] = t
    return out


TOOL_BY_WIRE: dict[str, ToolSpec] = _wire_map((*TOOLS, *CLIENT_TOOLS))


def resolve_tool(name: str) -> ToolSpec | None:
    """The spec a ``tools/call`` name addresses: the canonical dotted name or its wire form."""
    return TOOL_BY_NAME.get(name) or TOOL_BY_WIRE.get(name)


def advertised_tools(settings: Any) -> tuple[ToolSpec, ...]:
    """What ``tools/list`` advertises under ``settings`` (a conditional tool only while enabled)."""
    return tuple(t for t in TOOLS if t.advertised(settings))


READ_TOOL_NAMES: frozenset[str] = frozenset({"memory.query", "memory.drilldown", "memory.raw", "hlm.export"})

__all__ = [
    "CLIENT_TOOLS",
    "READ_SERVICE_AVAILABLE",
    "READ_TOOL_NAMES",
    "TOOL_BY_NAME",
    "TOOL_BY_WIRE",
    "TOOL_NAMES",
    "TOOLS",
    "WIRE_NAME_RE",
    "ToolSpec",
    "advertised_tools",
    "resolve_tool",
    "wire_name",
]
