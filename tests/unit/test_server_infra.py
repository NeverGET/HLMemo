"""Regression probes for response durability and dependency readiness (D-027)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from tests.bootstrap_test_db import ensure_test_database

from hlmemo.server import app as server
from hlmemo.server.middleware import AuthMiddleware


@pytest.mark.parametrize("failure", [None, "commit", "interrupt"])
async def test_response_waits_for_outer_commit(failure):
    sent = []
    persisted = []
    pending = []

    async def commit():
        assert not sent
        if failure == "commit":
            raise OSError("connection lost at commit")
        persisted.extend(pending)
        pending.clear()

    async def rollback():
        pending.clear()

    conn = SimpleNamespace(commit=commit, rollback=rollback)

    @asynccontextmanager
    async def connection():
        yield conn

    app = Starlette()
    app.state.pool = SimpleNamespace(connection=connection)
    scope = {"type": "http", "method": "POST", "path": "/devices/register", "headers": [], "app": app}

    async def route(scope, receive, send):
        pending.append("write")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":', "more_body": True})
        assert not sent and not persisted
        if failure == "interrupt":
            raise asyncio.CancelledError()
        await send({"type": "http.response.body", "body": b"true}", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    middleware = AuthMiddleware(route)
    if failure == "interrupt":
        with pytest.raises(asyncio.CancelledError):
            await middleware(scope, receive, send)
        assert not sent
    else:
        await middleware(scope, receive, send)
        assert sent[0]["status"] == (503 if failure else 200)
        if failure:
            body = json.loads(b"".join(m.get("body", b"") for m in sent))
            assert body["code"] == "E_UNAVAILABLE" and body["retryable"]
    assert persisted == ([] if failure else ["write"])
    assert not pending


@pytest.mark.parametrize("dependency", ["ready", "db", "migration", "models"])
async def test_readiness_checks_dependencies_and_reuses_starlette_state(monkeypatch, dependency):
    app = Starlette()

    async def fetchall():
        return [("old" if dependency == "migration" else "current",)]

    async def execute(sql):
        if dependency == "db":
            raise OSError("offline")
        return SimpleNamespace(fetchall=fetchall)

    async def rollback():
        pass

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(execute=execute, rollback=rollback)

    app.state.pool = SimpleNamespace(connection=connection)

    async def connect(*args, **kwargs):
        return connection()

    monkeypatch.setattr(server.AsyncConnection, "connect", connect)
    app.state.model_check_cache = {"existing": True}
    caches = []

    def verify(model_dir, lock, cache, embedder, meter):
        caches.append(cache)
        return {"ok": dependency != "models"}

    monkeypatch.setattr(server, "migration_head", lambda: "current")
    monkeypatch.setattr(server, "_verify_models_blocking", verify)
    ok, checks = await server.readiness(app)
    assert ok == (dependency == "ready")
    assert caches[0] is app.state.model_check_cache
    if dependency != "ready":
        assert not checks[dependency]["ok"]


def test_readiness_requires_complete_hashes_and_working_inference_and_meter(tmp_path, monkeypatch):
    for rel in server.MODEL_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("asset")
    lock = tmp_path / "models.lock"
    lock.write_text("")
    hashes = server.model_hashes(tmp_path)
    assert not server._verify_models_blocking(tmp_path, lock, {}, Mock(), Mock())["ok"]
    lock.write_text("\n".join(f"{rel}: sha256:{digest}" for rel, digest in hashes.items()))
    embedder = Mock()
    meter = Mock()
    cache = {}
    assert server._verify_models_blocking(tmp_path, lock, cache, embedder, meter)["ok"]
    assert server._verify_models_blocking(tmp_path, lock, cache, embedder, meter)["ok"]
    embedder.embed_query.assert_called_once_with("readiness")
    meter.count_text.assert_called_once_with("readiness")
    meter.count_text.side_effect = RuntimeError("budget tokenizer unavailable offline")
    with pytest.raises(RuntimeError, match="tokenizer"):
        server._verify_models_blocking(tmp_path, lock, {}, embedder, meter)
    (tmp_path / server.HASHED_FILES[0]).write_text("corrupt asset")
    assert not server._verify_models_blocking(tmp_path, lock, cache, embedder, meter)["ok"]


def test_readiness_real_pinned_assets(model_dir, embedder):
    lock = Path(__file__).resolve().parents[2] / "models.lock"
    checks = server._verify_models_blocking(model_dir, lock, {}, embedder, server.Meter())
    assert checks["ok"] and checks["inference"] and checks["meter"]


@pytest.mark.parametrize("database", ["hlm", "hlm_retr", "postgres", ""])
def test_test_image_bootstrap_rejects_non_test_databases(monkeypatch, database):
    connect = Mock()
    monkeypatch.setattr("tests.bootstrap_test_db.psycopg.connect", connect)
    with pytest.raises(ValueError, match="hlm_verify"):
        ensure_test_database(f"host=db user=hlm dbname='{database}'")
    connect.assert_not_called()


@pytest.mark.parametrize("exists", [False, True])
def test_test_image_bootstrap_only_creates_absent_verify_database(monkeypatch, exists):
    from unittest.mock import MagicMock

    from psycopg.conninfo import conninfo_to_dict

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (1,) if exists else None
    connect = MagicMock()
    connect.return_value.__enter__.return_value = conn
    monkeypatch.setattr("tests.bootstrap_test_db.psycopg.connect", connect)
    ensure_test_database("postgresql://hlm:hlm@db:5432/hlm_verify")
    assert conninfo_to_dict(connect.call_args.args[0])["dbname"] == "postgres"
    assert connect.call_args.kwargs["autocommit"] is True
    assert conn.execute.call_count == (1 if exists else 2)
    assert conn.execute.call_args_list[0].args[1] == ("hlm_verify",)
    if not exists:
        assert conn.execute.call_args.args[0].as_string() == 'CREATE DATABASE "hlm_verify"'
