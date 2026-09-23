"""W0a (D-061) unit checks: fail-closed settings, route filter, ops parsing, CLI login/self-revoke."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from test_cli_support import (  # noqa: F401
    FakeApi,
    _clean_tables,
    _isolated_home,
    home,
    runner,
    write_toml,
)

from hlmemo.auth.errors import HlmError
from hlmemo.cli import credentials
from hlmemo.cli.client_config import EX_NOPERM, EX_USAGE
from hlmemo.cli.hlm import app
from hlmemo.cli.http_client import HlmHttp
from hlmemo.config import Settings
from hlmemo.ops.service import parse_duration, parse_grant
from hlmemo.server.middleware import route_closed

TOK = "hlm_" + "L" * 43
SERVER = "http://127.0.0.1:8765/mcp"


# --------------------------------------------------------------------------- settings


def test_defaults_are_fail_closed() -> None:
    # _isolated_home cleared every HLM_* variable, including the fixtures' dev opt-in.
    s = Settings(_env_file=None)
    assert (s.registration_mode, s.admin_http, s.deployment) == ("closed", "disabled", "development")
    assert s.unsafe_config() == []  # development never refuses


@pytest.mark.parametrize(
    ("registration", "admin_http", "unsafe"),
    [
        ("closed", "disabled", False),
        ("open", "disabled", True),
        ("secret", "disabled", True),
        ("closed", "enabled", True),
        ("open", "enabled", True),
    ],
)
def test_production_unsafe_config(registration: str, admin_http: str, unsafe: bool) -> None:
    s = Settings(deployment="production", registration_mode=registration, admin_http=admin_http)
    assert bool(s.unsafe_config()) is unsafe


def test_modes_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HLM_REGISTRATION_MODE", " Open ")
    monkeypatch.setenv("HLM_ADMIN_HTTP", "ENABLED")
    monkeypatch.setenv("HLM_DEPLOYMENT", "production")
    s = Settings()
    assert (s.registration_mode, s.admin_http, s.deployment) == ("open", "enabled", "production")
    monkeypatch.setenv("HLM_REGISTRATION_MODE", "wide-open")
    with pytest.raises(ValueError):
        Settings()


def test_admin_token_is_ignored_while_admin_http_disabled() -> None:
    assert not Settings(admin_token="x" * 64, admin_http="disabled").admin_enabled
    assert Settings(admin_token="x" * 64, admin_http="enabled").admin_enabled


# --------------------------------------------------------------------------- route filter


PROD = Settings(registration_mode="closed", admin_http="disabled")
DEV = Settings(registration_mode="open", admin_http="enabled")


@pytest.mark.parametrize(
    "path",
    [
        "/devices/register",
        "/devices/register/",
        "//devices//register",
        "/devices/approve",
        "/devices/grant",
        "/devices/list",
        "/admin",
        "/admin/",
        "/admin/projects",
        "//admin/projects/x/grants",
        "/admin/devices/7/revoke",
    ],
)
def test_closed_paths_in_production(path: str) -> None:
    assert route_closed(path, PROD)
    assert not route_closed(path, DEV)


@pytest.mark.parametrize(
    "path", ["/health", "/ready", "/devices/whoami", "/devices/revoke", "/mcp", "/administrator"]
)
def test_public_paths_stay_open(path: str) -> None:
    assert not route_closed(path, PROD)


def test_register_closed_only_with_closed_mode() -> None:
    for mode in ("open", "secret"):
        assert not route_closed("/devices/register", Settings(registration_mode=mode, admin_http="disabled"))
        assert route_closed("/devices/list", Settings(registration_mode=mode, admin_http="disabled"))


# --------------------------------------------------------------------------- ops parsing


def test_parse_duration_and_grant() -> None:
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("2w") == timedelta(weeks=2)
    for bad in ("0m", "1.5h", "2x", "", "-1h", "1h30m"):
        with pytest.raises(HlmError):
            parse_duration(bad)
    assert parse_grant("gates-probe:write") == ("gates-probe", "write")
    for bad in ("x:write", "proj:owner", "proj", ":write"):
        with pytest.raises(HlmError):
            parse_grant(bad)


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    write_toml(Path.cwd() / "hlm.toml", server=SERVER)
    monkeypatch.setattr(credentials, "_keyring", lambda: None)  # force the 0600 file fallback
    fake = FakeApi()
    orig = HlmHttp.__init__
    monkeypatch.setattr(
        HlmHttp, "__init__", lambda self, *a, **kw: orig(self, *a, transport=fake.transport, **kw)
    )
    return fake


def _health(status: str, device_id: int = 9, name: str = "testbox") -> dict:
    return {"status": "ok", "device": {"id": device_id, "name": name, "status": status, "class": "personal"}}


def test_login_token_stdin_stores_verified_token(api: FakeApi) -> None:
    api.json("GET", "/health", _health("trusted"))
    res = runner().invoke(app, ["device", "login", "--name", "testbox", "--token-stdin"], input=f"\n{TOK}\n")
    assert res.exit_code == 0, res.output
    assert credentials.load_token(SERVER, "testbox") == TOK
    assert api.requests[0].headers["Authorization"] == f"Bearer {TOK}"
    creds = credentials.credentials_path()
    assert creds.stat().st_mode & 0o777 == 0o600
    # G-W0-8 (client side): the token is not echoed, logged or placed in argv.
    assert TOK not in res.output
    for path in home().rglob("*"):
        if path.is_file() and path != creds:
            assert TOK not in path.read_text(errors="ignore"), path


@pytest.mark.parametrize("status", ["revoked", "pending"])
def test_login_refuses_untrusted_token(api: FakeApi, status: str) -> None:
    api.json("GET", "/health", _health(status))
    res = runner().invoke(app, ["device", "login", "--token-stdin"], input=TOK + "\n")
    assert res.exit_code == EX_NOPERM and "token rejected" in res.output
    assert credentials.load_token(SERVER, "testbox") is None


def test_login_unknown_token_and_garbage(api: FakeApi) -> None:
    api.json("GET", "/health", {"status": "ok"})  # unknown bearer: no device echoed
    res = runner().invoke(app, ["device", "login", "--token-stdin"], input=TOK + "\n")
    assert res.exit_code == EX_NOPERM
    res = runner().invoke(app, ["device", "login", "--token-stdin"], input="not-a-token\n")
    assert res.exit_code == EX_USAGE and "does not look like" in res.output
    res = runner().invoke(app, ["device", "login", "--token-stdin"], input="")
    assert res.exit_code == EX_USAGE and "no token received" in res.output
    assert not api.requests[1:], "garbage never reaches the server"


def test_login_getpass_fallback(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    from hlmemo.cli import hlm as hlm_mod

    api.json("GET", "/health", _health("trusted"))
    monkeypatch.setattr(hlm_mod.getpass, "getpass", lambda prompt: TOK)
    res = runner().invoke(app, ["device", "login"])
    assert res.exit_code == 0, res.output
    assert credentials.load_token(SERVER, "testbox") == TOK and TOK not in res.output


def test_register_404_prints_operator_hint(api: FakeApi) -> None:
    res = runner().invoke(app, ["device", "register"])
    assert res.exit_code == EX_NOPERM
    assert "registration is closed" in res.output and "hlm_ops.sh device mint" in res.output
    assert "--token-stdin" in res.output


def test_revoke_self_uses_public_route_and_forgets_token(api: FakeApi) -> None:
    credentials.store_token(SERVER, "testbox", TOK)
    api.json("GET", "/health", _health("trusted", device_id=12))

    def revoke(r: httpx.Request) -> httpx.Response:
        assert json.loads(r.content) == {"id": 12} and r.headers["Authorization"] == f"Bearer {TOK}"
        return httpx.Response(200, json={"device": {"id": 12, "status": "revoked"}, "revoked_grants": 2})

    api.on("POST", "/devices/revoke", revoke)
    res = runner().invoke(app, ["device", "revoke", "--self"])
    assert res.exit_code == 0, res.output
    assert "revoked this device (id=12)" in res.output
    assert credentials.load_token(SERVER, "testbox") is None
    assert [r.url.path for r in api.requests] == ["/health", "/devices/revoke"]


def test_revoke_other_device_on_closed_admin_http_hints_ops(api: FakeApi) -> None:
    credentials.store_token(SERVER, "testbox", TOK)
    res = runner().invoke(app, ["device", "revoke", "7"])
    assert res.exit_code == EX_USAGE
    assert "hlm_ops.sh device revoke 7" in res.output
    res = runner().invoke(app, ["device", "revoke", "7", "--self"])
    assert res.exit_code == EX_USAGE


# --------------------------------------------------------------------------- ops status (Sol 36 M2)


@pytest.fixture
def loopback_ready(monkeypatch: pytest.MonkeyPatch):
    """A stand-in API loopback listener answering /ready 503 with diagnostics; no database."""
    import http.server
    import threading

    body = json.dumps(
        {
            "status": "not_ready",
            "checks": {
                "db": {"ok": False, "error": "OperationalError: connection refused"},
                "models": {"ok": True},
            },
        }
    ).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(503 if self.path == "/ready" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HLM_API_PORT", str(server.server_address[1]))
    monkeypatch.setenv("HLM_DB_DSN", "postgresql://hlm:hlm@127.0.0.1:1/unreachable")  # nothing listens
    yield
    server.shutdown()


def test_ops_status_shows_ready_diagnostics_with_the_database_down(loopback_ready, capsys) -> None:
    from hlmemo.ops.cli import EX_UNAVAILABLE, main

    assert main(["status", "--json"]) == EX_UNAVAILABLE
    out = json.loads(capsys.readouterr().out)
    assert out["ready"]["checks"]["db"]["error"] == "OperationalError: connection refused"
    assert out["db"]["ok"] is False and "jobs" not in out
    assert main(["status"]) == EX_UNAVAILABLE
    text = capsys.readouterr().out
    assert "ready       not_ready failing=db" in text
    assert "check     db: OperationalError: connection refused" in text
    assert "db          UNAVAILABLE" in text
