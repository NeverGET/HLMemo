"""Shared helpers for the MCP wire tests (G2 wire, G7 server): in-process app + raw JSON-RPC.

The Starlette app is driven through `httpx.ASGITransport`; the lifespan (pool, admin binding,
MCP session manager) is entered explicitly per "server start". `mcp_rpc()` speaks raw JSON-RPC to
`/mcp` so the tests can assert the exact wire shape (D-024 (6)); `sdk_client()` wraps the same
ASGI app in the official `mcp` client for the protocol-level tests.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from hlmemo.config import get_settings
from hlmemo.server.app import create_app

ADMIN_TOKEN = "hlm_" + "M" * 43
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


@contextlib.asynccontextmanager
async def running_app(
    db_dsn: str, *, admin_token: str | None = ADMIN_TOKEN
) -> AsyncIterator[httpx.AsyncClient]:
    settings = get_settings(db_dsn=db_dsn, admin_token=admin_token, registration_secret=None)
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


# --------------------------------------------------------------------------- onboarding


async def register(client: httpx.AsyncClient, name: str) -> tuple[int, str]:
    body = {"name": name, "fingerprint": f"fp-{name}-{uuid.uuid4()}", "os": "darwin", "client": "pytest/0"}
    r = await client.post("/devices/register", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    return data["device"]["id"], data["token"]


async def create_project(client: httpx.AsyncClient, slug: str) -> int:
    r = await client.post(
        "/admin/projects", json={"slug": slug, "name": slug.title()}, headers=bearer(ADMIN_TOKEN)
    )
    assert r.status_code == 201, r.text
    return r.json()["project"]["id"]


async def trusted_device(
    client: httpx.AsyncClient,
    name: str,
    grants: list[dict[str, str]] | None = None,
    device_class: str = "personal",
) -> tuple[int, str]:
    did, tok = await register(client, name)
    body: dict[str, Any] = {"class": device_class}
    if grants is not None:
        body["grants"] = grants
    r = await client.post(f"/admin/devices/{did}/approve", json=body, headers=bearer(ADMIN_TOKEN))
    assert r.status_code == 200, r.text
    return did, tok


async def writer_on(client: httpx.AsyncClient, slug: str, name: str = "writer") -> str:
    """Create `slug` and a trusted device holding `write` on it; returns the device token."""
    await create_project(client, slug)
    _did, tok = await trusted_device(client, name, grants=[{"project": slug, "role": "write"}])
    return tok


# --------------------------------------------------------------------------- raw JSON-RPC


async def mcp_rpc(
    client: httpx.AsyncClient,
    token: str | None,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    id: int = 1,
) -> httpx.Response:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return await client.post("/mcp", json=msg, headers={**MCP_HEADERS, **bearer(token)})


async def call_tool_raw(
    client: httpx.AsyncClient, token: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """`tools/call` over the wire; returns the JSON-RPC `result` object (never unwrapped)."""
    r = await mcp_rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body, body  # tool failures are results with isError, never RPC errors
    return body["result"]


def write_args(project: str, items: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"project": project, "request_id": str(uuid.uuid4()), "client": "pytest/0", "items": items, **kw}


def fact(title: str, body: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "fact", "title": title, "body": body, **kw}


# --------------------------------------------------------------------------- SDK client


@contextlib.asynccontextmanager
async def sdk_client(app: Starlette, token: str) -> AsyncIterator[Client]:
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=bearer(token)
    )
    async with http:
        async with Client(streamable_http_client("http://test/mcp", http_client=http)) as client:
            yield client
