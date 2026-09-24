"""``memory.query`` registration (PHASE0-SPEC §3; W2e adds ``synthesize``, contract ``query/2``).

The tool is ``app_bound`` so that a ``synthesize:true`` call can reach the app's shared
``ReadDeps``, its ``Synthesizer`` and the request-transaction ``detach`` (D-062). A call WITHOUT
the flag (absent or ``false``) runs exactly the Phase-0 path: a savepoint in the request
transaction around ``handlers.memory_query`` with the lifespan's ``ReadDeps`` — the same code,
the same errors and byte-identical output as before W2e.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core import synthesis_service
from hlmemo.core.errors import ToolError
from hlmemo.server.tools import handlers, schemas

QUERY = "memory.query"
QUERY_DESCRIPTION = (
    "Hybrid (lexical + trigram + vector, RRF) search over the project's memory at a bi-temporal "
    "point. Returns the project card, ranked hits with clues and query-centred preview excerpts, "
    "and evidence:'matched'|'none' within token_budget. Drill the top 5 clues with "
    "memory.drilldown before relying on a preview. Read-only. synthesize:true (a question) adds a "
    "cited draft answer when the evidence is weak; verify its clues."
)
QUERY_INPUT: dict[str, Any] = copy.deepcopy(schemas.QUERY_INPUT)
QUERY_INPUT["properties"]["synthesize"] = {"type": "boolean", "default": False}


def synthesize_flag(args: dict[str, Any]) -> bool:
    """Pop ``synthesize`` from ``args`` (the read service's request model does not know it)."""
    flag = args.pop("synthesize", False)
    if not isinstance(flag, bool):
        raise ToolError("E_INVALID_ARG", "synthesize must be a boolean", field="synthesize")
    return flag


async def memory_query(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    app: Any = None,
    detach: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    if app is None:
        raise ToolError("E_UNAVAILABLE", "tool needs the server application", retryable=True)
    if not synthesize_flag(args):
        # the Phase-0 path of mcp_server's read branch, verbatim (savepoint + shared deps)
        async with conn.transaction():
            deps = app.state.read_deps
            if deps is None:
                raise RuntimeError("read dependencies are not initialized")
            return await handlers.memory_query(conn, ctx, args, deps=deps)
    from hlmemo.librarian.tasks.synthesis import app_synthesizer

    deps = getattr(app.state, "read_deps", None)
    if deps is None:
        raise RuntimeError("read dependencies are not initialized")
    pool = getattr(app.state, "pool", None)
    return await synthesis_service.query_synthesize(
        conn,
        ctx,
        args,
        deps=deps,
        synth=app_synthesizer(app),
        detach=detach,
        reconnect=pool.connection if pool is not None else None,
    )


def tool_spec(spec_cls: Any) -> Any:
    """The ``memory.query`` ``ToolSpec`` (the class is passed in to avoid an import cycle)."""
    return spec_cls(QUERY, QUERY_DESCRIPTION, QUERY_INPUT, memory_query, app_bound=True)


__all__ = ["QUERY", "QUERY_DESCRIPTION", "QUERY_INPUT", "memory_query", "synthesize_flag", "tool_spec"]
