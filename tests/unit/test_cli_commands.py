"""`hlm query` / `hlm close` output + exit codes, REST client envelope mapping, device/project commands."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from test_cli_support import (  # noqa: F401
    FakeApi,
    _clean_tables,
    _isolated_home,
    compact,
    error_result,
    fake_server,
    query_ok,
    runner,
    write_toml,
)

from hlmemo.cli import hlm as hlm_mod
from hlmemo.cli.client_config import EX_NOPERM, EX_UNAVAILABLE, EX_USAGE
from hlmemo.cli.hlm import app, exit_code_for
from hlmemo.cli.http_client import HlmHttp, HlmHttpError
from hlmemo.cli.mcp_client import MemoryClient

TOK = "hlm_" + "b" * 43


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HLM_DEVICE_TOKEN", TOK)
    return write_toml(Path.cwd() / "hlm.toml", budget=1000)


def _install(monkeypatch: pytest.MonkeyPatch, query=None, close=None):  # noqa: ANN001
    srv, calls = fake_server(query, close)
    monkeypatch.setattr(hlm_mod.Ctx, "memory", lambda self: MemoryClient.in_memory(srv))
    return calls


def test_query_prints_compact_sorted_json(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, query=lambda a: query_ok(a["project"], a["token_budget"]))
    res = runner().invoke(app, ["query", "pool bug", "--kind", "lesson"])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == compact(query_ok("hlmemo", 1000))
    assert "\n" not in res.output.strip() and '": ' not in res.output
    assert calls[0]["args"] == {
        "project": "hlmemo",
        "query": "pool bug",
        "token_budget": 1000,
        "kinds": ["lesson"],
    }
    res = runner().invoke(app, ["--project", "other", "query", "x", "--budget", "300"])
    assert (
        res.exit_code == 0
        and calls[1]["args"]["project"] == "other"
        and calls[1]["args"]["token_budget"] == 300
    )


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [
        ("E_DEVICE_PENDING", EX_NOPERM),
        ("E_AUTH", EX_NOPERM),
        ("E_UNAVAILABLE", EX_UNAVAILABLE),
        ("E_INVALID_ARG", EX_USAGE),
        ("E_VERSION_CONFLICT", 1),
    ],
)
def test_query_error_exit_codes(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, code: str, exit_code: int
) -> None:
    _install(monkeypatch, query=lambda _a: error_result(code, "msg", x=1))
    res = runner().invoke(app, ["query", "q"])
    assert res.exit_code == exit_code == exit_code_for(code)
    assert f"error {code}: msg" in res.output


def test_query_without_project_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    write_toml(Path.cwd() / "hlm.toml", project=None)
    monkeypatch.setenv("HLM_DEVICE_TOKEN", TOK)
    res = runner().invoke(app, ["query", "q"])
    assert res.exit_code == EX_USAGE and "no project configured" in res.output


def test_close_builds_call_the_day(cfg: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install(monkeypatch, close=lambda a: {"ack": True, "session_note_clue": "v9"})
    card = tmp_path / "card.md"
    card.write_text("# card")
    res = runner().invoke(
        app,
        [
            "close",
            "--notes",
            "did things",
            "--decision",
            "D1",
            "--decision",
            "D2",
            "--lesson",
            "pool::discard INTRANS",
            "--card",
            str(card),
            "--card-version",
            "42",
            "--session-id",
            "11111111-1111-1111-1111-111111111111",
        ],
    )
    assert res.exit_code == 0, res.output
    assert res.output.strip() == '{"ack":true,"session_note_clue":"v9"}'
    a = calls[0]["args"]
    assert a["project"] == "hlmemo" and a["notes"] == "did things" and a["decisions"] == ["D1", "D2"]
    assert a["lessons"] == [{"title": "pool", "body": "discard INTRANS"}]
    assert a["card_update"] == {"body": "# card", "expected_version_id": "42"}
    assert a["session_id"] == "11111111-1111-1111-1111-111111111111" and len(a["request_id"]) == 36
    res = runner().invoke(app, ["close", "--notes", "x", "--lesson", "no-separator"])
    assert res.exit_code == EX_USAGE
    res = runner().invoke(app, ["close"])
    assert res.exit_code == EX_USAGE


def test_http_client_maps_envelopes_and_transport_errors() -> None:
    api = FakeApi()
    api.json(
        "GET",
        "/health",
        {"code": "E_DEVICE_PENDING", "message": "pending", "retryable": True, "details": {}},
        403,
    )
    with HlmHttp("http://s:1/mcp", TOK, transport=api.transport) as http, pytest.raises(HlmHttpError) as ei:
        http.health()
    assert ei.value.code == "E_DEVICE_PENDING" and ei.value.status == 403 and ei.value.retryable
    assert api.requests[0].headers["Authorization"] == f"Bearer {TOK}"
    assert str(api.requests[0].url) == "http://s:1/health"

    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with (
        HlmHttp("http://s:1", None, transport=httpx.MockTransport(boom)) as http,
        pytest.raises(HlmHttpError) as ei,
    ):
        http.health()
    assert ei.value.code == "E_UNAVAILABLE" and ei.value.retryable


def test_device_register_stores_token_and_approve_uses_admin(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HLM_DEVICE_TOKEN")
    monkeypatch.setenv("HLM_ADMIN_TOKEN", "hlm_admin")
    from hlmemo.cli import credentials

    monkeypatch.setattr(credentials, "_keyring", lambda: None)  # force the file fallback
    api = FakeApi()

    def register(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        assert body["name"] == "testbox" and body["class"] == "personal" and len(body["fingerprint"]) == 64
        return httpx.Response(
            201,
            json={
                "device": {"id": 7, "name": "testbox", "status": "pending", "class": "personal"},
                "token": TOK,
            },
        )

    api.on("POST", "/devices/register", register)
    api.json(
        "GET",
        "/devices/list",
        {
            "devices": [
                {"id": 1, "name": "admin", "reserved": True, "class": "server", "status": "trusted"},
                {"id": 7, "name": "testbox", "class": "personal", "status": "pending"},
            ]
        },
    )
    api.json(
        "POST",
        "/admin/devices/7/approve",
        {
            "device": {"id": 7, "name": "testbox", "status": "trusted", "class": "personal"},
            "grants": [{"project": "hlmemo", "role": "write"}],
        },
    )
    api.json("POST", "/admin/projects", {"project": {"id": 3, "slug": "hlmemo", "name": "HLMemo"}}, 201)
    orig = HlmHttp.__init__
    monkeypatch.setattr(
        HlmHttp, "__init__", lambda self, *a, **kw: orig(self, *a, transport=api.transport, **kw)
    )

    res = runner().invoke(app, ["device", "register"])
    assert res.exit_code == 0, res.output
    assert "id=7" in res.output and credentials.load_token("http://127.0.0.1:8765/mcp", "testbox") == TOK
    assert "Authorization" not in api.requests[0].headers

    res = runner().invoke(
        app, ["--admin", "device", "approve", "testbox", "--class", "personal", "--grant", "hlmemo:write"]
    )
    assert res.exit_code == 0, res.output
    approve_req = api.requests[-1]
    assert approve_req.headers["Authorization"] == "Bearer hlm_admin"
    assert json.loads(approve_req.content) == {
        "class": "personal",
        "grants": [{"project": "hlmemo", "role": "write"}],
    }

    res = runner().invoke(app, ["--admin", "--json", "project", "create", "hlmemo", "--name", "HLMemo"])
    assert res.exit_code == 0 and json.loads(res.output)["project"]["slug"] == "hlmemo"
    assert json.loads(api.requests[-1].content) == {"slug": "hlmemo", "name": "HLMemo"}

    res = runner().invoke(app, ["device", "list"])
    assert res.exit_code == 0 and "admin (reserved)" in res.output and TOK[:8] not in res.output

    res = runner().invoke(app, ["--admin", "device", "approve", "ghost", "--class", "ci"])
    assert res.exit_code == EX_USAGE and "E_NOT_FOUND" in res.output


def test_init_writes_toml_and_instruction_files() -> None:
    (Path.cwd() / "CLAUDE.md").write_text("# existing\n")
    res = runner().invoke(app, ["init", "--project", "hlmemo", "--device-name", "mbp", "--instructions"])
    assert res.exit_code == 0, res.output
    toml = (Path.cwd() / "hlm.toml").read_text()
    assert 'project     = "hlmemo"' in toml and 'device_name = "mbp"' in toml and "on_failure" in toml
    assert (Path.cwd() / "CLAUDE.md").read_text().startswith("# existing\n") and "hlmemo:instructions" in (
        Path.cwd() / "CLAUDE.md"
    ).read_text()
    assert (Path.cwd() / "AGENTS.md").is_file() and (Path.cwd() / "GEMINI.md").is_file()
    res = runner().invoke(app, ["init", "--instructions"])
    assert res.exit_code == EX_USAGE  # exists, no --force
    assert (Path.cwd() / "CLAUDE.md").read_text().count("hlmemo:instructions") == 1
