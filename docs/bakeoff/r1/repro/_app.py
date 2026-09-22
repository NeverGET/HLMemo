"""In-process app with overridable settings (copy of test_g5_auth.running_app)."""
import contextlib
from typing import Any

import httpx

from hlmemo.config import get_settings
from hlmemo.server.app import create_app
from tests.integration._mcp_fixtures import ADMIN_TOKEN


@contextlib.asynccontextmanager
async def app_client(db_dsn: str, *, register_rate_limit=None, client_addr=("127.0.0.1", 123), wrap=None, **kw: Any):
    kw.setdefault("registration_secret", None)
    settings = get_settings(db_dsn=db_dsn, admin_token=ADMIN_TOKEN, **kw)
    app = create_app(settings, register_rate_limit=register_rate_limit)
    async with app.router.lifespan_context(app):
        asgi = wrap(app) if wrap else app
        transport = httpx.ASGITransport(app=asgi, client=client_addr)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client
