"""Shared helpers for the W1.5 integration tests: projects created through the ops path (so they
carry the D-015 skeleton card), trusted devices with grants, and an in-process ``call(tool, args)``
that drives the same services the MCP handlers call (one transaction + commit per call)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import psycopg

from hlmemo.auth.context import AuthContext, Role
from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.core.errors import ToolError
from hlmemo.core.export_service import export
from hlmemo.core.read_service import default_read_deps, drilldown, query, raw
from hlmemo.core.write_service import call_the_day, write
from hlmemo.ops import service as ops
from hlmemo.server.tools.handlers import as_result_dict

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "import"


async def make_project(connect, slug: str) -> tuple[int, int]:  # noqa: ANN001
    """``ops project create`` (project_created + grant + skeleton card, one transaction)."""
    async with await connect() as conn:
        out = await ops.project_create(conn, slug, slug.title())
        await conn.commit()
    async with await connect() as conn:
        cur = await conn.execute("SELECT project_id, card_logical_id FROM projects WHERE slug = %s", (slug,))
        pid, card = await cur.fetchone()
    assert out["created"]
    return pid, card


async def make_device(
    connect,  # noqa: ANN001
    name: str,
    grants: dict[int, str],
    *,
    device_class: str = "personal",
) -> AuthContext:
    async with await connect() as conn:
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id) VALUES (%s, %s, %s, %s, 'trusted', now(), 1) RETURNING device_id",
            (name, device_class, f"fp-{name}", f"h-{name}"),
        )
        (did,) = await cur.fetchone()
        for pid, role in grants.items():
            await conn.execute(
                "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
                " VALUES (%s, %s, %s, 1)",
                (did, pid, role),
            )
        await conn.commit()
    return AuthContext(
        device_id=did,
        device_class=device_class,
        is_admin=False,
        token_generation=1,
        grants={pid: Role(role) for pid, role in grants.items()},
        client="pytest-import/0",
    )


class Caller:
    """``call(tool, args)`` over the services; ToolError → ToolCallError like the MCP client."""

    def __init__(self, connect, ctx: AuthContext, *, write_deps: Any = None) -> None:  # noqa: ANN001
        self.connect = connect
        self.ctx = ctx
        self.deps = default_read_deps()
        self.write_deps = write_deps  # None = write_service.default_deps() (librarian per settings)
        self.calls: list[str] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        async with await self.connect() as conn:
            try:
                if tool == "memory.write":
                    res = as_result_dict(await write(conn, self.ctx, args, raw=args, deps=self.write_deps))
                elif tool == "memory.call_the_day":
                    res = as_result_dict(
                        await call_the_day(conn, self.ctx, args, raw=args, deps=self.write_deps)
                    )
                elif tool == "hlm.export":
                    res = await export(conn, self.ctx, args, deps=self.deps)
                elif tool == "memory.raw":
                    res = await raw(conn, self.ctx, args, deps=self.deps)
                elif tool == "memory.query":
                    res = await query(conn, self.ctx, args, deps=self.deps)
                elif tool == "memory.drilldown":
                    res = await drilldown(conn, self.ctx, args, deps=self.deps)
                else:
                    raise AssertionError(tool)
                await conn.commit()
            except ToolError as exc:
                await conn.rollback()
                raise ToolCallError(exc.code, exc.message, details=exc.details) from exc
        return res

    def writes(self) -> int:
        return self.calls.count("memory.write")


def copy_fixture(tmp: Path, name: str = "repo") -> Path:
    dst = tmp / name
    shutil.copytree(FIXTURE / name, dst)
    return dst


async def scalar(connect, sql: str, params: tuple[Any, ...] = ()) -> Any:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return row[0] if row else None


async def rows(connect, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


__all__ = ["FIXTURE", "Caller", "copy_fixture", "make_device", "make_project", "psycopg", "rows", "scalar"]
