"""The five HLMemo tools (PHASE0-SPEC §3, D-013): name, description, input schema, handler.

``TOOLS`` is the single registry ``server/mcp_server.py`` advertises on ``tools/list`` and
dispatches ``tools/call`` against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hlmemo.server.tools import handlers, schemas
from hlmemo.server.tools.handlers import READ_SERVICE_AVAILABLE, Handler


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "memory.query",
        "Hybrid (lexical + trigram + vector, RRF) search over the project's memory at a bi-temporal "
        "point. Returns the project card, ranked hits with clues and previews, and "
        "evidence:'matched'|'none' within token_budget. Read-only.",
        schemas.QUERY_INPUT,
        handlers.memory_query,
    ),
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
)

TOOL_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)

__all__ = ["READ_SERVICE_AVAILABLE", "TOOL_BY_NAME", "TOOL_NAMES", "TOOLS", "ToolSpec"]
