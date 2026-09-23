"""Versioned librarian prompts (PHASE2-4-ROADMAP §2 Architecture).

Each task has ``<task>/v<N>.md`` (the static system prompt, which comes first in every request so
provider prompt caching applies, D-019) and ``<task>/v<N>.schema.json`` (the JSON schema the
response must satisfy). ``load_task(name)`` picks the highest version unless one is pinned.
Model quirks are never written here: a profile's ``prompt_overrides[<task>].system_append`` is
appended at request time (D-017).
"""

from __future__ import annotations

import json
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
    "synthesis": 700,
}

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


@lru_cache(maxsize=32)
def load_task(name: str, version: int | None = None) -> TaskSpec:
    if name not in MAX_TOKENS:
        raise KeyError(f"unknown librarian task {name!r}")
    available = versions(name)
    if not available:
        raise FileNotFoundError(f"no prompt for task {name!r} under {PROMPT_DIR}")
    v = version if version is not None else available[-1]
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
        max_tokens=MAX_TOKENS[name],
    )


__all__ = ["MAX_TOKENS", "PROMPT_DIR", "TaskSpec", "load_task", "versions"]
