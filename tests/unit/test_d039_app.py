"""D-039 readiness single-flight, cancellation isolation and fail-closed proxy config."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.server import app as server


@pytest.fixture(autouse=True)
def _clean_tables():
    """These probes use fake connections and must not truncate a shared test database."""


def fake_dependencies(monkeypatch, *, connect=None):
    async def fetchall():
        return [(server.migration_head(),)]

    async def execute(_sql):
        return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(execute=execute)

    async def default_connect(*args, **kwargs):
        return connection()

    monkeypatch.setattr(server, "AsyncConnection", SimpleNamespace(connect=connect or default_connect))
    monkeypatch.setattr(server, "_verify_models_blocking", lambda *_: {"ok": True})


@pytest.mark.parametrize(
    ("config", "valid"),
    [
        ("", True),
        ("127.0.0.1/32, ::1/128, 172.18.0.0/16", True),
        ("172.18.0.0/16,garbage", False),
        ("172.18.0.0/99", False),
        ("172.18.0.0/16,", False),
        ("127.0.0.1", False),
        ("*", False),
    ],
)
async def test_ready_reports_invalid_proxy_cidr_configuration(monkeypatch, config, valid):
    fake_dependencies(monkeypatch)
    app = server.create_app(get_settings(trusted_proxy_ips=config))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == (200 if valid else 503)
    check = response.json()["checks"]["trusted_proxy_ips"]
    assert check["ok"] is valid
    if not valid:
        assert "HLM_TRUSTED_PROXY_IPS" in check["error"] and "CIDR list" in check["error"]


async def test_readiness_caches_failures_and_retries_after_expiry(monkeypatch):
    calls = 0

    async def offline(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise OSError("database offline")

    fake_dependencies(monkeypatch, connect=offline)
    app = server.create_app(get_settings(trusted_proxy_ips=""))
    results = await asyncio.gather(*(server.readiness(app) for _ in range(500)))
    assert calls == 1 and all(not ok for ok, _ in results)
    assert "database offline" in results[0][1]["db"]["error"]
    assert not (await server.readiness(app))[0] and calls == 1
    app.state.readiness_probe.expires_at = 0
    assert not (await server.readiness(app))[0] and calls == 2


async def test_readiness_timeout_closes_connection_caches_failure_and_retries(monkeypatch):
    calls = active = closed = 0
    blocked = asyncio.Event()

    async def execute(_sql):
        await blocked.wait()
        pytest.fail("readiness query should time out")

    @asynccontextmanager
    async def connection():
        nonlocal active, closed
        active += 1
        try:
            yield SimpleNamespace(execute=execute)
        finally:
            active -= 1
            closed += 1

    async def connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return connection()

    fake_dependencies(monkeypatch, connect=connect)
    app = server.create_app(get_settings(trusted_proxy_ips="", readiness_timeout_s=0.02))
    results = await asyncio.gather(*(server.readiness(app) for _ in range(500)))
    assert calls == closed == 1 and active == 0
    assert all(not ok and "TimeoutError" in checks["db"]["error"] for ok, checks in results)
    assert not app.state.readiness_probe.db_slot.locked()
    assert not (await server.readiness(app))[0] and calls == 1
    app.state.readiness_probe.expires_at = 0
    assert not (await server.readiness(app))[0]
    assert calls == closed == 2 and active == 0
    assert not app.state.readiness_probe.db_slot.locked()


async def test_cancelled_readiness_callers_cannot_release_connection_slot(monkeypatch):
    opened = asyncio.Event()
    release = asyncio.Event()
    closing = asyncio.Event()
    allow_close = asyncio.Event()
    calls = active = peak = 0

    async def fetchall():
        return [(server.migration_head(),)]

    async def execute(_sql):
        await release.wait()
        return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def connection():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        opened.set()
        try:
            yield SimpleNamespace(execute=execute)
        finally:
            closing.set()
            await allow_close.wait()
            active -= 1

    async def connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return connection()

    fake_dependencies(monkeypatch, connect=connect)
    app = server.create_app(get_settings(trusted_proxy_ips=""))
    caller = asyncio.create_task(server.readiness(app))
    await opened.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    followers = [asyncio.create_task(server.readiness(app)) for _ in range(500)]
    release.set()
    await closing.wait()
    await asyncio.sleep(0)
    assert calls == active == peak == 1
    assert app.state.readiness_probe.db_slot.locked()
    assert not any(task.done() for task in followers)
    allow_close.set()
    results = await asyncio.gather(*followers)
    assert all(ok for ok, _ in results) and active == 0 and peak == 1 and calls == 1
    assert not app.state.readiness_probe.db_slot.locked()
