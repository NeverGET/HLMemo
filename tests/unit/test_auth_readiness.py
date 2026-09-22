"""Missing assets keep the API alive; readiness recovers one shared model session."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.server import app as server


@pytest.fixture(autouse=True)
def _clean_tables():
    """Lifecycle probes use fake pools and never modify a database."""


@pytest.fixture
def missing_model_app(monkeypatch, tmp_path):
    async def noop(*args):
        return 1

    async def fetchall():
        return [(server.phase0_head(),)]

    async def execute(_sql):
        return SimpleNamespace(fetchall=fetchall)

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(execute=execute)

    async def connect(*args, **kwargs):
        return connection()

    monkeypatch.setattr(server, "bind_admin_device", noop)
    monkeypatch.setattr(server, "create_pool", lambda *_: SimpleNamespace(open=noop, close=noop))
    monkeypatch.setattr(server, "AsyncConnection", SimpleNamespace(connect=connect))
    monkeypatch.setattr(server, "default_model_dir", lambda: tmp_path)
    return server.create_app(get_settings(trusted_proxy_ips="")), tmp_path


async def test_missing_models_lifespan_serves_not_ready_with_reason(missing_model_app, monkeypatch):
    app, model_dir = missing_model_app
    constructor = Mock(side_effect=AssertionError("must not load absent model files"))
    monkeypatch.setattr(server, "Embedder", constructor)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        check = body["checks"]["models"]
        assert check["dir"] == str(model_dir)
        assert check["missing"] == list(server.MODEL_FILES)
        assert "model files missing" in check["error"] and "make models" in check["error"]
        assert app.state.embedder is None and app.state.read_deps is None
        constructor.assert_not_called()


async def test_readiness_recovers_one_shared_embedder_after_files_appear(missing_model_app, monkeypatch):
    app, model_dir = missing_model_app
    entered = threading.Event()
    release = threading.Event()
    instance = Mock()
    meter = Mock()

    def construct(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return instance

    constructor = Mock(side_effect=construct)
    monkeypatch.setattr(server, "Embedder", constructor)
    monkeypatch.setattr(server, "Meter", lambda: meter)
    async with app.router.lifespan_context(app):
        assert not (await server.readiness(app))[0]
        constructor.assert_not_called()
        for rel in server.MODEL_FILES:
            path = model_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"restored asset")
        lock = model_dir / "models.lock"
        lock.write_text(
            "".join(f"{rel}: sha256:{digest}\n" for rel, digest in server.model_hashes(model_dir).items())
        )
        project_file = server._project_file
        monkeypatch.setattr(
            server, "_project_file", lambda name: lock if name == "models.lock" else project_file(name)
        )
        app.state.readiness_probe.expires_at = 0
        caller = asyncio.create_task(server.readiness(app))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            followers = [asyncio.create_task(server.readiness(app)) for _ in range(100)]
            await asyncio.sleep(0)
            assert app.state.model_check_lock.locked()
            assert constructor.call_count == 1
        finally:
            release.set()
        assert all(ok for ok, _ in await asyncio.gather(*followers))
        assert app.state.embedder is app.state.read_deps.embedder is instance
        assert app.state.read_deps.meter is meter
        app.state.readiness_probe.expires_at = 0
        assert (await server.readiness(app))[0]
        constructor.assert_called_once_with(model_dir, threads=app.state.settings.embed_intra_op_num_threads)
        instance.embed_query.assert_called_once_with("readiness")
        meter.count_text.assert_called_once_with("readiness")
    assert app.state.embedder is None and app.state.read_deps is None
