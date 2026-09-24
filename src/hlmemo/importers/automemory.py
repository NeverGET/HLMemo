"""``hlm import automemory <dir>``: a Claude Code auto-memory directory
(``~/.claude/projects/<project>/memory/``: ``MEMORY.md`` index + topic files with frontmatter
``name``/``description`` and a ``type``). Flat directory; keys are file names relative to ``<dir>``.

The ``type`` sits at the top level in older files and under ``metadata:`` in current Claude Code
(``metadata:\\n  type: feedback``); both are read (e2e 2026-09-24 finding #1: the nested form
was missed, so no feedback file became a lesson). Kinds (``TYPE_KINDS``): ``feedback`` → lesson;
``user`` (who the owner is, preferences), ``project`` (state, goals, decisions) and ``reference``
(pointers to external systems) → fact, like the ``MEMORY.md`` index and untyped files: HLMemo
has no preference/reference kind (``write_models.Kind``) and the legacy inventory
(docs/research/03) prescribes none. A lesson file with several independent rules becomes one
lesson per rule (``importers.lessons``, applied by ``build``).

The frontmatter stays in the body of a single item (its description is good retrieval text); a
split rule carries the description in its ``## Context``. The frontmatter's
``date``/``valid_from`` is the only accepted date evidence besides dated headings (Sol #6).
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers.build import Candidate, build
from hlmemo.importers.common import SECTION_CHARS, ParseResult, walk_files

SYSTEM = "automemory"
TYPE_KINDS = {
    "feedback": "lesson",
    "lesson": "lesson",
    "user": "fact",
    "project": "fact",
    "reference": "fact",
}


def memory_type(meta: dict[str, Any]) -> str:
    """The file's auto-memory type: top-level ``type`` (older files) or ``metadata.type``."""
    value = meta.get("type")
    if not (isinstance(value, str) and value.strip()):
        nested = meta.get("metadata")
        value = nested.get("type") if isinstance(nested, dict) else None
    return value.strip().lower() if isinstance(value, str) else ""


def kind_for(rel: str, meta: dict[str, Any], text: str) -> str:
    return TYPE_KINDS.get(memory_type(meta), "fact")


def parse(
    directory: Path,
    *,
    repo: Path | None = None,
    now: datetime | None = None,
    system: str = SYSTEM,
    tz: tzinfo | None = None,
    section_chars: int = SECTION_CHARS,
) -> ParseResult:
    d = directory.resolve()
    files = walk_files(d, (".md",), recursive=False) if d.is_dir() else []
    return build(
        system,
        [Candidate(f, f.name) for f in files],
        kind_fn=kind_for,
        repo=repo,
        git=None,  # auto-memory lives outside any repository
        now=now,
        scopes=[""],
        empty_sources=[] if files else [d.name],
        tz=tz,
        section_chars=section_chars,
    )


__all__ = ["SYSTEM", "TYPE_KINDS", "kind_for", "memory_type", "parse"]
