"""W0a gates G-W0-1..G-W0-6 (PHASE2-4-ROADMAP §1 W0a, D-052/D-061).

The app runs in production access mode (`registration_mode=closed`, `admin_http=disabled`)
in-process; devices are minted with the operator service (`hlmemo.ops`) on the same test DB.
G-W0-7/8 (deploy tooling) live in tests/deploy/test_w0_access.py; G-W0-9 is remote.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from hlmemo.auth.context import AuthContext
from hlmemo.auth.cursors import sign_cursor, verify_cursor
from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import resolve
from hlmemo.config import get_settings
from hlmemo.db.replay import rebuild_projections
from hlmemo.ops import service as ops
from hlmemo.server.app import UnsafeConfigError, create_app, route_table
from hlmemo.server.middleware import route_closed
from tests.integration._write_fixtures import dump_projections

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PROD = {"registration_mode": "closed", "admin_http": "disabled"}
ADMIN_SHAPED = secrets.token_hex(32)  # the retired HLM_ADMIN_TOKEN format
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "w0", "version": "1"},
    },
}


def bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


@contextlib.asynccontextmanager
async def running_app(db_dsn: str, **settings_kw: Any) -> AsyncIterator[httpx.AsyncClient]:
    kw = {**PROD, "admin_token": ADMIN_SHAPED, "registration_secret": None, **settings_kw}
    app = create_app(get_settings(db_dsn=db_dsn, **kw), register_rate_limit=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def op(connect, fn, *args: Any, **kw: Any) -> Any:
    """One operator transaction (what `python -m hlmemo.ops` does), committed."""
    async with await connect() as conn:
        out = await fn(conn, *args, **kw)
        await conn.commit()
        return out


async def mint(connect, name: str, *grants: str, expires: str | None = None) -> tuple[int, str]:
    minted = await op(
        connect, ops.mint, name=name, device_class="personal", grants=list(grants), expires=expires
    )
    return minted.device["id"], minted.token


async def scalar(connect, sql: str, *params: Any) -> Any:
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        return (await cur.fetchone())[0]


async def call_tool(client: httpx.AsyncClient, token: str, name: str, arguments: dict[str, Any]) -> dict:
    r = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={**MCP_HEADERS, **bearer(token)},
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    return json.loads(result["content"][0]["text"])


def write_args(project: str, title: str) -> dict[str, Any]:
    return {
        "project": project,
        "request_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "items": [{"kind": "fact", "title": title, "body": f"W0 body for {title}"}],
    }


APP_FOR_TABLE = create_app(get_settings(db_dsn="postgresql://x/x", **PROD))
ROUTES = route_table(APP_FOR_TABLE)
CLOSED_ROUTES = [(m, p) for m, p in ROUTES if route_closed(p, APP_FOR_TABLE.state.settings)]


def test_route_table_covers_the_closed_set() -> None:
    """Every build_routes() entry is either public or closed exactly as the roadmap table says."""
    closed_paths = {p for _, p in CLOSED_ROUTES}
    assert closed_paths == {
        "/devices/register",
        "/devices/approve",
        "/devices/grant",
        "/devices/list",
        "/admin/devices/2/approve",
        "/admin/devices/2/revoke",
        "/admin/projects",
        "/admin/projects/proj/grants",
    }
    public = {p for _, p in ROUTES} - closed_paths
    assert public == {"/health", "/ready", "/devices/revoke", "/devices/whoami", "/mcp"}


# --------------------------------------------------------------------------- G-W0-1


async def _call_without_body(app: Any, method: str, path: str, token: str | None) -> tuple[int, bytes]:
    """Drive the ASGI app with a receive() that raises if it is ever awaited."""

    async def receive() -> dict:
        raise AssertionError(f"body read on closed route {method} {path}")

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    headers = [(b"content-type", b"application/json"), (b"content-length", b"1048576")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("203.0.113.9", 5555),
        "server": ("test", 80),
        "app": app,
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


async def test_closed_routes_never_read_body(db_dsn, connect) -> None:
    """G-W0-1: an ASGI receive() that raises if awaited; 404, no exception, no row, no event."""
    variants = [
        *CLOSED_ROUTES,
        ("POST", "/devices/register/"),
        ("POST", "//admin/projects"),
        ("GET", "/admin"),
    ]
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        _, trusted = await mint(connect, "w0-body-trusted")
        devices_before = await scalar(connect, "SELECT count(*) FROM devices")
        events_before = await scalar(connect, "SELECT count(*) FROM events")
        for token in (None, trusted, "hlm_" + "j" * 43, ADMIN_SHAPED):
            for method, path in variants:
                status, body = await _call_without_body(app, method, path, token)
                assert status == 404, (method, path, token is not None)
                assert json.loads(body)["code"] == "E_NOT_FOUND"
        assert await scalar(connect, "SELECT count(*) FROM devices") == devices_before
        assert await scalar(connect, "SELECT count(*) FROM events") == events_before


# --------------------------------------------------------------------------- G-W0-2


def _expected(method: str, path: str, kind: str) -> int | None:
    """The roadmap table; None = 'per grants' (checked separately)."""
    if (method, path) in CLOSED_ROUTES:
        return 404
    if path in ("/health", "/ready"):
        return 200 if kind in ("anonymous", "trusted") else 401
    if path == "/devices/whoami":
        return 200 if kind == "trusted" else 401
    if path == "/devices/revoke":
        return 404 if kind == "trusted" else 401  # body id = another device (self-only)
    if path == "/mcp":
        return None if kind == "trusted" else 401
    raise AssertionError(f"route {method} {path} missing from the W0a table")


@pytest.mark.parametrize("kind", ["anonymous", "trusted", "junk", "admin-shaped"])
async def test_route_table_production_mode(db_dsn, connect, kind: str) -> None:
    """G-W0-2: every build_routes() entry x bearer kind equals the roadmap table (production mode).

    The admin-shaped bearer is the configured HLM_ADMIN_TOKEN: with admin HTTP disabled device 1
    is never bound, so it is just an unknown token (D-061)."""
    async with running_app(db_dsn) as client:
        other, _ = await mint(connect, "w0-table-other")
        _, trusted = await mint(connect, "w0-table-trusted")
        token = {
            "anonymous": None,
            "trusted": trusted,
            "junk": "hlm_" + "k" * 43,
            "admin-shaped": ADMIN_SHAPED,
        }[kind]
        mismatches = []
        for method, path in ROUTES:
            if (method, path, kind) == ("GET", "/mcp", "trusted"):
                # The persistent SSE channel never finishes under ASGITransport; POST initialize
                # (below) and the live-listener RG-routes check cover the trusted /mcp row.
                continue
            body = {"id": other} if path == "/devices/revoke" else ({} if method != "GET" else None)
            headers = {**MCP_HEADERS, **bearer(token)} if path == "/mcp" else bearer(token)
            if path == "/mcp" and method == "POST":
                body = INIT
            r = await client.request(method, path, json=body, headers=headers)
            want = _expected(method, path, kind)
            if want is None:
                ok = r.status_code not in (401, 403, 404) and (method != "POST" or r.status_code == 200)
            else:
                ok = r.status_code == want
            if not ok:
                mismatches.append((method, path, r.status_code, want, r.text[:120]))
        assert not mismatches, mismatches
        # The self-only revoke above never touched the other device.
        assert await scalar(connect, "SELECT status FROM devices WHERE device_id = %s", other) == "trusted"
        admin = await scalar(connect, "SELECT token_sha256 FROM devices WHERE device_id = 1")
        assert admin == "reserved:admin", "device 1 must stay disabled in production"


# --------------------------------------------------------------------------- G-W0-3


@pytest.mark.parametrize(
    "unsafe",
    [
        {"registration_mode": "open"},
        {"registration_mode": "secret"},
        {"admin_http": "enabled"},
        {"registration_mode": "open", "admin_http": "enabled"},
    ],
)
async def test_production_refuses_unsafe_config(db_dsn, unsafe: dict[str, str]) -> None:
    """G-W0-3: HLM_DEPLOYMENT=production + open/secret registration or admin HTTP: lifespan fails,
    /ready answers 503 "unsafe config"."""
    settings = get_settings(db_dsn=db_dsn, deployment="production", **{**PROD, **unsafe})
    app = create_app(settings, register_rate_limit=None)
    with pytest.raises(UnsafeConfigError, match="unsafe config"):
        async with app.router.lifespan_context(app):
            pass
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["access_config"]["error"] == "unsafe config"  # loopback peer: details
    # Sol 34 #6: any non-loopback peer (Caddy, the internet) gets the status only.
    public = httpx.ASGITransport(app=app, client=("203.0.113.5", 40000))
    async with httpx.AsyncClient(transport=public, base_url="http://test") as client:
        r = await client.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}
    # The safe production config starts and reports the access check as ok.
    safe = create_app(get_settings(db_dsn=db_dsn, deployment="production", **PROD), register_rate_limit=None)
    async with safe.router.lifespan_context(safe):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=safe), base_url="http://test"
        ) as client:
            assert (await client.get("/ready")).json()["checks"]["access_config"] == {"ok": True}
        proxied = httpx.ASGITransport(app=safe, client=("172.30.39.2", 40000))  # Caddy's subnet
        async with httpx.AsyncClient(transport=proxied, base_url="http://test") as client:
            r = await client.get("/ready")
            assert set(r.json()) == {"status"} and r.json()["status"] in ("ready", "not_ready")


def test_production_env_refusal_via_process(db_dsn) -> None:
    """The env-var path the container uses: HLM_DEPLOYMENT=production with open registration."""
    code = (
        "import asyncio\n"
        "from hlmemo.server.app import create_app\n"
        "app = create_app()\n"
        "async def main():\n"
        "    async with app.router.lifespan_context(app):\n"
        "        pass\n"
        "asyncio.run(main())\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HLM_DB_DSN": db_dsn,
        "HLM_DEPLOYMENT": "production",
        "HLM_REGISTRATION_MODE": "open",
        "HLM_ADMIN_HTTP": "disabled",
    }
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert "unsafe config: HLM_REGISTRATION_MODE=open" in proc.stderr


# --------------------------------------------------------------------------- G-W0-4


async def test_ops_mint_roundtrip(db_dsn, connect) -> None:
    """G-W0-4: mint -> trusted -> granted project OK; other project E_FORBIDDEN_PROJECT;
    revoke -> 401. Events recorded (device 1, hlm-ops client); replay identical."""
    await op(connect, ops.project_create, "w0-granted", "Granted")
    await op(connect, ops.project_create, "w0-other", "Other")
    device_id, token = await mint(connect, "w0-laptop", "w0-granted:write", expires="2h")
    async with running_app(db_dsn) as client:
        health = (await client.get("/health", headers=bearer(token))).json()
        assert health["device"]["status"] == "trusted" and health["device"]["id"] == device_id
        who = (await client.get("/devices/whoami", headers=bearer(token))).json()
        assert who["grants"] == [
            {"project": "w0-granted", "project_id": who["grants"][0]["project_id"], "role": "write"}
        ]
        ok = await call_tool(client, token, "memory.write", write_args("w0-granted", "w0 granted write"))
        assert ok.get("versions"), ok
        denied = await call_tool(client, token, "memory.write", write_args("w0-other", "w0 other write"))
        assert denied["code"] == "E_FORBIDDEN_PROJECT"
        await op(connect, ops.revoke, "w0-laptop", settings=client.app.state.settings)  # type: ignore[attr-defined]
        r = await client.get("/devices/whoami", headers=bearer(token))
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"
        r = await client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **bearer(token)})
        assert r.status_code == 401
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT kind, device_id, client, payload FROM events WHERE kind <> 'write' ORDER BY event_id"
        )
        rows = await cur.fetchall()
    kinds = [r[0] for r in rows]
    assert kinds == [
        "project_created",
        "grant_added",
        "project_created",
        "grant_added",
        "device_minted",
        "grant_added",
        "device_revoked",
        "grant_revoked",
    ]
    assert {r[1] for r in rows} == {1} and all(r[2].startswith("hlm-ops/") for r in rows)
    minted = next(r[3] for r in rows if r[0] == "device_minted")
    assert minted["resolved"]["device_id"] == device_id and minted["resolved"]["via"] == "mint"
    assert token not in json.dumps([r[3] for r in rows]), "a token must never enter an event"
    assert await scalar(connect, "SELECT count(*) FROM events WHERE kind = 'write'") == 1
    async with await connect() as conn:
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_ops_cli_prints_only_the_token(db_dsn, connect) -> None:
    """`python -m hlmemo.ops device mint` stdout is exactly the token; metadata goes to stderr."""
    # HLM_API_PORT=9: `status` reads /ready on the loopback listener; never probe the dev stack here.
    env = {"PATH": "/usr/bin:/bin", "HLM_DB_DSN": db_dsn, "HLM_API_PORT": "9"}
    run = lambda *a: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "hlmemo.ops", *a], env=env, capture_output=True, text=True, timeout=60
    )
    assert run("project", "create", "w0-cli", "--name", "CLI").returncode == 0
    assert run("project", "create", "w0-cli", "--exists-ok").returncode == 0
    assert run("project", "create", "w0-cli").returncode == 2  # exists, no --exists-ok
    proc = run(
        "device",
        "mint",
        "--name",
        "w0-cli-dev",
        "--class",
        "ci",
        "--grant",
        "w0-cli:read",
        "--expires",
        "30m",
    )
    assert proc.returncode == 0, proc.stderr
    token = proc.stdout.strip()
    assert proc.stdout == token + "\n" and token.startswith("hlm_") and len(token) == 47
    meta = json.loads(proc.stderr.strip().splitlines()[-1])
    assert meta["minted"]["name"] == "w0-cli-dev" and meta["grants"] == [
        {"project": "w0-cli", "role": "read"}
    ]
    assert token not in proc.stderr
    listed = json.loads(run("device", "list", "--json").stdout)["devices"]
    assert [d["name"] for d in listed] == ["admin", "w0-cli-dev"]
    assert run("device", "revoke", "1").returncode == 1  # device 1 is reserved
    rotated = run("device", "rotate", "w0-cli-dev")
    assert rotated.returncode == 0 and rotated.stdout.strip() != token
    assert run("device", "mint", "--name", "w0-cli-dev", "--class", "ci").returncode == 2  # name taken
    assert run("device", "revoke", "w0-cli-dev").returncode == 0
    status = json.loads(run("status", "--json").stdout)
    assert status["devices"] == {"revoked": 1} and status["migration"] == ["0005_w0_access"]
    assert status["ready"]["status"] == "unreachable"  # no API on the loopback port in this test


# --------------------------------------------------------------------------- G-W0-5


async def test_expired_device_rejected(db_dsn, connect) -> None:
    """G-W0-5 (as aligned by D-061 / Sol 34 #5): expired == revoked at the pre-body gate and
    in-transaction. Authentication precedes cursor verification, so a cursor presented with the
    expired bearer fails with E_AUTH (401) and is never examined. Renewal is an operator rotation
    (token_generation + 1), after which the pre-expiry cursor fails with E_INVALID_CURSOR."""
    await op(connect, ops.project_create, "w0-exp", "Expiry")
    device_id, token = await mint(connect, "w0-expiring", "w0-exp:write", expires="1h")
    async with running_app(db_dsn) as client:
        assert (await client.get("/devices/whoami", headers=bearer(token))).status_code == 200
        me = (await client.get("/devices/whoami", headers=bearer(token))).json()["device"]
        ctx = AuthContext(
            device_id=device_id,
            device_class="personal",
            is_admin=False,
            token_generation=me["token_generation"],
        )
        secret = client.app.state.cursor_secret  # type: ignore[attr-defined]
        cursor = sign_cursor(secret, ctx, {"h": "x", "i": 0})
        written = await call_tool(client, token, "memory.write", write_args("w0-exp", "w0 expiry item"))
        version_id = written["versions"][0]["version_id"]
        async with await connect() as conn:
            await conn.execute(
                "UPDATE devices SET expires_at = now() - interval '1 second' WHERE device_id = %s",
                (device_id,),
            )
            await conn.commit()
        # Pre-body gate (no body is read for an expired bearer) ...
        for method, path, body in (
            ("GET", "/devices/whoami", None),
            ("POST", "/mcp", INIT),
            ("POST", "/devices/revoke", {"id": device_id}),
        ):
            r = await client.request(method, path, json=body, headers={**MCP_HEADERS, **bearer(token)})
            assert r.status_code == 401 and r.json()["code"] == "E_AUTH", (path, r.text)
        # A cursor presented with the expired bearer: auth fails first -> E_AUTH, never the cursor.
        raw_call = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "memory.raw",
                "arguments": {
                    "project": "w0-exp",
                    "version_id": version_id,
                    "cursor": cursor,
                    "token_budget": 2000,
                },
            },
        }
        r = await client.post("/mcp", json=raw_call, headers={**MCP_HEADERS, **bearer(token)})
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"
        health = (await client.get("/health", headers=bearer(token))).json()["device"]
        assert health["status"] == "revoked"
        # ... and in-transaction (the authoritative resolve under FOR SHARE).
        async with await connect() as conn:
            with pytest.raises(HlmError) as ei:
                await resolve(conn, token)
            assert ei.value.code == "E_AUTH" and "expired" in ei.value.message
        # Renewal is an operator rotation: new token, generation + 1 -> the old cursor is dead.
        renewed = await op(
            connect,
            ops.rotate,
            "w0-expiring",
            settings=client.app.state.settings,
            expires="1h",  # type: ignore[attr-defined]
        )
        assert (await client.get("/devices/whoami", headers=bearer(token))).status_code == 401
        who = await client.get("/devices/whoami", headers=bearer(renewed.token))
        assert who.status_code == 200
        new_ctx = AuthContext(
            device_id=device_id,
            device_class="personal",
            is_admin=False,
            token_generation=who.json()["device"]["token_generation"],
        )
        assert new_ctx.token_generation == ctx.token_generation + 1
        with pytest.raises(HlmError) as ei:
            verify_cursor(secret, cursor, new_ctx)
        assert ei.value.code == "E_INVALID_CURSOR"
        # ... and end to end: the renewed bearer passes auth, the old cursor is refused.
        refused = await call_tool(client, renewed.token, "memory.raw", raw_call["params"]["arguments"])
        assert refused["code"] == "E_INVALID_CURSOR", refused
    # A rotate of an expired device without a new expiry is refused (it would stay expired).
    async with await connect() as conn:
        await conn.execute(
            "UPDATE devices SET expires_at = now() - interval '1 second' WHERE device_id = %s", (device_id,)
        )
        await conn.commit()
    with pytest.raises(HlmError, match="expired"):
        await op(connect, ops.rotate, "w0-expiring", settings=get_settings())


# --------------------------------------------------------------------------- G-W0-6


async def test_self_revoke_only(db_dsn, connect) -> None:
    """G-W0-6: the caller can revoke only itself; another device's id gets 404 with no difference
    between existing and non-existing ids."""
    victim, victim_token = await mint(connect, "w0-victim")
    caller, caller_token = await mint(connect, "w0-caller")
    async with running_app(db_dsn) as client:
        answers = []
        for target in (victim, 1, 999_999, -5):
            r = await client.post("/devices/revoke", json={"id": target}, headers=bearer(caller_token))
            answers.append((r.status_code, r.json()))
        assert {a[0] for a in answers} == {404}
        assert len({json.dumps(a[1], sort_keys=True) for a in answers}) == 1, answers
        assert await scalar(connect, "SELECT status FROM devices WHERE device_id = %s", victim) == "trusted"
        assert (await client.get("/devices/whoami", headers=bearer(victim_token))).status_code == 200
        assert await scalar(connect, "SELECT count(*) FROM events WHERE kind = 'device_revoked'") == 0
        r = await client.post("/devices/revoke", json={"id": caller}, headers=bearer(caller_token))
        assert r.status_code == 200 and r.json()["device"]["status"] == "revoked"
        assert (await client.get("/devices/whoami", headers=bearer(caller_token))).status_code == 401
    async with await connect() as conn:
        cur = await conn.execute("SELECT device_id, payload FROM events WHERE kind = 'device_revoked'")
        (row,) = await cur.fetchall()
    assert row[0] == caller and row[1]["resolved"]["device_id"] == caller


async def test_dev_mode_keeps_phase0_revoke_contract(db_dsn, connect) -> None:
    """admin_http=enabled (dev): another device's id is still E_FORBIDDEN (Phase-0 G5 unchanged)."""
    victim, _ = await mint(connect, "w0-dev-victim")
    _, caller_token = await mint(connect, "w0-dev-caller")
    async with running_app(db_dsn, registration_mode="open", admin_http="enabled") as client:
        r = await client.post("/devices/revoke", json={"id": victim}, headers=bearer(caller_token))
        assert r.status_code == 403 and r.json()["code"] == "E_FORBIDDEN"
        r = await client.post(
            "/devices/register", json={"name": "w0-dev-reg", "fingerprint": "fp-w0", "client": "t/0"}
        )
        assert r.status_code == 201


async def test_secret_mode_without_secret_refuses_registration(db_dsn) -> None:
    async with running_app(db_dsn, registration_mode="secret", admin_http="enabled") as client:
        r = await client.post(
            "/devices/register", json={"name": "w0-sec", "fingerprint": "fp-sec", "client": "t/0"}
        )
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"


# --------------------------------------------------------------------------- live listener


def _live_route_check(db_dsn: str) -> subprocess.CompletedProcess[str]:
    """Start uvicorn (production access mode) and run check_edge.py --routes --mint-ops against it."""
    import os
    import socket
    import time
    import urllib.request

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": "/usr/bin:/bin",
        "HLM_DB_DSN": db_dsn,
        "HLM_DEPLOYMENT": "production",
        "HLM_REGISTRATION_MODE": "closed",
        "HLM_ADMIN_HTTP": "disabled",
        "HLM_API_HOST": "127.0.0.1",
        "HLM_API_PORT": str(port),
        **{k: v for k, v in os.environ.items() if k in ("HLM_MODELS_DIR", "HOME")},
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "hlmemo.server.app"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                pass
            assert server.poll() is None, server.stderr.read().decode()[-2000:]
            assert time.monotonic() < deadline, "api never became ready (is HLM_MODELS_DIR set?)"
            time.sleep(0.5)
        script = str(ROOT / "deploy/scripts/check_edge.py")
        base = f"http://127.0.0.1:{port}"
        routes = subprocess.run(
            [sys.executable, script, "--routes", "--base", base, "--mint-ops"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        status = subprocess.run(
            [sys.executable, "-m", "hlmemo.ops", "status", "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return routes, status
    finally:
        server.terminate()
        server.wait(timeout=30)


async def test_check_edge_routes_against_a_real_listener(db_dsn, connect) -> None:
    """deploy/scripts/check_edge.py --routes (RG-routes) passes against uvicorn in production mode."""
    import asyncio

    proc, status = await asyncio.to_thread(_live_route_check, db_dsn)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Sol 34 #6: the public route saw status only; operators get the details via hlmemo.ops.
    assert status.returncode == 0, status.stderr
    ready = json.loads(status.stdout)["ready"]
    assert ready["status"] == "ready" and ready["checks"]["migration"]["expected"] == "0005_w0_access"
    assert "RESULT routes PASS" in proc.stdout
    assert "hlm_" not in proc.stdout + proc.stderr, "a token was printed"
    # The checker's device revoked itself through the public self-revoke route.
    assert (
        await scalar(connect, "SELECT count(*) FROM devices WHERE status = 'trusted' AND device_id <> 1") == 0
    )
    assert await scalar(connect, "SELECT count(*) FROM events WHERE kind = 'device_revoked'") == 1


def test_alembic_main_head_from_0001_and_0004() -> None:
    """CC-1 / D-061: `alembic upgrade main@head` works from a database at 0001 or 0004.

    Opt-in: HLM_TEST_SCRATCH_DSN names a DISPOSABLE database (downgraded to base and rebuilt).
    It never creates or drops databases and refuses the protected ones (D-056)."""
    import os

    dsn = os.environ.get("HLM_TEST_SCRATCH_DSN")
    if not dsn:
        pytest.skip("set HLM_TEST_SCRATCH_DSN to a disposable database to run the migration round-trip")
    name = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname")
    assert name not in {"hlm", "hlm_verify", "hlm_retr", "hlm_test"}, f"refusing protected database {name}"
    test_dsn = os.environ.get("HLM_TEST_DSN", "")
    assert name != psycopg.conninfo.conninfo_to_dict(test_dsn).get("dbname"), "scratch DSN must differ"

    def alembic(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HLM_DB_DSN": dsn},
            capture_output=True,
            text=True,
            timeout=180,
        )

    def version() -> list[tuple[str]]:
        with psycopg.connect(dsn) as conn:
            return conn.execute("SELECT version_num FROM alembic_version ORDER BY 1").fetchall()

    for start in ("0001_phase0", "0004_title_norm_fold"):
        with psycopg.connect(dsn, autocommit=True) as conn:  # disposable: start from an empty schema
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        assert alembic("upgrade", start).returncode == 0
        assert version() == [(start,)]
        up = alembic("upgrade", "main@head")
        assert up.returncode == 0, up.stderr
        assert version() == [("0005_w0_access",)]
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name = 'devices' AND column_name = 'expires_at'"
            ).fetchone()
    down = alembic("downgrade", "0004_title_norm_fold")
    assert down.returncode == 0, down.stderr
    assert alembic("upgrade", "phase0@head").returncode == 0  # the old label resolves the same head
    assert version() == [("0005_w0_access",)]
    # device_minted events are authoritative: the downgrade refuses instead of rewriting them.
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO events (device_id, client, request_id, kind, payload, payload_sha256, occurred_at)"
            " VALUES (1, 'hlm-ops/test', gen_random_uuid(), 'device_minted', '{}', 'x', now())"
        )
    refused = alembic("downgrade", "0004_title_norm_fold")
    assert refused.returncode != 0 and "events_kind_check" in refused.stderr
    assert version() == [("0005_w0_access",)]
