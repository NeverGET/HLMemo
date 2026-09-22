"""API embedding configuration must match the pinned model before startup."""

from __future__ import annotations

import pytest

from hlmemo.config import get_settings
from hlmemo.core.embedder import EmbedConfigMismatch
from hlmemo.server.app import create_app, readiness


@pytest.mark.parametrize("override", [{"embed_model": "other/model"}, {"embed_revision": "deadbeef"}])
def test_app_creation_rejects_unpinned_embedding(override):
    with pytest.raises(EmbedConfigMismatch, match="models.lock"):
        create_app(get_settings(**override))


async def test_lifespan_rechecks_embedding_before_database_binding(monkeypatch):
    settings = get_settings()
    app = create_app(settings)
    settings.embed_revision = "changed-after-construction"
    with pytest.raises(EmbedConfigMismatch):
        async with app.router.lifespan_context(app):
            pytest.fail("invalid configuration reached startup")


async def test_readiness_reports_embedding_configuration(monkeypatch):
    app = create_app(get_settings())
    app.state.settings.embed_revision = "drift"

    async def offline(*args, **kwargs):
        raise OSError("test database offline")

    monkeypatch.setattr("hlmemo.server.app.AsyncConnection.connect", offline)
    monkeypatch.setattr("hlmemo.server.app._verify_models_blocking", lambda *_: {"ok": True})
    ok, checks = await readiness(app)
    assert not ok and not checks["embed_config"]["ok"]
    assert "models.lock" in checks["embed_config"]["error"]
