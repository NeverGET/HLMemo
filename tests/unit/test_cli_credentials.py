"""Device token storage: keyring first, file fallback with 0600, env override."""

from __future__ import annotations

import os
import stat

import pytest
from test_cli_support import _clean_tables, _isolated_home  # noqa: F401

from hlmemo.cli import credentials

URL = "http://127.0.0.1:8765/mcp"


class _NoKeyring:
    def get_password(self, *_a):  # noqa: ANN001
        from keyring.errors import NoKeyringError

        raise NoKeyringError("no backend")

    set_password = get_password
    delete_password = get_password


class _MemKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, key):  # noqa: ANN001
        return self.store.get((service, key))

    def set_password(self, service, key, value):  # noqa: ANN001
        self.store[(service, key)] = value

    def delete_password(self, service, key):  # noqa: ANN001
        del self.store[(service, key)]


def test_file_fallback_has_0600_and_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_keyring", lambda: _NoKeyring())
    where = credentials.store_token(URL, "mbp", "hlm_abc")
    p = credentials.credentials_path()
    assert where == str(p) and p.is_file()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert credentials.load_token(URL, "mbp") == "hlm_abc"
    assert credentials.load_token(URL, "other") is None
    text = p.read_text()
    assert '"mbp@http://127.0.0.1:8765" = "hlm_abc"' in text
    credentials.store_token("http://other:1/mcp", "ci", "hlm_xyz")  # second entry keeps the first
    assert credentials.load_token(URL, "mbp") == "hlm_abc"
    assert credentials.load_token("http://other:1", "ci") == "hlm_xyz"
    assert credentials.delete_token(URL, "mbp") is True
    assert credentials.load_token(URL, "mbp") is None


def test_keyring_preferred_and_file_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    kr = _MemKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: kr)
    assert credentials.store_token(URL, "mbp", "hlm_k") == "keyring"
    assert kr.store[(credentials.SERVICE, "mbp@http://127.0.0.1:8765")] == "hlm_k"
    assert not credentials.credentials_path().exists()
    assert credentials.load_token(URL, "mbp") == "hlm_k"


def test_env_token_wins_over_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_keyring", lambda: _NoKeyring())
    credentials.store_token(URL, "mbp", "hlm_file")
    monkeypatch.setenv(credentials.TOKEN_ENV_VAR, "hlm_env")
    assert credentials.load_token(URL, "mbp") == "hlm_env"
    assert credentials.load_token(URL, None) == "hlm_env"
    assert os.environ[credentials.TOKEN_ENV_VAR] == "hlm_env"
