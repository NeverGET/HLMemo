"""``memory.ask`` registration (D-130, D-136): the research librarian (``core/research_service``).

``app_bound``: the handler takes the app's shared ``ReadDeps``, its ``Researcher`` (built on first
use) and the pool (fresh connections after ``detach``, D-062). Advertised on ``tools/list`` only
while ``research_available(settings)``: ``HLM_RESEARCH_ENABLED`` and the librarian LLM runtime
(``HLM_LIBRARIAN_ENABLED``, ``HLM_LLM_MODE`` not ``off``). The schema and description stay short
(G-SURF: the whole ``tools/list`` ≤ 3,000 o200k tokens).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core import research_service
from hlmemo.core.errors import ToolError
from hlmemo.server.tools.schemas import DEFS

NAME = "memory.ask"
DESCRIPTION = (
    "Ask the research librarian a question about a project's memory: it searches several ways, reads "
    "the evidence and answers {answer, confidence, abstained, primary:[{handle,path,quote}], "
    "related:[{handle,path}]}; open handles with memory.drilldown. Read-only, ~10-20 s."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": research_service.QUESTION_MAX},
        "project": DEFS["Slug"],
        "token_budget": {**DEFS["Budget"], "default": research_service.DEFAULT_BUDGET},
    },
    "required": ["question"],
    "additionalProperties": False,
}


def research_available(settings: Any) -> bool:
    """``memory.ask`` is served (and advertised) only with the flag on and an LLM runtime."""
    return (
        bool(getattr(settings, "research_enabled", False))
        and bool(getattr(settings, "librarian_enabled", False))
        and getattr(settings, "llm_mode", "off") != "off"
    )


async def memory_ask(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    app: Any = None,
    detach: Callable[..., Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    if app is None:
        raise ToolError("E_UNAVAILABLE", "tool needs the server application", retryable=True)
    state = app.state
    if not research_available(state.settings):
        raise ToolError("E_UNAVAILABLE", "memory.ask is disabled on this server", reason="disabled")
    deps = getattr(state, "read_deps", None)
    if deps is None:
        raise ToolError("E_UNAVAILABLE", "read dependencies are not initialized", reason="not_ready")
    from hlmemo.librarian.tasks.research import app_researcher

    pool = getattr(state, "pool", None)
    return await research_service.ask(
        conn,
        ctx,
        args,
        deps=deps,
        researcher=app_researcher(app),
        detach=detach,
        reconnect=pool.connection if pool is not None else None,
        settings=state.settings,
    )


def tool_spec(spec_cls: Any) -> Any:
    """The ``memory.ask`` ``ToolSpec`` (the class is passed in to avoid an import cycle)."""
    return spec_cls(NAME, DESCRIPTION, INPUT_SCHEMA, memory_ask, app_bound=True, listed=research_available)


__all__ = ["DESCRIPTION", "INPUT_SCHEMA", "NAME", "memory_ask", "research_available", "tool_spec"]
