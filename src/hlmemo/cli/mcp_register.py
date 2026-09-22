"""`hlm mcp add claude|codex|agy` (PHASE0-SPEC §5, PHASE0-ASSUMPTIONS-VERIFIED #9).

claude 2.1.278:  claude mcp add --transport http hlm <URL>/mcp --header "Authorization: Bearer <token>"
codex 0.155.1:   codex mcp add hlm --url <URL>/mcp --bearer-token-env-var HLM_DEVICE_TOKEN
                 (the flag names an env var; `hlm claude|codex|agy` exports HLM_DEVICE_TOKEN for the launch)
agy 1.1.4:       no `mcp` subcommand -> merge into ~/.gemini/config/mcp_config.json:
                 {"mcpServers": {"hlm": {"serverUrl": "<URL>/mcp",
                                         "headers": {"Authorization": "Bearer <token>"}}}}
                 existing entries preserved; idempotent (unchanged file is not rewritten).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hlmemo.cli.client_config import EX_UNAVAILABLE, CliError, mcp_url
from hlmemo.cli.launch import TOKEN_ENV_VAR

SERVER_NAME = "hlm"
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def claude_command(server_url: str, token: str, *, scope: str | None = None) -> list[str]:
    argv = ["claude", "mcp", "add", "--transport", "http", SERVER_NAME, mcp_url(server_url)]
    if scope:
        argv += ["--scope", scope]
    argv += ["--header", f"Authorization: Bearer {token}"]
    return argv


def codex_command(server_url: str) -> list[str]:
    return [
        "codex",
        "mcp",
        "add",
        SERVER_NAME,
        "--url",
        mcp_url(server_url),
        "--bearer-token-env-var",
        TOKEN_ENV_VAR,
    ]


def agy_config_path() -> Path:
    override = os.environ.get("HLM_AGY_MCP_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".gemini" / "config" / "mcp_config.json"


def agy_entry(server_url: str, token: str) -> dict[str, Any]:
    return {"serverUrl": mcp_url(server_url), "headers": {"Authorization": f"Bearer {token}"}}


def agy_merge(path: Path, server_url: str, token: str) -> bool:
    """Merge the hlm entry into agy's mcp_config.json. Returns True when the file changed."""
    doc: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as exc:
            raise CliError(f"{path} is not valid JSON; fix or remove it first ({exc})") from exc
        if not isinstance(loaded, dict):
            raise CliError(f"{path} must contain a JSON object")
        doc = loaded
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        doc["mcpServers"] = servers
    entry = agy_entry(server_url, token)
    if servers.get(SERVER_NAME) == entry:
        return False
    servers[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def register(
    cli: str,
    server_url: str,
    token: str,
    *,
    runner: Runner | None = None,
    scope: str | None = None,
    agy_path: Path | None = None,
) -> str:
    """Register the HLMemo MCP server with one CLI; returns a one-line human summary."""
    run = runner or _default_runner
    if cli == "claude":
        argv = claude_command(server_url, token, scope=scope)
    elif cli == "codex":
        argv = codex_command(server_url)
    elif cli == "agy":
        path = agy_path or agy_config_path()
        changed = agy_merge(path, server_url, token)
        return f"agy: {'updated' if changed else 'already up to date'} {path} (mcpServers.{SERVER_NAME})"
    else:
        raise CliError(f"unknown CLI {cli!r}; expected claude, codex or agy", 64)
    try:
        proc = run(argv)
    except FileNotFoundError as exc:
        raise CliError(f"{cli} not found on PATH", EX_UNAVAILABLE) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise CliError(f"{cli} mcp add failed (exit {proc.returncode}): {err}", EX_UNAVAILABLE)
    note = f" (export {TOKEN_ENV_VAR} in the shell, or launch via `hlm codex`)" if cli == "codex" else ""
    return f"{cli}: registered MCP server '{SERVER_NAME}' -> {mcp_url(server_url)}{note}"
