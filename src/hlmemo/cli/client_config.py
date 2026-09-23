"""Client-side configuration for `hlm` (PHASE0-SPEC §5).

Precedence: CLI flags > `HLM_*` environment > nearest `./hlm.toml` > `~/.config/hlm/hlm.toml`
> profile > defaults. The TOML/profile/env layering is done by `hlmemo.config.Settings`; this
module only adds the `[client]` / `[preflight]` overlays and the flag layer on top.

Exit codes (sysexits.h, §5 "Failure behaviour"):
  EX_USAGE 64        bad flags / rejected CLI_ARGS override (`E_INVALID_ARG` locally)
  EX_UNAVAILABLE 69  preflight failed with on_failure=block, server unreachable
  EX_NOPERM 77       device pending / not authorized (`E_DEVICE_PENDING`, `E_AUTH`)
"""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hlmemo.config import Settings, get_settings

EX_OK = 0
EX_USAGE = 64
EX_SOFTWARE = 70
EX_UNAVAILABLE = 69
EX_NOPERM = 77
EX_NOTFOUND = 127

DEFAULT_SERVER_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_BUDGET = 3000
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_ON_FAILURE = "block"
ON_FAILURE_VALUES = ("block", "warn")
MCP_PATH = "/mcp"

_UNSET: Any = object()


class CliError(Exception):
    """Fatal CLI error carrying the exit code and the stderr line."""

    def __init__(self, message: str, exit_code: int = EX_SOFTWARE) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def config_dir() -> Path:
    override = os.environ.get("HLM_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "hlm"


def log_path() -> Path:
    return config_dir() / "hlm.log"


def base_url(server_url: str) -> str:
    """`http://host:port/mcp` -> `http://host:port` (REST base); already-bare URLs pass through."""
    url = server_url.rstrip("/")
    if url.endswith(MCP_PATH):
        url = url[: -len(MCP_PATH)]
    return url


def mcp_url(server_url: str) -> str:
    return base_url(server_url) + MCP_PATH


@dataclass(frozen=True)
class ClientConfig:
    server_url: str = DEFAULT_SERVER_URL
    project: str | None = None
    device_name: str | None = None
    budget: int = DEFAULT_BUDGET
    timeout_s: float = DEFAULT_TIMEOUT_S
    on_failure: str = DEFAULT_ON_FAILURE

    @property
    def base(self) -> str:
        return base_url(self.server_url)

    @property
    def mcp(self) -> str:
        return mcp_url(self.server_url)

    def require_project(self) -> str:
        if not self.project:
            raise CliError(
                "no project configured: pass --project or set [client].project in hlm.toml", EX_USAGE
            )
        return self.project


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else None


def resolve_client_config(
    settings: Settings | None = None,
    *,
    server_url: str | None = None,
    project: str | None = None,
    device_name: str | None = None,
    budget: int | None = None,
    timeout_s: float | None = None,
    on_failure: str | None = None,
) -> ClientConfig:
    """flags > env > [client]/[preflight] tables (via Settings) > defaults."""
    s = settings or get_settings()
    client: dict[str, Any] = dict(s.client or {})
    pre: dict[str, Any] = dict(s.preflight or {})

    def pick(flag: Any, env_name: str, table: dict[str, Any], key: str, default: Any) -> Any:
        if flag is not None:
            return flag
        e = _env(env_name)
        if e is not None:
            return e
        v = table.get(key)
        return v if v not in (None, "") else default

    # server_url: Settings already applied env > [hlm] > [client].server_url > default
    url = server_url or _env("HLM_SERVER_URL") or s.server_url or DEFAULT_SERVER_URL
    on_fail = str(pick(on_failure, "HLM_PREFLIGHT_ON_FAILURE", pre, "on_failure", DEFAULT_ON_FAILURE)).lower()
    if on_fail not in ON_FAILURE_VALUES:
        raise CliError(
            f"[preflight].on_failure must be one of {ON_FAILURE_VALUES}, got {on_fail!r}", EX_USAGE
        )
    try:
        bud = int(pick(budget, "HLM_PREFLIGHT_BUDGET", pre, "budget", DEFAULT_BUDGET))
        tmo = float(pick(timeout_s, "HLM_PREFLIGHT_TIMEOUT_S", pre, "timeout_s", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError) as exc:
        raise CliError(f"invalid [preflight] value: {exc}", EX_USAGE) from exc
    return ClientConfig(
        server_url=url,
        project=pick(project, "HLM_PROJECT", client, "project", None),
        device_name=pick(device_name, "HLM_DEVICE_NAME", client, "device_name", None),
        budget=bud,
        timeout_s=tmo,
        on_failure=on_fail,
    )


# --------------------------------------------------------------------------- fingerprint (§2, assumption 5)


def _machine_id() -> str:
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, timeout=5
            ).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        except (OSError, subprocess.SubprocessError):
            pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text().strip()
            if v:
                return v
        except OSError:
            continue
    return socket.gethostname()


INSTALL_ID_FILE = "install_id"
_INSTALL_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _read_install_id(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value if _INSTALL_ID_RE.match(value) else None


def _publish_install_id(path: Path, value: str) -> bool:
    """Create ``path`` with ``value`` atomically and never overwrite: the content is written to a
    private temp file first and published with ``link()`` (fails with EEXIST if another process
    won; readers never see a partial file). Where hard links are unsupported, ``O_CREAT|O_EXCL``
    is used. Returns False when the file already exists."""
    tmp = path.with_name(f".{INSTALL_ID_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(value + "\n")
        try:
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
        except OSError:  # no hard links on this filesystem: exclusive create instead
            try:
                fd2 = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return False
            with os.fdopen(fd2, "w") as fh:
                fh.write(value + "\n")
            return True
    finally:
        tmp.unlink(missing_ok=True)


def install_id() -> str:
    """Random per-config-dir id (``<config_dir>/install_id``, 0600), created on first use (D-055).

    Two `hlm` installations of the same OS user (different ``HLM_CONFIG_DIR``) are different
    devices and must not collide on ``devices.fingerprint``; the same config dir keeps its id, so
    re-registering from it presents the same fingerprint. Creation is race-free: concurrent first
    uses publish with no-overwrite semantics and the losers re-read the winner's id. A malformed
    file is replaced. If the directory is not writable a per-process id is used (registration
    still works — the server stores it — only the stability across runs is lost).
    """
    path = config_dir() / INSTALL_ID_FILE
    value = _read_install_id(path)
    if value is not None:
        return value
    candidate = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for _ in range(3):
            if _publish_install_id(path, candidate):
                return candidate
            value = _read_install_id(path)
            if value is not None:
                return value  # another process won the race
            path.unlink(missing_ok=True)  # malformed leftover: replace it
    except OSError:
        pass
    return candidate


def device_fingerprint() -> str:
    """``sha256(machine id : username : install id)`` — machine id = macOS IOPlatformUUID, Linux
    /etc/machine-id, else hostname; install id = ``install_id()`` of the config dir (D-055: before
    it, a second config dir of the same OS user collided on ``devices_fingerprint_key``).

    The fingerprint is only sent by ``hlm device register``; already registered devices keep
    authenticating with their stored token, so the change needs no migration."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry (containers)
        user = os.environ.get("USER", "unknown")
    return hashlib.sha256(f"{_machine_id()}:{user}:{install_id()}".encode()).hexdigest()


def default_device_name() -> str:
    host = socket.gethostname().split(".")[0].lower()
    name = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in host).strip("-") or "device"
    return name[:64]


def os_string() -> str:
    return f"{platform.system()} {platform.release()} {platform.machine()}"[:256]
