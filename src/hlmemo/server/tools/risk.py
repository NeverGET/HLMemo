"""W2d tools: ``memory.risk_check`` and ``memory.register_lesson`` (PHASE2-4-ROADMAP W2d, CC-4).

Registered with one line in ``server/tools/__init__.py`` (``*risk.tool_specs(ToolSpec)``). The
risk check is ``app_bound``: ``server/mcp_server.py`` passes the Starlette app, from which it
takes the lifespan's shared ``ReadDeps`` (embedder, meter, DF cache) and the app's ``RiskJudge``
(built on first use from ``app.state.settings``). Descriptions and schemas are kept
short: ``tools/list`` must stay ≤ 3,000 o200k tokens with all nine CC-4 tools (G-SURF).
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core import lesson_service, risk_service
from hlmemo.core.errors import ToolError
from hlmemo.server.tools.schemas import DEFS

RISK_CHECK = "memory.risk_check"
REGISTER_LESSON = "memory.register_lesson"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Self-contained like the §3 schemas, but single-use definitions are inlined (G-SURF)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


RISK_CHECK_INPUT = _schema(
    {
        "project": DEFS["Slug"],
        "task": {"type": "string", "minLength": 1, "maxLength": 4000},
        "token_budget": DEFS["Budget"],
        "mode": {"enum": ["auto", "deterministic"], "default": "auto"},
    },
    ["project", "task", "token_budget"],
)

REGISTER_LESSON_INPUT = _schema(
    {
        "project": DEFS["Slug"],
        "request_id": DEFS["Uuid"],
        "mistake": {"type": "string", "maxLength": 16000},
        "fix": {"type": "string", "maxLength": 16000},
        "context": {"type": "string", "maxLength": 16000},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
        "device_scope": {**DEFS["DeviceScope"], "default": "all"},
    },
    ["project", "request_id", "mistake", "fix"],
)

RISK_CHECK_DESCRIPTION = (
    "Check a planned task against past lessons you can read (all granted projects). warn lists "
    "warnings to heed; no_matching_evidence is not a safety guarantee; judged=false: retrieval only."
)
REGISTER_LESSON_DESCRIPTION = (
    "Record a lesson from a mistake (mistake/fix/context); idempotent per request_id."
)


def _app_state(app: Any) -> Any:
    if app is None:
        raise ToolError("E_UNAVAILABLE", "tool needs the server application", retryable=True)
    return app.state


async def memory_risk_check(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any], *, app: Any = None
) -> dict[str, Any]:
    from hlmemo.librarian.risk_judge import app_judge

    state = _app_state(app)
    deps = getattr(state, "read_deps", None)
    if deps is None:
        raise ToolError("E_UNAVAILABLE", "read dependencies are not initialized", retryable=True)
    judge = app_judge(app) if args.get("mode", "auto") != "deterministic" else None
    return await risk_service.risk_check(conn, ctx, args, deps=deps, judge=judge)


async def memory_register_lesson(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]
) -> dict[str, Any]:
    return await lesson_service.register_lesson(conn, ctx, args)


def tool_specs(spec_cls: Any) -> tuple[Any, ...]:
    """The two W2d ``ToolSpec`` rows (the class is passed in to avoid an import cycle)."""
    return (
        spec_cls(RISK_CHECK, RISK_CHECK_DESCRIPTION, RISK_CHECK_INPUT, memory_risk_check, app_bound=True),
        spec_cls(REGISTER_LESSON, REGISTER_LESSON_DESCRIPTION, REGISTER_LESSON_INPUT, memory_register_lesson),
    )


__all__ = [
    "REGISTER_LESSON",
    "REGISTER_LESSON_INPUT",
    "RISK_CHECK",
    "RISK_CHECK_INPUT",
    "memory_register_lesson",
    "memory_risk_check",
    "tool_specs",
]
