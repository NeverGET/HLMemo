"""G7 clients (VALIDATION-GATES): each pinned coding CLI registers the `hlm` MCP server and completes
a headless write -> query turn against the running compose stack (`tests/smoke/<cli>.sh`).

Needs a live stack and a trusted device: set `HLM_DEVICE_TOKEN` (write on `HLM_PROJECT`, default
`g7-smoke`). Skips with a clear reason when the token, the stack or the CLI binary is missing.
The smoke scripts are the source of truth; this test only shells out and asserts exit 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override tests/conftest.py's autouse DB truncation: this module needs no database and must
    not wipe the developer's onboarding state (devices/projects) in the compose db."""
    yield


SMOKE_DIR = REPO_ROOT / "tests" / "smoke"
SERVER_URL = os.environ.get("HLM_SERVER_URL", "http://127.0.0.1:8765/mcp")
TIMEOUT_S = int(os.environ.get("SMOKE_TIMEOUT_S", "300"))

# Vars a nested Claude Code session exports; `claude -p` must not see them (it would refuse to nest).
_NESTED_CLAUDE_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
)


def _token() -> str:
    token = os.environ.get("HLM_DEVICE_TOKEN")
    if not token:
        pytest.skip("HLM_DEVICE_TOKEN not set (trusted device with write on HLM_PROJECT)")
    return token


def _server_reachable() -> None:
    base = SERVER_URL.removesuffix("/mcp")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:  # noqa: S310 - local dev URL
            if resp.status != 200:
                pytest.skip(f"{base}/health -> {resp.status}")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"HLMemo api not reachable at {base}/health: {exc}")


@pytest.mark.parametrize("cli", ["claude", "codex", "agy"])
def test_cli_smoke(cli: str) -> None:
    if shutil.which(cli) is None:
        pytest.skip(f"{cli} binary not on PATH")
    token = _token()
    _server_reachable()
    env = {k: v for k, v in os.environ.items() if k not in _NESTED_CLAUDE_VARS}
    env.update(
        HLM_DEVICE_TOKEN=token,
        HLM_SERVER_URL=SERVER_URL,
        HLM_PROJECT=os.environ.get("HLM_PROJECT", "g7-smoke"),
    )
    t0 = time.monotonic()
    proc = subprocess.run(
        ["bash", str(SMOKE_DIR / f"{cli}.sh")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S + 60,
        check=False,
    )
    wall = time.monotonic() - t0
    print(f"\n{cli}: exit {proc.returncode} in {wall:.0f}s\n{proc.stderr[-1500:]}")
    detail = f"{proc.stdout[-1500:]}\n{proc.stderr[-3000:]}"
    assert proc.returncode == 0, f"{cli} smoke failed (exit {proc.returncode}):\n{detail}"
