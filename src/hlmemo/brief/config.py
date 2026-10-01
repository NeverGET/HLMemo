"""Brief configuration: kill switch, ``[brief]`` table, cwd -> project (the capture mapping file)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hlmemo.capture import config as CC

KILL_ENV = "HLM_BRIEF"  # "off" / "0" / "false" / "no" disables the brief
DRYRUN_ENV = "HLM_BRIEF_DRYRUN"  # a directory: write the would-be brief there and inject nothing
WALL_ENV = "HLM_BRIEF_WALL_S"  # test/ops override of the hard wall clock

WALL_S = 4.0  # hard budget for the whole hook process (the registered hook timeout is 5)
FETCH_S = 3.2  # the memory calls must finish by then; the rest is assembly + output
BRIEF_TOKENS = 1500
SOURCES = ("startup", "clear", "compact")


def killed(env: dict[str, str] | None = None) -> bool:
    v = (env if env is not None else os.environ).get(KILL_ENV, "")
    return v.strip().lower() in {"off", "0", "false", "no", "disabled"}


def wall_seconds() -> float:
    try:
        v = float(os.environ.get(WALL_ENV, ""))
    except ValueError:
        return WALL_S
    return v if 0 < v <= 60 else WALL_S


@dataclass(frozen=True)
class BriefConfig:
    capture: CC.CaptureConfig  # mapping, exclusions and [writer] overrides, from the capture file
    enabled: bool = True  # [brief] enabled
    sources: tuple[str, ...] = SOURCES
    budget_tokens: int = BRIEF_TOKENS
    decisions_max_age_days: int = 7
    decisions_max_lines: int = 0  # 0 = no decision/open history (the card is the current state)
    lesson_body_chars: int = 0  # 0 = lesson titles only
    include_auto: bool = False  # auto-captured (unreviewed) notes/lessons are NOT shown unless true


def _nat(v: Any, default: int, hi: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi else default


def load_config(path: Path | None = None) -> BriefConfig:
    """Missing/invalid file -> an empty mapping (the brief is a no-op). Never raises."""
    p = path or CC.config_path()
    try:
        # The mapping, exclusions and [writer] are the capture ones, but `[capture] enabled = false`
        # (capture's own switch) must not silence the brief: only `[brief]` and HLM_BRIEF do.
        cap = replace(CC.load_config(p), enabled=True)
        with p.open("rb") as fh:
            doc: dict[str, Any] = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return BriefConfig(capture=CC.CaptureConfig(enabled=False), enabled=False)
    b = doc.get("brief") or {}
    if not isinstance(b, dict):
        b = {}
    srcs = tuple(s for s in (b.get("sources") or SOURCES) if isinstance(s, str))
    bt = b.get("budget_tokens")
    ok = isinstance(bt, int) and not isinstance(bt, bool) and 200 <= bt <= BRIEF_TOKENS
    ad = b.get("decisions_max_age_days")
    age = ad if isinstance(ad, int) and not isinstance(ad, bool) and 1 <= ad <= 365 else 7
    return BriefConfig(
        capture=cap,
        decisions_max_age_days=age,
        decisions_max_lines=_nat(b.get("decisions_max_lines"), 0, 50),
        lesson_body_chars=_nat(b.get("lesson_body_chars"), 0, 400),
        include_auto=b.get("include_auto", False) is True,
        enabled=b.get("enabled", True) is not False,
        sources=srcs or SOURCES,
        budget_tokens=bt if ok else BRIEF_TOKENS,
    )


def map_cwd(cfg: BriefConfig, cwd: str) -> str | None:
    """Exactly capture's mapping (longest path-component prefix, same exclusions such as worktrees)."""
    if not cfg.enabled:
        return None
    return CC.map_cwd(cfg.capture, cwd)
