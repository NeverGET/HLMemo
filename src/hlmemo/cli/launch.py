"""Launch the coding CLI with the preflight prompt (PHASE0-SPEC §5, pinned in tests/smoke/VERSIONS).

    claude "$P"                                        # headless: claude -p "$P"
    codex -C "$ROOT" "$P"                              # headless: codex exec -C "$ROOT" "$P"
    agy --add-dir "$ROOT" --prompt-interactive "$P"    # headless: agy --add-dir "$ROOT" --print "$P"

Prompt/resume overrides in the pass-through CLI_ARGS are rejected (`E_INVALID_ARG` locally, exit 64).
The device token is exported to the child as `HLM_DEVICE_TOKEN` for the launch only (codex names it).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from hlmemo.cli.client_config import EX_NOTFOUND, EX_USAGE, CliError

CLIS = ("claude", "codex", "agy")
TOKEN_ENV_VAR = "HLM_DEVICE_TOKEN"

# flags/subcommands that would replace or resume the prompt hlm injects (§5: rejected)
_REJECTED: dict[str, frozenset[str]] = {
    "claude": frozenset({"-p", "--print", "-c", "--continue", "-r", "--resume"}),
    "codex": frozenset({"exec", "e", "resume", "--resume"}),
    "agy": frozenset({"-p", "--print", "--prompt", "-i", "--prompt-interactive", "--resume", "-r"}),
}


class LaunchArgError(CliError):
    def __init__(self, message: str) -> None:
        super().__init__(message, EX_USAGE)


def check_cli_args(cli: str, cli_args: list[str]) -> None:
    bad = _REJECTED.get(cli, frozenset())
    for a in cli_args:
        key = a.split("=", 1)[0]
        if key in bad:
            raise LaunchArgError(
                f"E_INVALID_ARG: {a!r} would override the hlm preflight prompt/session for {cli}; "
                "use --task / --headless instead"
            )


def build_argv(
    cli: str, *, root: Path, prompt: str | None, headless: bool = False, cli_args: list[str] | None = None
) -> list[str]:
    if cli not in CLIS:
        raise LaunchArgError(f"unknown CLI {cli!r}; expected one of {', '.join(CLIS)}")
    extra = list(cli_args or [])
    check_cli_args(cli, extra)
    root_s = str(root)
    if cli == "claude":
        argv = ["claude", *extra]
        if prompt is not None:
            argv += ["-p", prompt] if headless else [prompt]
        return argv
    if cli == "codex":
        argv = ["codex", "exec", "-C", root_s, *extra] if headless else ["codex", "-C", root_s, *extra]
        if prompt is not None:
            argv.append(prompt)
        return argv
    argv = ["agy", "--add-dir", root_s, *extra]
    if prompt is not None:
        argv += ["--print", prompt] if headless else ["--prompt-interactive", prompt]
    return argv


def child_env(token: str | None, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if token:
        env[TOKEN_ENV_VAR] = token
    return env


def exec_cli(argv: list[str], *, root: Path, token: str | None, env: dict[str, str] | None = None) -> None:
    """Replace the current process (never returns on success)."""
    exe = shutil.which(argv[0])
    if exe is None:
        raise CliError(f"{argv[0]} not found on PATH", EX_NOTFOUND)
    os.chdir(root)
    os.execvpe(exe, [argv[0], *argv[1:]], child_env(token, env))
