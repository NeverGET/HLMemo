"""PLACEHOLDER — Phase-0 Task 1 scaffold only.

Serves `GET /health` -> {"status":"starting"} so the compose `api` healthcheck has a target.
The real MCP server (mcp 2.x `MCPServer`, `streamable_http_app("/mcp")`, auth middleware,
/admin, /devices) is added by a later task; keep this file tiny until then.
"""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger("hlmemo.server")


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "starting"})


app = Starlette(routes=[Route("/health", health, methods=["GET"])])


def main() -> None:
    import uvicorn

    from hlmemo.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    log.warning("hlmemo api: MCP server not implemented yet; serving placeholder /health only")
    if not s.admin_enabled:
        log.warning("admin device disabled: HLM_ADMIN_TOKEN not set")
    uvicorn.run(app, host=s.api_host, port=s.api_port, log_level="info")


if __name__ == "__main__":
    main()
