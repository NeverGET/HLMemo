"""Tool handlers: ``(conn, ctx, arguments) -> result dict`` (PHASE0-SPEC §3).

Each handler receives the request transaction connection and the request's ``AuthContext`` (both
derived by ``server/middleware.py`` from the bearer, never from the payload) and delegates to the
core service. Errors surface as ``ToolError``/``HlmError``; ``server/mcp_server.py`` turns them
into the ``isError`` envelope.

``memory.query`` / ``memory.drilldown`` / ``memory.raw`` call ``hlmemo.core.read_service`` when
it is importable (contract: ``query/drilldown/raw(conn, ctx, req) -> mapping | pydantic model``).
Until that module lands they validate the budget range (so ``E_BUDGET_*`` keep their spec
meaning) and answer ``E_UNAVAILABLE`` (retryable).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Mapping
from types import ModuleType
from typing import Any, Protocol

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core import write_service
from hlmemo.core.budget import BudgetError, validate_budget
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps

Handler = Callable[[AsyncConnection, AuthContext, dict[str, Any]], Awaitable[dict[str, Any]]]


class ReadHandler(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        ctx: AuthContext,
        args: dict[str, Any],
        *,
        deps: ReadDeps | None = None,
    ) -> dict[str, Any]: ...


def _load_read_service() -> ModuleType | None:
    try:
        return importlib.import_module("hlmemo.core.read_service")
    except ImportError:
        return None


read_service: ModuleType | None = _load_read_service()
READ_SERVICE_AVAILABLE: bool = read_service is not None


def as_result_dict(result: Any) -> dict[str, Any]:
    """Normalise a service result (pydantic model or mapping) to the dict that gets serialised."""
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, Mapping):
        return dict(result)
    raise TypeError(f"unsupported tool result type {type(result).__name__}")


# --------------------------------------------------------------------------- write side


async def memory_write(conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    return as_result_dict(await write_service.write(conn, ctx, args, raw=args))


async def memory_call_the_day(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]
) -> dict[str, Any]:
    return as_result_dict(await write_service.call_the_day(conn, ctx, args, raw=args))


# --------------------------------------------------------------------------- read side


async def _read(
    name: str, conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any], deps: ReadDeps | None
) -> dict[str, Any]:
    if read_service is None:
        try:  # keep the §3 budget errors meaningful even while the service is pending
            validate_budget(args.get("token_budget"))
        except BudgetError as exc:
            raise ToolError(exc.code, str(exc), **exc.details) from exc
        raise ToolError("E_UNAVAILABLE", f"memory.{name}: read_service pending", tool=f"memory.{name}")
    fn = getattr(read_service, name)
    return as_result_dict(await fn(conn, ctx, args, deps=deps))


async def memory_query(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any], *, deps: ReadDeps | None = None
) -> dict[str, Any]:
    return await _read("query", conn, ctx, args, deps)


async def memory_drilldown(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any], *, deps: ReadDeps | None = None
) -> dict[str, Any]:
    return await _read("drilldown", conn, ctx, args, deps)


async def memory_raw(
    conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any], *, deps: ReadDeps | None = None
) -> dict[str, Any]:
    return await _read("raw", conn, ctx, args, deps)


__all__ = [
    "READ_SERVICE_AVAILABLE",
    "Handler",
    "as_result_dict",
    "memory_call_the_day",
    "memory_drilldown",
    "memory_query",
    "memory_raw",
    "memory_write",
]
