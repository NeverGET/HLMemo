"""``hlm import automemory <dir>``: a Claude Code auto-memory directory
(``~/.claude/projects/<project>/memory/``: ``MEMORY.md`` index + topic files with frontmatter
``name``/``description``/``type``). Flat directory; keys are file names relative to ``<dir>``.

Kinds: ``type: feedback`` → lesson, everything else (``project``, ``reference``, ``user``, the
index) → fact. The frontmatter stays in the body (its description is good retrieval text); its
``date``/``valid_from`` is the only accepted date evidence besides dated headings (Sol #6).
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers.build import Candidate, build
from hlmemo.importers.common import SECTION_CHARS, ParseResult, walk_files

SYSTEM = "automemory"


def kind_for(rel: str, meta: dict[str, Any], text: str) -> str:
    kind = str(meta.get("type", "")).strip().lower()
    if kind in ("feedback", "lesson"):
        return "lesson"
    return "fact"


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


__all__ = ["SYSTEM", "kind_for", "parse"]
