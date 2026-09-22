"""G5 auth & device model (PHASE0-SPEC §2, §7): status gate, onboarding, revocation ordering,
cursor binding, admin device binding across restarts.

The app is exercised in-process through `httpx.ASGITransport`; the Starlette lifespan (pool +
admin binding) is entered explicitly per "server start". Restart tests build the app several
times with different settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.cursors import sign_cursor, verify_cursor
from hlmemo.auth.errors import HlmError
from hlmemo.auth.tokens import generate_token, hash_token, looks_like_token
from hlmemo.config import get_settings
from hlmemo.server.app import ADMIN_DISABLED_WARNING, create_app, route_table

pytestmark = pytest.mark.integration

ADMIN_TOKEN = "hlm_" + "A" * 43
CURSOR_SECRET = "g5-test-cursor-secret"
ROUTES = route_table(create_app(get_settings(db_dsn="postgresql://x/x")))


# --------------------------------------------------------------------------- helpers


def bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


@contextlib.asynccontextmanager
async def running_app(
    db_dsn: str, *, admin_token: str | None = ADMIN_TOKEN, **settings_kw: Any
) -> AsyncIterator[httpx.AsyncClient]:
    """One 'server start': lifespan entered (admin binding), an httpx client bound to the ASGI app."""
    settings_kw.setdefault("registration_secret", None)
    settings = get_settings(db_dsn=db_dsn, admin_token=admin_token, **settings_kw)
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def register(client: httpx.AsyncClient, name: str, **extra: Any) -> tuple[int, str]:
    body = {"name": name, "fingerprint": f"fp-{name}-{uuid.uuid4()}", "os": "darwin", "client": "pytest/0"}
    body.update(extra)
    r = await client.post("/devices/register", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    return data["device"]["id"], data["token"]


async def approve(
    client: httpx.AsyncClient,
    device_id: int,
    *,
    token: str = ADMIN_TOKEN,
    device_class: str = "personal",
    grants: list[dict[str, str]] | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"class": device_class}
    if grants is not None:
        body["grants"] = grants
    return await client.post(f"/admin/devices/{device_id}/approve", json=body, headers=bearer(token))


async def trusted_device(
    client: httpx.AsyncClient,
    name: str,
    grants: list[dict[str, str]] | None = None,
    device_class: str = "personal",
) -> tuple[int, str]:
    did, tok = await register(client, name)
    r = await approve(client, did, grants=grants, device_class=device_class)
    assert r.status_code == 200, r.text
    return did, tok


async def create_project(client: httpx.AsyncClient, slug: str) -> int:
    r = await client.post(
        "/admin/projects", json={"slug": slug, "name": slug.title()}, headers=bearer(ADMIN_TOKEN)
    )
    assert r.status_code == 201, r.text
    return r.json()["project"]["id"]


async def device_row(connect, device_id: int) -> dict[str, Any]:
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, token_sha256, token_generation, revoked_at, last_seen_at, class, is_admin"
            " FROM devices WHERE device_id = %s",
            (device_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        keys = (
            "status",
            "token_sha256",
            "token_generation",
            "revoked_at",
            "last_seen_at",
            "class",
            "is_admin",
        )
        return dict(zip(keys, row, strict=True))


async def count(connect, sql: str, *params: Any) -> int:
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        return int((await cur.fetchone())[0])


@pytest.fixture(scope="module", autouse=True)
async def _unbind_admin_after_module(connect) -> AsyncIterator[None]:
    """Leave device 1 as a fresh migration would (placeholder hash) once this module is done."""
    yield
    async with await connect() as conn:
        await conn.execute("UPDATE devices SET token_sha256 = 'reserved:admin' WHERE device_id = 1")
        await conn.commit()


# --------------------------------------------------------------------------- tests


async def test_health_no_auth_and_pending_poll(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        r = await c.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        did, tok = await register(c, "poller")
        assert looks_like_token(tok) and did >= 2
        r = await c.get("/health", headers=bearer(tok))
        assert r.status_code == 200
        assert r.json()["device"] == {"id": did, "name": "poller", "status": "pending", "class": "other"}
        r = await c.get("/health", headers=bearer("hlm_" + "x" * 43))
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"
        # the token is stored only as its sha256
        row = await device_row(connect, did)
        assert row["token_sha256"] == hash_token(tok) and tok not in row["token_sha256"]


async def test_device1_reserved_at_migration(db_dsn, connect) -> None:
    row = await device_row(connect, 1)
    assert row["class"] == "server" and row["is_admin"] is True and row["status"] == "trusted"
    async with running_app(db_dsn) as c:
        # register can never produce device 1 nor its name
        r = await c.post(
            "/devices/register",
            json={"name": "admin", "fingerprint": "fp-admin", "client": "pytest/0"},
        )
        assert r.status_code == 400 and r.json()["code"] == "E_INVALID_ARG"
        did, _ = await register(c, "someone")
        assert did != 1
        me = await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))
        assert me.status_code == 200 and me.json()["device"]["reserved"] is True
        # approve / revoke / grant on id 1 -> E_FORBIDDEN, even for device 1 itself
        r = await approve(c, 1)
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        r = await c.post("/admin/devices/1/revoke", headers=bearer(ADMIN_TOKEN))
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        await create_project(c, "p-reserved")
        r = await c.post(
            "/admin/projects/p-reserved/grants",
            json={"device": 1, "role": "read"},
            headers=bearer(ADMIN_TOKEN),
        )
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        r = await c.request(
            "DELETE", "/admin/projects/p-reserved/grants", json={"device": 1}, headers=bearer(ADMIN_TOKEN)
        )
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
    # placeholder hash restored only when the API starts without the env var (see restart test)
    assert (await device_row(connect, 1))["token_sha256"] == hash_token(ADMIN_TOKEN)


async def test_register_approve_whoami_flow(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        pid = await create_project(c, "flow")
        did, tok = await register(c, "laptop", **{"class": "personal"})
        assert (await c.get("/devices/whoami", headers=bearer(tok))).status_code == 403
        r = await approve(c, did, device_class="work", grants=[{"project": "flow", "role": "write"}])
        assert r.status_code == 200 and r.json()["device"]["status"] == "trusted"
        me = await c.get("/devices/whoami", headers=bearer(tok))
        assert me.status_code == 200
        body = me.json()
        assert body["device"]["class"] == "work" and body["device"]["is_admin"] is False
        assert body["grants"] == [{"project": "flow", "project_id": pid, "role": "write"}]
        assert body["scope"] == ["all", "class:work", f"device:{did}"]
        listed = (await c.get("/devices/list", headers=bearer(tok))).json()["devices"]
        assert [d["id"] for d in listed] == [1, did] and all("token" not in d for d in listed)
        projects = (await c.get("/admin/projects", headers=bearer(tok))).json()["projects"]
        assert [(p["slug"], p["role"]) for p in projects] == [("flow", "write")]
        assert (await c.get("/devices/whoami", headers=bearer(tok))).status_code == 200
    kinds = {}
    async with await connect() as conn:
        cur = await conn.execute("SELECT kind, device_id, project_id FROM events ORDER BY event_id")
        for kind, dev, proj in await cur.fetchall():
            kinds.setdefault(kind, []).append((dev, proj))
    assert kinds["device_registered"] == [(did, None)]
    assert kinds["device_approved"] == [(1, None)]
    assert kinds["grant_added"] == [(1, pid), (1, pid)]  # creator admin grant + embedded approve grant
    assert kinds["project_created"] == [(1, pid)]
    assert (await device_row(connect, did))["last_seen_at"] is not None


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_pending_device_all_http_routes_rejected(db_dsn, method: str, path: str) -> None:
    async with running_app(db_dsn) as c:
        _, tok = await register(c, "pend")
        r = await c.request(
            method, path, headers=bearer(tok), json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        if (method, path) == ("GET", "/health"):
            assert r.status_code == 200 and r.json()["device"]["status"] == "pending"
        else:
            assert r.status_code == 403, (method, path, r.text)
            assert r.json()["code"] == "E_DEVICE_PENDING"
        # a route the table does not know is gated too
        r = await c.get("/nope", headers=bearer(tok))
        assert r.status_code == 403 and r.json()["code"] == "E_DEVICE_PENDING"


async def test_pending_device_mcp_initialize_list_call_rejected(db_dsn) -> None:
    rpc = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "memory.query", "arguments": {}},
        },
    ]
    async with running_app(db_dsn) as c:
        did, pending = await register(c, "mcp-pending")
        for msg in rpc:
            r = await c.post("/mcp", json=msg, headers=bearer(pending))
            assert (r.status_code, r.json()["code"]) == (403, "E_DEVICE_PENDING"), msg["method"]
        rid, revoked = await trusted_device(c, "mcp-revoked")
        assert (await c.post(f"/admin/devices/{rid}/revoke", headers=bearer(ADMIN_TOKEN))).status_code == 200
        for msg in rpc:
            r = await c.post("/mcp", json=msg, headers=bearer(revoked))
            assert (r.status_code, r.json()["code"]) == (401, "E_AUTH"), msg["method"]
        r = await c.post("/mcp", json=rpc[0])
        assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
        # a trusted device reaches the (not yet implemented) endpoint
        r = await c.post("/mcp", json=rpc[0], headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 501 and r.json()["code"] == "E_UNAVAILABLE"


async def test_revoked_token_rejected(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        await create_project(c, "rv")
        did, tok = await trusted_device(c, "gone", grants=[{"project": "rv", "role": "read"}])
        gen_before = (await device_row(connect, did))["token_generation"]
        assert (await c.get("/devices/whoami", headers=bearer(tok))).status_code == 200
        r = await c.post(f"/admin/devices/{did}/revoke", headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 200 and r.json()["revoked_grants"] == 1
        r = await c.get("/devices/whoami", headers=bearer(tok))
        assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
        r = await c.get("/health", headers=bearer(tok))
        assert r.status_code == 200 and r.json()["device"]["status"] == "revoked"
        for missing in ({}, {"Authorization": "Basic abc"}, bearer(generate_token())):
            r = await c.get("/devices/whoami", headers=missing)
            assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
        # second revoke is an error, and a revoked device cannot be approved again
        r = await c.post(f"/admin/devices/{did}/revoke", headers=bearer(ADMIN_TOKEN))
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        r = await approve(c, did)
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
    row = await device_row(connect, did)
    assert row["status"] == "revoked" and row["revoked_at"] is not None
    assert row["token_generation"] == gen_before + 1
    assert (
        await count(
            connect,
            "SELECT count(*) FROM device_project_grants WHERE device_id=%s AND revoked_at IS NULL",
            did,
        )
        == 0
    )
    assert await count(connect, "SELECT count(*) FROM events WHERE kind='device_revoked'") == 1
    assert await count(connect, "SELECT count(*) FROM events WHERE kind='grant_revoked'") == 1


async def test_revoke_restricted_to_admin_or_self(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        await create_project(c, "rr")
        boss_id, boss = await trusted_device(c, "boss", grants=[{"project": "rr", "role": "admin"}])
        peer_id, peer = await trusted_device(c, "peer", grants=[{"project": "rr", "role": "write"}])
        # project admin is NOT enough for a global revoke
        r = await c.post(f"/admin/devices/{peer_id}/revoke", headers=bearer(boss))
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        r = await c.post("/devices/revoke", json={"id": peer_id}, headers=bearer(boss))
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        assert (await device_row(connect, peer_id))["status"] == "trusted"
        # ...but removing that project's grant is
        r = await c.request(
            "DELETE", "/admin/projects/rr/grants", json={"device": peer_id}, headers=bearer(boss)
        )
        assert r.status_code == 200
        assert (await c.get("/devices/whoami", headers=bearer(peer))).json()["grants"] == []
        # self-revoke works
        r = await c.post("/devices/revoke", json={"id": peer_id}, headers=bearer(peer))
        assert r.status_code == 200 and r.json()["device"]["status"] == "revoked"
        assert (await c.get("/devices/whoami", headers=bearer(peer))).status_code == 401
        # device 1 may revoke anyone (but not itself); unknown id -> 404 for device 1
        r = await c.post(f"/admin/devices/{boss_id}/revoke", headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 200
        r = await c.post("/admin/devices/9999/revoke", headers=bearer(ADMIN_TOKEN))
        assert (r.status_code, r.json()["code"]) == (404, "E_NOT_FOUND")


async def test_approve_grants_require_project_admin(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        await create_project(c, "pa")
        await create_project(c, "pb")
        _, approver = await trusted_device(c, "approver", grants=[{"project": "pa", "role": "admin"}])
        _, writer = await trusted_device(c, "writer", grants=[{"project": "pa", "role": "write"}])
        new_id, new_tok = await register(c, "newbie")
        cases = [
            (writer, [{"project": "pa", "role": "read"}], "E_FORBIDDEN_PROJECT"),  # write, not admin
            (approver, [{"project": "pb", "role": "read"}], "E_FORBIDDEN_PROJECT"),  # no grant at all
            (
                approver,
                [{"project": "pa", "role": "read"}, {"project": "pb", "role": "read"}],
                "E_FORBIDDEN_PROJECT",
            ),
            (
                approver,
                [{"project": "nope", "role": "read"}],
                "E_FORBIDDEN_PROJECT",
            ),  # unknown slug, no enumeration
            (
                approver,
                [{"project": "pa", "role": "read"}, {"project": "pa", "role": "write"}],
                "E_INVALID_ARG",
            ),
        ]
        for tok, grants, code in cases:
            r = await approve(c, new_id, token=tok, grants=grants)
            assert r.json()["code"] == code, (grants, r.text)
            assert (await device_row(connect, new_id))["status"] == "pending"
            assert (await c.get("/devices/whoami", headers=bearer(new_tok))).status_code == 403
        assert (
            await count(connect, "SELECT count(*) FROM device_project_grants WHERE device_id=%s", new_id) == 0
        )
        assert (
            await count(
                connect,
                "SELECT count(*) FROM events WHERE kind IN ('device_approved','grant_added')"
                " AND payload->'resolved'->>'device_id' = %s",
                str(new_id),
            )
            == 0
        )
        # a project admin approves with a grant on its own project: atomic success
        r = await approve(c, new_id, token=approver, grants=[{"project": "pa", "role": "write"}])
        assert r.status_code == 200, r.text
        me = (await c.get("/devices/whoami", headers=bearer(new_tok))).json()
        assert [(g["project"], g["role"]) for g in me["grants"]] == [("pa", "write")]
        # approving an already-trusted device -> E_INVALID_ARG
        r = await approve(c, new_id, token=approver)
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        # device 1 may embed grants on any project without holding one
        other_id, other_tok = await register(c, "other")
        r = await approve(c, other_id, grants=[{"project": "pb", "role": "admin"}])
        assert r.status_code == 200
        assert (await c.get("/devices/whoami", headers=bearer(other_tok))).json()["grants"][0][
            "project"
        ] == "pb"


async def test_grant_endpoint_rules(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        pid = await create_project(c, "g1")
        await create_project(c, "g2")
        _, padmin = await trusted_device(c, "padmin", grants=[{"project": "g1", "role": "admin"}])
        tgt_id, tgt = await trusted_device(c, "target")
        h = bearer(padmin)
        r = await c.post("/admin/projects/g2/grants", json={"device": tgt_id, "role": "read"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN_PROJECT")
        r = await c.post("/admin/projects/missing/grants", json={"device": tgt_id, "role": "read"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN_PROJECT")
        r = await c.post("/admin/projects/g1/grants", json={"device": 1, "role": "read"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        r = await c.post("/admin/projects/g1/grants", json={"device": 4242, "role": "read"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (404, "E_NOT_FOUND")
        r = await c.post("/admin/projects/g1/grants", json={"device": tgt_id, "role": "owner"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        r = await c.post(
            "/devices/grant", json={"device": tgt_id, "project": "g1", "role": "read"}, headers=h
        )
        assert r.status_code == 200, r.text
        r = await c.post("/admin/projects/g1/grants", json={"device": tgt_id, "role": "write"}, headers=h)
        assert r.status_code == 200  # re-role
        me = (await c.get("/devices/whoami", headers=bearer(tgt))).json()
        assert me["grants"] == [{"project": "g1", "project_id": pid, "role": "write"}]
        r = await c.request("DELETE", "/devices/grant", json={"device": tgt_id, "project": "g1"}, headers=h)
        assert r.status_code == 200
        r = await c.request("DELETE", "/devices/grant", json={"device": tgt_id, "project": "g1"}, headers=h)
        assert (r.status_code, r.json()["code"]) == (404, "E_NOT_FOUND")
        assert (await c.get("/devices/whoami", headers=bearer(tgt))).json()["grants"] == []
        # the target itself (no admin grant) cannot grant
        r = await c.post(
            "/admin/projects/g1/grants", json={"device": tgt_id, "role": "admin"}, headers=bearer(tgt)
        )
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN_PROJECT")
    assert (
        await count(connect, "SELECT count(*) FROM events WHERE kind='grant_added' AND project_id=%s", pid)
        >= 3
    )
    assert (
        await count(connect, "SELECT count(*) FROM events WHERE kind='grant_revoked' AND project_id=%s", pid)
        == 1
    )


async def test_project_create_requires_device1(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        _, tok = await trusted_device(c, "notadmin")
        r = await c.post("/admin/projects", json={"slug": "nope", "name": "Nope"}, headers=bearer(tok))
        assert (r.status_code, r.json()["code"]) == (403, "E_FORBIDDEN")
        r = await c.post(
            "/admin/projects", json={"slug": "Bad Slug", "name": "x"}, headers=bearer(ADMIN_TOKEN)
        )
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        r = await c.post("/admin/projects", content=b"not json", headers=bearer(ADMIN_TOKEN))
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        pid = await create_project(c, "dup")
        r = await c.post(
            "/admin/projects", json={"slug": "dup", "name": "again"}, headers=bearer(ADMIN_TOKEN)
        )
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        projects = (await c.get("/admin/projects", headers=bearer(ADMIN_TOKEN))).json()["projects"]
        assert [p["id"] for p in projects] == [pid]
        assert (await c.get("/admin/projects", headers=bearer(tok))).json()["projects"] == []
    assert await count(connect, "SELECT count(*) FROM projects") == 1
    assert await count(connect, "SELECT count(*) FROM events WHERE kind='project_created'") == 1
    assert (
        await count(connect, "SELECT count(*) FROM device_project_grants WHERE device_id=1 AND role='admin'")
        == 1
    )


async def test_revocation_ordering_concurrent(db_dsn, connect) -> None:
    """A revoke racing 200 authorized reads/writes: no request that started after the revoke commit
    succeeds, and every grant write either fully happened (row + event) or not at all."""
    n_reads, n_writes = 100, 100
    async with running_app(db_dsn, pool_max_size=8) as c:
        pid = await create_project(c, "race")
        victim_id, victim = await trusted_device(c, "victim", grants=[{"project": "race", "role": "admin"}])
        targets = [(await register(c, f"tgt-{i}"))[0] for i in range(n_writes)]

        results: list[tuple[float, str, int, int | None]] = []  # (t_start, kind, status, target)
        revoke_done_at: list[float] = []

        async def read(i: int) -> None:
            t0 = time.monotonic()
            r = await c.get("/devices/whoami", headers=bearer(victim))
            results.append((t0, "read", r.status_code, None))

        async def write(tgt: int) -> None:
            t0 = time.monotonic()
            r = await c.post(
                "/admin/projects/race/grants", json={"device": tgt, "role": "read"}, headers=bearer(victim)
            )
            results.append((t0, "write", r.status_code, tgt))

        async def revoke() -> None:
            r = await c.post(f"/admin/devices/{victim_id}/revoke", headers=bearer(ADMIN_TOKEN))
            assert r.status_code == 200, r.text
            revoke_done_at.append(time.monotonic())

        reqs = [read(i) for i in range(n_reads)] + [write(t) for t in targets]
        # interleave reads and writes; launch them progressively so the revoke lands mid-stream:
        # 120 before the revoke is issued, 40 while it is in flight, 40 after its commit.
        reqs = [t for pair in zip(reqs[:n_reads], reqs[n_reads:], strict=True) for t in pair]
        pending: list[asyncio.Task[None]] = []

        async def launch(coros) -> None:
            for coro in coros:
                pending.append(asyncio.create_task(coro))
                await asyncio.sleep(0)

        await launch(reqs[:120])
        rev = asyncio.create_task(revoke())
        await launch(reqs[120:160])
        await rev
        await launch(reqs[160:])
        await asyncio.gather(*pending)

    assert len(results) == n_reads + n_writes and revoke_done_at
    t_rev = revoke_done_at[0]
    statuses = {s for _, _, s, _ in results}
    assert statuses <= {200, 401}, statuses
    ok = [r for r in results if r[2] == 200]
    rejected = [r for r in results if r[2] == 401]
    assert ok and rejected, "race not exercised: both outcomes expected"
    for t0, kind, status, _ in results:
        if t0 > t_rev:
            assert status == 401, f"{kind} started after revoke commit but succeeded"
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT device_id FROM device_project_grants WHERE project_id=%s AND revoked_at IS NULL"
            " AND device_id NOT IN (1, %s)",
            (pid, victim_id),
        )
        live_grants = {int(r[0]) for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT (payload->'resolved'->>'device_id')::bigint FROM events"
            " WHERE kind='grant_added' AND project_id=%s AND device_id=%s",
            (pid, victim_id),
        )
        event_targets = {int(r[0]) for r in await cur.fetchall()}
    succeeded = {tgt for _, kind, s, tgt in results if kind == "write" and s == 200}
    failed = {tgt for _, kind, s, tgt in results if kind == "write" and s == 401}
    assert live_grants == succeeded and event_targets == succeeded, "partial write detected"
    assert not (failed & live_grants) and not (failed & event_targets)
    assert (await device_row(connect, victim_id))["status"] == "revoked"


async def test_cursor_bound_to_device_and_generation(db_dsn, connect) -> None:
    secret = b"unit-secret"
    a = AuthContext(device_id=7, device_class="personal", is_admin=False, token_generation=3)
    b = AuthContext(device_id=8, device_class="personal", is_admin=False, token_generation=3)
    a_rotated = AuthContext(device_id=7, device_class="personal", is_admin=False, token_generation=4)
    payload = {"version_id": 42, "ordinal": 5}
    cur = sign_cursor(secret, a, payload)
    assert verify_cursor(secret, cur, a) == payload
    for bad_ctx in (b, a_rotated):
        with pytest.raises(HlmError) as ei:
            verify_cursor(secret, cur, bad_ctx)
        assert ei.value.code == "E_INVALID_CURSOR"
    for garbage in ("", "abc", cur + "x", cur[:-3] + "AAA", cur.split(".")[0] + ".Zm9v", None, 12):
        with pytest.raises(HlmError) as ei:
            verify_cursor(secret, garbage, a)
        assert ei.value.code == "E_INVALID_CURSOR"
    with pytest.raises(HlmError):
        verify_cursor(b"other-secret", cur, a)
    # end to end: a real device's generation moves on revoke, killing its cursors
    async with running_app(db_dsn) as c:
        did, tok = await trusted_device(c, "cursor-dev")
        me = (await c.get("/devices/whoami", headers=bearer(tok))).json()["device"]
        ctx = AuthContext(
            device_id=did, device_class=me["class"], is_admin=False, token_generation=me["token_generation"]
        )
        app_secret = c.app.state.cursor_secret  # type: ignore[attr-defined]
        cur = sign_cursor(app_secret, ctx, payload)
        assert verify_cursor(app_secret, cur, ctx) == payload
        assert (await c.post("/devices/revoke", json={"id": did}, headers=bearer(tok))).status_code == 200
    gen = (await device_row(connect, did))["token_generation"]
    assert gen == me["token_generation"] + 1
    after = AuthContext(device_id=did, device_class=me["class"], is_admin=False, token_generation=gen)
    with pytest.raises(HlmError) as ei:
        verify_cursor(app_secret, cur, after)
    assert ei.value.code == "E_INVALID_CURSOR"


async def test_device1_restart_without_env_disables_admin(db_dsn, connect, monkeypatch, caplog) -> None:
    monkeypatch.setenv("HLM_CURSOR_SECRET", CURSOR_SECRET)
    payload = {"version_id": 1, "ordinal": 0}
    # start 1: env set -> bound, generation +1
    g0 = (await device_row(connect, 1))["token_generation"]
    async with running_app(db_dsn) as c:
        r = await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 200 and r.json()["device"]["is_admin"] is True
        g1 = r.json()["device"]["token_generation"]
        assert g1 == g0 + 1
        secret = c.app.state.cursor_secret  # type: ignore[attr-defined]
        ctx1 = AuthContext(device_id=1, device_class="server", is_admin=True, token_generation=g1)
        cursor = sign_cursor(secret, ctx1, payload)
        assert verify_cursor(secret, cursor, ctx1) == payload
    assert (await device_row(connect, 1))["token_sha256"] == hash_token(ADMIN_TOKEN)
    # start 2: env unset -> placeholder hash, generation +1, WARNING, same bearer 401
    with caplog.at_level(logging.WARNING, logger="hlmemo.server"):
        async with running_app(db_dsn, admin_token=None) as c:
            assert ADMIN_DISABLED_WARNING in caplog.text
            r = await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))
            assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
            r = await c.get("/health", headers=bearer(ADMIN_TOKEN))
            assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
            assert (await c.get("/health")).status_code == 200  # API still serves
            row = await device_row(connect, 1)
            assert row["token_sha256"] == "reserved:admin" and row["token_generation"] == g1 + 1
            assert row["status"] == "trusted" and row["is_admin"] is True
            ctx2 = AuthContext(
                device_id=1, device_class="server", is_admin=True, token_generation=row["token_generation"]
            )
            with pytest.raises(HlmError) as ei:
                verify_cursor(c.app.state.cursor_secret, cursor, ctx2)  # type: ignore[attr-defined]
            assert ei.value.code == "E_INVALID_CURSOR"
    # start 3: env set again -> works, generation +1 again (cursors never survive a restart)
    async with running_app(db_dsn) as c:
        r = await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))
        assert r.status_code == 200 and r.json()["device"]["token_generation"] == g1 + 2
        ctx3 = AuthContext(device_id=1, device_class="server", is_admin=True, token_generation=g1 + 2)
        with pytest.raises(HlmError):
            verify_cursor(c.app.state.cursor_secret, cursor, ctx3)  # type: ignore[attr-defined]
    # start 4: same token again -> still generation +1 (bumped on EVERY start)
    async with running_app(db_dsn) as c:
        r = await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))
        assert r.json()["device"]["token_generation"] == g1 + 3


async def test_admin_bypasses_grants_not_device_scope(db_dsn, connect) -> None:
    async with running_app(db_dsn) as c:
        pid = await create_project(c, "scoped")
        me = (await c.get("/devices/whoami", headers=bearer(ADMIN_TOKEN))).json()
        assert me["scope"] == ["all", "class:server", "device:1"]
        # grants bypass: device 1 administers a project it holds no explicit grant on
        async with await connect() as conn:
            await conn.execute("DELETE FROM device_project_grants WHERE device_id = 1")
            await conn.commit()
        did, _ = await trusted_device(c, "worker")
        r = await c.post(
            "/admin/projects/scoped/grants", json={"device": did, "role": "read"}, headers=bearer(ADMIN_TOKEN)
        )
        assert r.status_code == 200
    ctx = AuthContext(device_id=1, device_class="server", is_admin=True, token_generation=1)
    assert ctx.role_for(pid) is Role.ADMIN and ctx.has(999999, Role.ADMIN)
    # device_scope is NOT bypassed: the §2 predicate over rows of every scope
    async with await connect() as conn:
        cur = await conn.execute(
            """
            INSERT INTO events (project_id, device_id, client, request_id, kind, payload,
                                payload_sha256, occurred_at)
            VALUES (%s, 1, 'pytest/0', %s, 'write', '{"request":{},"resolved":{}}', 'sha', now())
            RETURNING event_id
            """,
            (pid, uuid.uuid4()),
        )
        (eid,) = await cur.fetchone()
        scopes = ["all", "class:server", "device:1", "class:personal", "class:work", "device:2", "class:ci"]
        for i, scope in enumerate(scopes):
            await conn.execute(
                """
                INSERT INTO memory_versions (logical_id, project_id, project_ids, device_scope, kind,
                                             title, body, token_count, valid_from, recorded_at,
                                             source_event_id)
                VALUES (%s, %s, %s, %s, 'fact', %s, 'b', 1, now(), now(), %s)
                """,
                (100 + i, pid, [pid], scope, scope, eid),
            )
        cur = await conn.execute(
            "SELECT device_scope FROM memory_versions WHERE %s = ANY(project_ids) AND device_scope = ANY(%s)"
            " ORDER BY device_scope",
            (pid, list(ctx.scope_values())),
        )
        visible = [r[0] for r in await cur.fetchall()]
        await conn.rollback()
    assert visible == ["all", "class:server", "device:1"]


async def test_registration_secret_required(db_dsn) -> None:
    body = {"name": "secretive", "fingerprint": "fp-secretive", "client": "pytest/0"}
    async with running_app(db_dsn, registration_secret="s3cret") as c:
        r = await c.post("/devices/register", json=body)
        assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
        r = await c.post("/devices/register", json=body, headers={"X-HLM-Registration-Secret": "wrong"})
        assert (r.status_code, r.json()["code"]) == (401, "E_AUTH")
        r = await c.post("/devices/register", json=body, headers={"X-HLM-Registration-Secret": "s3cret"})
        assert r.status_code == 201
        # duplicate name / fingerprint -> E_INVALID_ARG (register with a fresh identity)
        r = await c.post("/devices/register", json=body, headers={"X-HLM-Registration-Secret": "s3cret"})
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")
        r = await c.post(
            "/devices/register",
            json={**body, "name": "Bad_Name"},
            headers={"X-HLM-Registration-Secret": "s3cret"},
        )
        assert (r.status_code, r.json()["code"]) == (400, "E_INVALID_ARG")


async def test_register_rate_limit(db_dsn) -> None:
    settings = get_settings(db_dsn=db_dsn, admin_token=ADMIN_TOKEN, registration_secret=None)
    app = create_app(settings, register_rate_limit=3)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            for i in range(3):
                assert (await register(c, f"rl-{i}"))[0] > 1
            r = await c.post(
                "/devices/register", json={"name": "rl-4", "fingerprint": "fp-rl-4", "client": "pytest/0"}
            )
            assert (r.status_code, r.json()["code"]) == (429, "E_RATE_LIMITED")
            assert r.json()["retryable"] is True
