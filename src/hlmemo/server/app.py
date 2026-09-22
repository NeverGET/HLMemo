"""HLMemo API (PHASE0-SPEC §2, §6): Starlette app factory.

  create_app(settings) -> Starlette
    lifespan  : open the pool, bind device 1 from HLM_ADMIN_TOKEN in ONE transaction (§2) — before
                uvicorn opens the listener — then serve; close the pool on shutdown.
    middleware: AuthMiddleware (bearer -> AuthContext on request.state, status gate before routing).
    routes    : GET /health (no auth) · /devices/* · /admin/* · /mcp (placeholder, gated, 501).

`python -m hlmemo.server.app` runs uvicorn with settings from HLM_* / hlm.toml.
The real MCP server (`mcp.streamable_http_app("/mcp")`) replaces the /mcp placeholder in a later task;
the middleware already sits in front of it.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hlmemo.auth.cursors import load_cursor_secret
from hlmemo.auth.errors import HlmError
from hlmemo.auth.tokens import hash_token
from hlmemo.config import Settings, get_settings
from hlmemo.db import auth_queries as q
from hlmemo.db.pool import create_pool
from hlmemo.server import admin, devices
from hlmemo.server.common import device_view
from hlmemo.server.middleware import AuthMiddleware, RateLimiter

log = logging.getLogger("hlmemo.server")

ADMIN_DISABLED_WARNING = "admin device disabled: HLM_ADMIN_TOKEN not set"


# --------------------------------------------------------------------------- routes


async def health(request: Request) -> JSONResponse:
    """No auth required. With a bearer (any status) the device is echoed so pending devices can poll."""
    body: dict = {"status": "ok"}
    device = request.state.device
    if device is not None:
        v = device_view(device)
        body["device"] = {"id": v["id"], "name": v["name"], "status": v["status"], "class": v["class"]}
    return JSONResponse(body)


async def mcp_placeholder(request: Request) -> JSONResponse:
    """Gated like the real thing (pending -> 403, revoked -> 401 by the middleware); not implemented yet."""
    err = HlmError("E_UNAVAILABLE", "MCP endpoint not implemented yet")
    return JSONResponse(err.to_dict(), status_code=501)


def build_routes() -> list[Route]:
    return [
        Route("/health", health, methods=["GET"]),
        Route("/devices/register", devices.register, methods=["POST"]),
        Route("/devices/approve", devices.approve, methods=["POST"]),
        Route("/devices/revoke", devices.revoke, methods=["POST"]),
        Route("/devices/grant", devices.grant_add, methods=["POST"]),
        Route("/devices/grant", devices.grant_remove, methods=["DELETE"]),
        Route("/devices/list", devices.list_devices, methods=["GET"]),
        Route("/devices/whoami", devices.whoami, methods=["GET"]),
        Route("/admin/devices/{id}/approve", devices.approve, methods=["POST"]),
        Route("/admin/devices/{id}/revoke", devices.revoke, methods=["POST"]),
        Route("/admin/projects", admin.projects_create, methods=["POST"]),
        Route("/admin/projects", admin.projects_list, methods=["GET"]),
        Route("/admin/projects/{slug}/grants", devices.grant_add, methods=["POST"]),
        Route("/admin/projects/{slug}/grants", devices.grant_remove, methods=["DELETE"]),
        Route("/mcp", mcp_placeholder, methods=["GET", "POST", "DELETE"]),
    ]


_SAMPLE_PARAMS = {"id": "2", "slug": "proj"}


def route_table(app: Starlette) -> list[tuple[str, str]]:
    """Every (METHOD, concrete path) the app serves — G5 parametrises the pending-device test over it."""
    out: list[tuple[str, str]] = []
    for r in app.routes:
        if not isinstance(r, Route):
            continue
        path = r.path
        for name, sample in _SAMPLE_PARAMS.items():
            path = path.replace("{" + name + "}", sample)
        for m in sorted(r.methods or ()):
            if m == "HEAD":
                continue
            out.append((m, path))
    return out


# --------------------------------------------------------------------------- lifespan


async def bind_admin_device(settings: Settings) -> int:
    """§2 start-up binding, one transaction; returns device 1's new token_generation."""
    pool = create_pool(settings)
    await pool.open()
    try:
        async with pool.connection() as conn:
            token_hash = (
                hash_token(settings.admin_token.get_secret_value()) if settings.admin_enabled else None
            )
            gen = await q.bind_admin_token(conn, token_hash)
            await conn.commit()
    finally:
        await pool.close()
    if settings.admin_enabled:
        log.info("admin device bound from HLM_ADMIN_TOKEN (token_generation=%d)", gen)
    else:
        log.warning(ADMIN_DISABLED_WARNING)
    return gen


def create_app(
    settings: Settings | None = None,
    *,
    register_rate_limit: int | None = 5,
) -> Starlette:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # Binding runs (and commits) before the pool used for traffic is opened and before uvicorn
        # starts accepting connections — no request can observe a half-bound device 1.
        app.state.admin_generation = await bind_admin_device(settings)
        pool = create_pool(settings)
        await pool.open()
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = Starlette(
        routes=build_routes(),
        middleware=[Middleware(AuthMiddleware)],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.cursor_secret = load_cursor_secret()
    app.state.register_limiter = RateLimiter(register_rate_limit) if register_rate_limit else None
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    log.warning("hlmemo api: /mcp is a gated placeholder (501); MCP tools land in a later task")
    uvicorn.run(create_app(s), host=s.api_host, port=s.api_port, log_level="info")


if __name__ == "__main__":
    main()
