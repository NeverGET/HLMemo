"""Launch argv per CLI, CLI_ARGS override rejection, block/warn/no-preflight (exec mocked)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_cli_support import (  # noqa: F401
    _clean_tables,
    _isolated_home,
    error_result,
    fake_server,
    home,
    query_ok,
    runner,
    write_toml,
)

from hlmemo.cli import hlm as hlm_mod
from hlmemo.cli.client_config import EX_NOPERM, EX_UNAVAILABLE, EX_USAGE
from hlmemo.cli.hlm import app
from hlmemo.cli.launch import LaunchArgError, build_argv, child_env
from hlmemo.cli.mcp_client import MemoryClient
from hlmemo.cli.preflight import UNAVAILABLE_PROMPT

ROOT = Path("/work/proj")


def test_argv_interactive_and_headless() -> None:
    assert build_argv("claude", root=ROOT, prompt="P") == ["claude", "P"]
    assert build_argv("claude", root=ROOT, prompt="P", headless=True) == ["claude", "-p", "P"]
    assert build_argv("codex", root=ROOT, prompt="P") == ["codex", "-C", "/work/proj", "P"]
    assert build_argv("codex", root=ROOT, prompt="P", headless=True) == [
        "codex",
        "exec",
        "-C",
        "/work/proj",
        "P",
    ]
    assert build_argv("agy", root=ROOT, prompt="P") == [
        "agy",
        "--add-dir",
        "/work/proj",
        "--prompt-interactive",
        "P",
    ]
    assert build_argv("agy", root=ROOT, prompt="P", headless=True) == [
        "agy",
        "--add-dir",
        "/work/proj",
        "--print",
        "P",
    ]
    # pass-through args sit before the prompt; no prompt -> no prompt flag
    assert build_argv("claude", root=ROOT, prompt="P", cli_args=["--model", "x"]) == [
        "claude",
        "--model",
        "x",
        "P",
    ]
    assert build_argv("agy", root=ROOT, prompt=None, cli_args=["--yolo"]) == [
        "agy",
        "--add-dir",
        "/work/proj",
        "--yolo",
    ]


@pytest.mark.parametrize(
    ("cli", "bad"),
    [
        ("claude", "-p"),
        ("claude", "--resume"),
        ("claude", "--continue"),
        ("codex", "resume"),
        ("codex", "exec"),
        ("agy", "--print"),
        ("agy", "-i"),
    ],
)
def test_prompt_and_resume_overrides_rejected(cli: str, bad: str) -> None:
    with pytest.raises(LaunchArgError) as ei:
        build_argv(cli, root=ROOT, prompt="P", cli_args=[bad, "x"])
    assert ei.value.exit_code == EX_USAGE and "E_INVALID_ARG" in ei.value.message


def test_child_env_exports_device_token() -> None:
    env = child_env("hlm_tok", {"PATH": "/bin"})
    assert env == {"PATH": "/bin", "HLM_DEVICE_TOKEN": "hlm_tok"}


# --------------------------------------------------------------------------- end-to-end through the Typer app


class _Exec:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv, *, root, token, env=None):  # noqa: ANN001
        self.calls.append({"argv": argv, "root": root, "token": token})


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """hlm.toml in cwd, a stored token, exec mocked; returns a hook to install the fake MCP server."""
    write_toml(Path.cwd() / "hlm.toml", budget=512)
    monkeypatch.setenv("HLM_DEVICE_TOKEN", "hlm_test_token")
    ex = _Exec()
    monkeypatch.setattr(hlm_mod, "exec_cli", ex)

    def install(handler):  # noqa: ANN001
        srv, calls = fake_server(handler)
        monkeypatch.setattr(
            hlm_mod, "MemoryClient", lambda url, token, timeout_s: MemoryClient.in_memory(srv)
        )
        return calls

    return ex, install


def test_success_launch_injects_prompt_and_token(wired) -> None:  # noqa: ANN001
    ex, install = wired
    calls = install(lambda a: query_ok(a["project"], a["token_budget"]))
    res = runner().invoke(app, ["claude", "--task", "fix pool", "--", "--model", "opus"])
    assert res.exit_code == 0, res.output
    assert calls[0]["args"] == {"project": "hlmemo", "query": "fix pool", "token_budget": 512}
    (call,) = ex.calls
    assert call["argv"][:3] == ["claude", "--model", "opus"]
    assert call["argv"][3].startswith('<hlmemo-preflight project="hlmemo" device="testbox" queried_at="')
    assert call["argv"][3].endswith("Task: fix pool")
    assert call["token"] == "hlm_test_token" and call["root"] == Path.cwd()


def test_block_default_exit_69_and_cli_not_launched(wired) -> None:  # noqa: ANN001
    ex, install = wired
    install(lambda _a: error_result("E_UNAVAILABLE", "connection refused", retryable=True))
    res = runner().invoke(app, ["codex", "--task", "t"])
    assert res.exit_code == EX_UNAVAILABLE
    assert "HLMemo preflight failed: connection refused" in res.output
    assert ex.calls == []


def test_pending_device_exit_77_with_hint(wired) -> None:  # noqa: ANN001
    ex, install = wired
    install(lambda _a: error_result("E_DEVICE_PENDING", "device pending", retryable=True))
    res = runner().invoke(app, ["agy", "--task", "t"])
    assert res.exit_code == EX_NOPERM
    assert "HLMemo preflight failed: device pending" in res.output and "device approve" in res.output
    assert ex.calls == []


def test_warn_launches_with_unavailable_prompt(wired, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ex, install = wired
    monkeypatch.setenv("HLM_PREFLIGHT_ON_FAILURE", "warn")
    install(lambda _a: error_result("E_UNAVAILABLE", "down", retryable=True))
    res = runner().invoke(app, ["claude", "--headless"])
    assert res.exit_code == 0, res.output
    assert "HLMemo preflight failed: down" in res.output
    assert ex.calls[0]["argv"] == ["claude", "-p", UNAVAILABLE_PROMPT]


def test_no_preflight_is_explicit_logged_and_skips_query(wired) -> None:  # noqa: ANN001
    ex, install = wired
    calls = install(lambda a: query_ok())
    res = runner().invoke(app, ["claude", "--no-preflight", "--task", "just go"])
    assert res.exit_code == 0, res.output
    assert calls == [] and ex.calls[0]["argv"] == ["claude", "just go"]
    log = (home() / ".config" / "hlm" / "hlm.log").read_text()
    assert "preflight skipped explicitly (--no-preflight) cli=claude" in log
    res = runner().invoke(app, ["codex", "--no-preflight"])
    assert res.exit_code == 0 and ex.calls[1]["argv"] == ["codex", "-C", str(Path.cwd())]


def test_rejected_override_exits_64_before_any_query(wired) -> None:  # noqa: ANN001
    ex, install = wired
    calls = install(lambda a: query_ok())
    res = runner().invoke(app, ["claude", "--task", "t", "--", "--resume", "abc"])
    assert res.exit_code == EX_USAGE and "E_INVALID_ARG" in res.output
    assert calls == [] and ex.calls == []


def test_missing_token_blocks_with_77(wired, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ex, install = wired
    monkeypatch.delenv("HLM_DEVICE_TOKEN")
    install(lambda a: query_ok())
    res = runner().invoke(app, ["claude"])
    assert res.exit_code == EX_NOPERM and "no device token" in res.output and ex.calls == []
