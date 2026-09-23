"""Startup, readiness and MCP queries must share one API model session."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from hlmemo.config import get_settings
from hlmemo.core import read_service
from hlmemo.core.embedder import MODEL_FILES, model_hashes
from hlmemo.server import app as server
from tests.integration._mcp_fixtures import ADMIN_TOKEN, call_tool_raw, writer_on

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("threads", [2, 3])
async def test_one_embedder_across_startup_readiness_and_queries(db_dsn, monkeypatch, tmp_path, threads):
    # Exercise real readiness file checks without loading a second real model in the test.
    model_dir = tmp_path / "model"
    for rel in MODEL_FILES:
        path = model_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"constructor is replaced by the counting factory")
    lock = tmp_path / "models.lock"
    lock.write_text("".join(f"{rel}: sha256:{digest}\n" for rel, digest in model_hashes(model_dir).items()))
    project_file = server._project_file
    monkeypatch.setattr(
        server, "_project_file", lambda name: lock if name == "models.lock" else project_file(name)
    )
    monkeypatch.setattr(server, "default_model_dir", lambda: model_dir)

    constructions = []

    class CountingEmbedder:
        def __init__(self, path, **kwargs):
            self.queries = []
            self.closed = False
            constructions.append((self, path, kwargs))

        def close(self):
            self.closed = True

        def embed_query(self, text):
            self.queries.append(text)
            vector = np.zeros(384, dtype=np.float32)
            vector[0] = 1.0
            return vector

    # Count both startup/readiness construction and any accidental standalone lazy fallback.
    monkeypatch.setattr(server, "Embedder", CountingEmbedder)
    monkeypatch.setattr(read_service, "Embedder", CountingEmbedder)
    settings = get_settings(
        db_dsn=db_dsn,
        admin_token=ADMIN_TOKEN,
        registration_secret=None,
        embed_intra_op_num_threads=threads,
    )
    app = server.create_app(settings, register_rate_limit=None)
    assert constructions == []
    async with app.router.lifespan_context(app):
        assert len(constructions) == 1
        instance, path, kwargs = constructions[0]
        assert path == model_dir
        assert kwargs["threads"] == threads
        assert app.state.embedder is app.state.read_deps.embedder is instance
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ready = await client.get("/ready")
            assert ready.status_code == 200, ready.text
            assert instance.queries == ["readiness"]
            token = await writer_on(client, "shared-embedder")
            queries = [f"shared model query {n}" for n in range(5)]
            for query in queries:
                result = await call_tool_raw(
                    client,
                    token,
                    "memory.query",
                    {"project": "shared-embedder", "query": query, "token_budget": 2000},
                )
                assert not result.get("isError"), result
                assert "budget" in json.loads(result["content"][0]["text"])
            assert instance.queries == ["readiness", *queries]

            # Expiring the HTTP probe cache must not create another session. Invalidating
            # the file-check cache forces the actual inference check to run again as well.
            app.state.readiness_probe.expires_at = 0
            app.state.model_check_cache.clear()
            ready = await client.get("/ready")
            assert ready.status_code == 200, ready.text
            assert instance.queries == ["readiness", *queries, "readiness"]
            assert app.state.read_deps.embedder is instance
            assert len(constructions) == 1
    assert len(constructions) == 1
    assert instance.closed
