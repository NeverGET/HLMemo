"""Body admission and process connection settings reach the actual server entrypoint."""

import pytest

from hlmemo.config import get_settings
from hlmemo.server import app


@pytest.fixture(autouse=True)
def _clean_tables():
    """Configuration tests have no database dependency."""
    yield


@pytest.mark.parametrize("configured", [False, True])
def test_uvicorn_connection_limits_and_spool_environment(monkeypatch, tmp_path, configured):
    values = {
        "api_limit_concurrency": 123 if configured else 512,
        "api_timeout_keep_alive": 7 if configured else 5,
        "request_body_base_s": 20 if configured else 30,
        "request_body_client_concurrency": 8 if configured else 16,
    }
    for key, value in values.items():
        if configured:
            monkeypatch.setenv(f"HLM_{key.upper()}", str(value))
        else:
            monkeypatch.delenv(f"HLM_{key.upper()}", raising=False)
    if configured:
        monkeypatch.setenv("HLM_REQUEST_SPOOL_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("HLM_REQUEST_SPOOL_DIR", raising=False)
    settings = get_settings()
    for key, value in values.items():
        assert getattr(settings, key) == value
    assert settings.request_spool_dir == (tmp_path if configured else None)
    sentinel = object()
    calls = []
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    monkeypatch.setattr(app, "create_app", lambda settings: sentinel)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    app.main()
    args, kwargs = calls[0]
    assert args == (sentinel,)
    assert kwargs["limit_concurrency"] == values["api_limit_concurrency"]
    assert kwargs["timeout_keep_alive"] == values["api_timeout_keep_alive"]
    assert kwargs["proxy_headers"] is False
