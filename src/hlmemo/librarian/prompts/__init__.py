"""Versioned librarian prompts (PHASE2-4-ROADMAP §2 Architecture).

Each task has ``<task>/v<N>.md`` (the static system prompt, which comes first in every request so
provider prompt caching applies, D-019) and ``<task>/v<N>.schema.json`` (the JSON schema the
response must satisfy). ``load_task(name)`` picks the highest version unless one is pinned: per call
(``load_task(name, version)``), or process-wide (``pin_versions({"relate": 1})`` or the
``HLM_LIBRARIAN_PROMPT_PINS="relate=1,relate_verify=1"`` environment variable — a rollback that
needs no code change; the G-LIVE-B runner's ``--prompts v1`` uses it). Model quirks are never
written here: a profile's ``prompt_overrides[<task>].system_append`` is appended at request time
(D-017).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

PROMPT_DIR = Path(__file__).resolve().parent

#: ``max_tokens`` per task (W2a: every request sets it; the reservation's worst case uses it).
MAX_TOKENS: dict[str, int] = {
    "placement": 200,
    "contradiction": 600,
    "summary": 700,
    "risk": 500,
    "risk_judge": 500,  # W2d memory.risk_check judge (api process, 4 s cap)
    "synthesis": 700,
    # W2b (batched): ≤ 8 items / ≤ 8 pairs per call, reasoning tokens included by the providers
    "place": 700,
    "relate": 1400,
    "relate_verify": 700,
}
#: per-version ``max_tokens`` where a version's answer is longer (relate/v2 adds scope, refiner and
#: the replaced statements; relate_verify/v2 adds replaces_all and adds_detail)
MAX_TOKENS_VERSION: dict[tuple[str, int], int] = {("relate", 2): 2000, ("relate_verify", 2): 900}
_PINS: dict[str, int] = {}


def pin_versions(pins: dict[str, int] | None) -> None:
    """Pin prompt versions process-wide (``None``/``{}`` = back to the latest)."""
    _PINS.clear()
    _PINS.update({k: int(v) for k, v in (pins or {}).items()})


def parse_pins(text: str | None) -> dict[str, int]:
    """``"relate=1, relate_verify=v1"`` -> ``{"relate": 1, "relate_verify": 1}``."""
    out: dict[str, int] = {}
    for part in (text or "").split(","):
        name, sep, ver = part.strip().partition("=")
        ver = ver.strip().lstrip("v")
        if sep and name.strip() and ver.isdigit():
            out[name.strip()] = int(ver)
    return out


pin_versions(parse_pins(os.environ.get("HLM_LIBRARIAN_PROMPT_PINS")))

_VERSION = re.compile(r"^v(\d+)\.md$")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    name: str
    prompt_version: str  # "v1"
    schema_version: str  # "v1"
    system: str
    schema: dict[str, Any]
    max_tokens: int

    def system_for(self, overrides: dict[str, Any] | None) -> str:
        extra = ((overrides or {}).get(self.name) or {}).get("system_append")
        return self.system if not extra else f"{self.system.rstrip()}\n{extra}\n"

    def schema_errors(self, obj: Any) -> str | None:
        errors = sorted(jsonschema.Draft202012Validator(self.schema).iter_errors(obj), key=lambda e: e.path)
        if not errors:
            return None
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "<root>"
        return f"{where}: {e.message}"[:300]


def versions(name: str) -> list[int]:
    d = PROMPT_DIR / name
    if not d.is_dir():
        return []
    return sorted(int(m.group(1)) for p in d.iterdir() if (m := _VERSION.match(p.name)))


def load_task(name: str, version: int | None = None) -> TaskSpec:
    if name not in MAX_TOKENS:
        raise KeyError(f"unknown librarian task {name!r}")
    available = versions(name)
    if not available:
        raise FileNotFoundError(f"no prompt for task {name!r} under {PROMPT_DIR}")
    v = version if version is not None else _PINS.get(name, available[-1])
    return _load(name, v)


@lru_cache(maxsize=64)
def _load(name: str, v: int) -> TaskSpec:
    d = PROMPT_DIR / name
    system = (d / f"v{v}.md").read_text(encoding="utf-8")
    schema = json.loads((d / f"v{v}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return TaskSpec(
        name=name,
        prompt_version=f"v{v}",
        schema_version=f"v{v}",
        system=system,
        schema=schema,
        max_tokens=MAX_TOKENS_VERSION.get((name, v), MAX_TOKENS[name]),
    )


__all__ = [
    "MAX_TOKENS",
    "MAX_TOKENS_VERSION",
    "PROMPT_DIR",
    "TaskSpec",
    "load_task",
    "parse_pins",
    "pin_versions",
    "versions",
]
