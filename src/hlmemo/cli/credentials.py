"""Device-token storage (PHASE0-SPEC §5).

OS keychain via `keyring`, else ~/.config/hlm/credentials.toml (mode 0600).

Lookup order: `HLM_DEVICE_TOKEN` env (CI / smoke) > keychain > credentials file.
The token is keyed by `<device_name>@<server base url>` so several servers/devices can coexist.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from pathlib import Path

from hlmemo.cli.client_config import base_url, config_dir

log = logging.getLogger("hlm.credentials")

SERVICE = "hlmemo"
TOKEN_ENV_VAR = "HLM_DEVICE_TOKEN"
CREDENTIALS_FILE = "credentials.toml"


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE


def credential_key(server_url: str, device_name: str) -> str:
    return f"{device_name}@{base_url(server_url)}"


# --------------------------------------------------------------------------- keyring


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError, NoKeyringError  # noqa: F401
    except ImportError:  # pragma: no cover - keyring is a hard dependency
        return None
    return keyring


def _keyring_get(key: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, key)
    except Exception as exc:  # noqa: BLE001 - NoKeyringError, locked keychain, dbus missing, ...
        log.debug("keyring get failed (%s); falling back to file", exc)
        return None


def _keyring_set(key: str, token: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, key, token)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("keyring set failed (%s); falling back to file", exc)
        return False


def _keyring_delete(key: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE, key)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- file fallback


def _read_file() -> dict[str, str]:
    p = credentials_path()
    if not p.is_file():
        return {}
    try:
        with p.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    tokens = doc.get("tokens", {})
    return {str(k): str(v) for k, v in tokens.items() if isinstance(v, str)}


def _write_file(tokens: dict[str, str]) -> Path:
    p = credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    lines = ["# hlm device tokens — keep private (mode 0600). Managed by `hlm device register`.", "[tokens]"]
    for k in sorted(tokens):
        # JSON string literals are valid TOML basic strings
        lines.append(f"{json.dumps(k)} = {json.dumps(tokens[k])}")
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return p


# --------------------------------------------------------------------------- public API


def load_token(server_url: str, device_name: str | None) -> str | None:
    env = os.environ.get(TOKEN_ENV_VAR)
    if env:
        return env
    if not device_name:
        return None
    key = credential_key(server_url, device_name)
    tok = _keyring_get(key)
    if tok:
        return tok
    return _read_file().get(key)


def store_token(server_url: str, device_name: str, token: str) -> str:
    """Store the token; returns "keyring" or the credentials file path that holds it."""
    key = credential_key(server_url, device_name)
    if _keyring_set(key, token):
        # make sure a stale file copy does not shadow a rotated keychain entry
        tokens = _read_file()
        if key in tokens:
            del tokens[key]
            _write_file(tokens)
        return "keyring"
    tokens = _read_file()
    tokens[key] = token
    return str(_write_file(tokens))


def delete_token(server_url: str, device_name: str) -> bool:
    key = credential_key(server_url, device_name)
    removed = _keyring_delete(key)
    tokens = _read_file()
    if key in tokens:
        del tokens[key]
        _write_file(tokens)
        removed = True
    return removed


def redact(token: str | None) -> str:
    if not token:
        return "(none)"
    return token[:8] + "…" if len(token) > 12 else "…"
