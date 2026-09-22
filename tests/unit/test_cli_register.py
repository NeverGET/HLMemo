"""`hlm mcp add`: exact claude/codex commands, agy JSON merge idempotence."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest
from test_cli_support import _clean_tables, _isolated_home, home, runner, write_toml  # noqa: F401

from hlmemo.cli import mcp_register
from hlmemo.cli.hlm import app
from hlmemo.cli.mcp_register import agy_merge, claude_command, codex_command, register

URL = "http://127.0.0.1:8765/mcp"
TOK = "hlm_" + "a" * 43


def test_claude_command_exact() -> None:
    assert claude_command("http://127.0.0.1:8765", TOK) == [
        "claude",
        "mcp",
        "add",
        "--transport",
        "http",
        "hlm",
        URL,
        "--header",
        f"Authorization: Bearer {TOK}",
    ]
    assert claude_command(URL, TOK, scope="user")[7:9] == ["--scope", "user"]


def test_codex_command_exact() -> None:
    assert codex_command(URL) == [
        "codex",
        "mcp",
        "add",
        "hlm",
        "--url",
        URL,
        "--bearer-token-env-var",
        "HLM_DEVICE_TOKEN",
    ]
    assert TOK not in " ".join(codex_command(URL))  # codex never receives the token itself


def test_agy_merge_preserves_entries_and_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "mcp_config.json"
    p.write_text(
        json.dumps(
            {"mcpServers": {"context7": {"serverUrl": "https://c7", "headers": {"X": "1"}}}, "other": 1}
        )
    )
    assert agy_merge(p, URL, TOK) is True
    doc = json.loads(p.read_text())
    assert doc["other"] == 1 and doc["mcpServers"]["context7"] == {
        "serverUrl": "https://c7",
        "headers": {"X": "1"},
    }
    assert doc["mcpServers"]["hlm"] == {"serverUrl": URL, "headers": {"Authorization": f"Bearer {TOK}"}}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    before = p.read_bytes()
    assert agy_merge(p, URL, TOK) is False  # unchanged -> not rewritten
    assert p.read_bytes() == before
    assert agy_merge(p, URL, "hlm_rotated") is True  # rotation replaces the entry in place
    assert json.loads(p.read_text())["mcpServers"]["hlm"]["headers"]["Authorization"] == "Bearer hlm_rotated"


def test_agy_merge_creates_file_and_rejects_garbage(tmp_path: Path) -> None:
    p = tmp_path / "new" / "mcp_config.json"
    assert agy_merge(p, URL, TOK) is True
    assert json.loads(p.read_text()) == {
        "mcpServers": {"hlm": {"serverUrl": URL, "headers": {"Authorization": f"Bearer {TOK}"}}}
    }
    p.write_text("{not json")
    with pytest.raises(Exception, match="not valid JSON"):
        agy_merge(p, URL, TOK)


def test_register_runs_verified_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def run(argv):  # noqa: ANN001
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    assert "hlm" in register("claude", URL, TOK, runner=run)
    assert "HLM_DEVICE_TOKEN" in register("codex", URL, TOK, runner=run)
    assert seen == [claude_command(URL, TOK), codex_command(URL)]

    def fail(argv):  # noqa: ANN001
        return subprocess.CompletedProcess(argv, 2, "", "boom")

    with pytest.raises(Exception, match="exit 2"):
        register("claude", URL, TOK, runner=fail)


def test_mcp_add_cli_uses_stored_token(monkeypatch: pytest.MonkeyPatch) -> None:
    write_toml(Path.cwd() / "hlm.toml")
    monkeypatch.setenv("HLM_DEVICE_TOKEN", TOK)
    seen: list[list[str]] = []
    monkeypatch.setattr(
        mcp_register,
        "_default_runner",
        lambda argv: (seen.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1],
    )
    res = runner().invoke(app, ["mcp", "add", "claude"])
    assert res.exit_code == 0, res.output
    assert seen == [claude_command(URL, TOK)]
    res = runner().invoke(app, ["mcp", "add", "agy"])
    assert res.exit_code == 0, res.output
    cfg = json.loads((home() / ".gemini" / "config" / "mcp_config.json").read_text())
    assert cfg["mcpServers"]["hlm"]["serverUrl"] == URL
    res = runner().invoke(app, ["mcp", "add", "cursor"])
    assert res.exit_code == 64
