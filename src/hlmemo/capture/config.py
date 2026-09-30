"""Capture configuration: project mapping, kill switch, paths. Stdlib only (the hook imports this)."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KILL_ENV = "HLM_CAPTURE"  # "off" / "0" / "false" / "no" disables capture
DRYRUN_ENV = "HLM_CAPTURE_DRYRUN"  # a directory: write the would-be payload there instead of prod
CONFIG_ENV = "HLM_CAPTURE_CONFIG"
STATE_ENV = "HLM_CAPTURE_STATE_DIR"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MIN_OWNER_MESSAGES = 3
DEFAULT_CAP_CHARS = 40_000
CLIENT_NAME = "claude-code/capture-hook"


def killed(env: dict[str, str] | None = None) -> bool:
    v = (env if env is not None else os.environ).get(KILL_ENV, "")
    return v.strip().lower() in {"off", "0", "false", "no", "disabled"}


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("HLM_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".config" / "hlm") / "capture.toml"


def state_dir() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "hlm" / "capture"


@dataclass(frozen=True)
class CaptureConfig:
    enabled: bool = True
    projects: dict[str, str] = field(default_factory=dict)  # normalized cwd prefix -> slug
    exclude: tuple[str, ...] = ()  # path fragments that are never captured (e.g. ".claude/worktrees")
    model: str = DEFAULT_MODEL
    min_owner_messages: int = DEFAULT_MIN_OWNER_MESSAGES
    cap_chars: int = DEFAULT_CAP_CHARS
    claude_bin: str = "claude"
    server_url: str | None = None  # writer overrides; default: the hlm client config (./hlm.toml)
    device_name: str | None = None
    relay_fallback: bool = True


def _norm(p: str) -> str:
    try:
        p = os.path.realpath(os.path.expanduser(p))
    except OSError:
        p = os.path.expanduser(p)
    return p.rstrip("/") or "/"


def load_config(path: Path | None = None) -> CaptureConfig:
    """Missing/invalid file -> an empty mapping (capture is a no-op). Never raises."""
    p = path or config_path()
    try:
        with p.open("rb") as fh:
            doc: dict[str, Any] = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return CaptureConfig(enabled=False)
    projects: dict[str, str] = {}
    for prefix, slug in (doc.get("projects") or {}).items():
        if isinstance(prefix, str) and isinstance(slug, str) and SLUG_RE.match(slug):
            projects[_norm(prefix)] = slug
    cap = doc.get("capture") or {}
    wr = doc.get("writer") or {}

    def _int(v: Any, default: int) -> int:
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default

    return CaptureConfig(
        enabled=bool(cap.get("enabled", True)),
        projects=projects,
        exclude=tuple(str(x) for x in (cap.get("exclude") or ())),
        model=str(cap.get("model") or DEFAULT_MODEL),
        min_owner_messages=_int(cap.get("min_owner_messages"), DEFAULT_MIN_OWNER_MESSAGES),
        cap_chars=_int(cap.get("cap_chars"), DEFAULT_CAP_CHARS),
        claude_bin=str(cap.get("claude_bin") or "claude"),
        server_url=wr.get("server_url") if isinstance(wr.get("server_url"), str) else None,
        device_name=wr.get("device_name") if isinstance(wr.get("device_name"), str) else None,
        relay_fallback=bool(wr.get("relay_fallback", True)),
    )


def map_cwd(cfg: CaptureConfig, cwd: str) -> str | None:
    """The slug for ``cwd`` (longest matching path-component prefix), or None (unmapped / excluded)."""
    if not cfg.enabled or not cwd:
        return None
    c = _norm(cwd)
    if any(frag and frag in c for frag in cfg.exclude):
        return None
    best: tuple[int, str] | None = None
    for prefix, slug in cfg.projects.items():
        if c == prefix or c.startswith(prefix.rstrip("/") + "/"):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), slug)
    return best[1] if best else None


def log_line(line: str) -> None:
    """One line per run, no content. Never raises."""
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "capture.log").open("a", encoding="utf-8") as fh:
            fh.write(line.replace("\n", " ")[:500] + "\n")
    except OSError:
        pass
