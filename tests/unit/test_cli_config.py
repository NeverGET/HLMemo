"""hlm client config: precedence flags > env > ./hlm.toml > ~/.config/hlm/hlm.toml > defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_cli_support import _clean_tables, _isolated_home, home, write_toml  # noqa: F401

from hlmemo.cli.client_config import (
    DEFAULT_BUDGET,
    EX_USAGE,
    CliError,
    base_url,
    mcp_url,
    resolve_client_config,
)


def test_defaults_without_any_file() -> None:
    cfg = resolve_client_config()
    assert cfg.server_url == "http://127.0.0.1:8765/mcp"
    assert cfg.project is None
    assert cfg.budget == DEFAULT_BUDGET
    assert cfg.on_failure == "block"
    with pytest.raises(CliError) as ei:
        cfg.require_project()
    assert ei.value.exit_code == EX_USAGE


def test_user_config_then_cwd_toml_then_env_then_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    write_toml(
        home() / ".config" / "hlm" / "hlm.toml",
        server="http://user:1/mcp",
        project="userproj",
        budget=500,
    )
    cfg = resolve_client_config()
    assert (cfg.server_url, cfg.project, cfg.budget) == ("http://user:1/mcp", "userproj", 500)

    write_toml(
        Path.cwd() / "hlm.toml", server="http://cwd:2/mcp", project="cwdproj", budget=700, on_failure="warn"
    )
    cfg = resolve_client_config()
    assert (cfg.server_url, cfg.project, cfg.budget, cfg.on_failure) == (
        "http://cwd:2/mcp",
        "cwdproj",
        700,
        "warn",
    )

    monkeypatch.setenv("HLM_SERVER_URL", "http://env:3/mcp")
    monkeypatch.setenv("HLM_PROJECT", "envproj")
    monkeypatch.setenv("HLM_PREFLIGHT_BUDGET", "900")
    cfg = resolve_client_config()
    assert (cfg.server_url, cfg.project, cfg.budget) == ("http://env:3/mcp", "envproj", 900)

    cfg = resolve_client_config(
        server_url="http://flag:4/mcp", project="flagproj", budget=1100, on_failure="block"
    )
    assert (cfg.server_url, cfg.project, cfg.budget, cfg.on_failure) == (
        "http://flag:4/mcp",
        "flagproj",
        1100,
        "block",
    )


def test_nearest_toml_found_walking_up(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path.cwd()
    write_toml(root / "hlm.toml", project="parentproj")
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert resolve_client_config().project == "parentproj"


def test_invalid_on_failure_is_usage_error() -> None:
    write_toml(Path.cwd() / "hlm.toml", on_failure="explode")
    with pytest.raises(CliError) as ei:
        resolve_client_config()
    assert ei.value.exit_code == EX_USAGE


def test_base_and_mcp_url() -> None:
    assert base_url("http://h:1/mcp") == "http://h:1"
    assert base_url("http://h:1/mcp/") == "http://h:1"
    assert base_url("http://h:1") == "http://h:1"
    assert mcp_url("http://h:1") == "http://h:1/mcp"
    assert mcp_url("http://h:1/mcp") == "http://h:1/mcp"
