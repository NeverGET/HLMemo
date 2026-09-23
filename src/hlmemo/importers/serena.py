"""``hlm import serena <dir>``: serena MCP memories (``.serena/memories/*.md``: H1-first Markdown,
no frontmatter, numeric-ordered ``01_...`` or topical names; inventory §1–2).

``<dir>`` is the memories directory or a project root containing ``.serena/memories``. Keys are
file names relative to the memories directory. Kinds from the name: lesson/footgun/feedback/
rule/gotcha → lesson, incident/outage/postmortem/session → episode, else fact. ``describes`` is
resolved against ``--repo`` (default: the project root when given one).
"""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers.build import Candidate, build
from hlmemo.importers.common import SECTION_CHARS, GitInfo, ParseResult, git_toplevel, walk_files

SYSTEM = "serena"


def kind_for(rel: str, meta: dict[str, Any], text: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    if re.search(r"lesson|footgun|feedback|gotcha|(^|[_-])rules?([_.-]|$)", name):
        return "lesson"
    if re.search(r"incident|outage|postmortem|session", name):
        return "episode"
    return "fact"


def memories_dir(path: Path) -> Path:
    p = path.resolve()
    nested = p / ".serena" / "memories"
    return nested if nested.is_dir() else p


def parse(
    directory: Path,
    *,
    repo: Path | None = None,
    now: datetime | None = None,
    system: str = SYSTEM,
    tz: tzinfo | None = None,
    section_chars: int = SECTION_CHARS,
) -> ParseResult:
    mdir = memories_dir(directory)
    project_root = mdir.parent.parent if mdir.parent.name == ".serena" else None
    files = walk_files(mdir, (".md",), recursive=False) if mdir.is_dir() else []
    top = git_toplevel(mdir) if mdir.is_dir() else None
    return build(
        system,
        [Candidate(f, f.name) for f in files],
        kind_fn=kind_for,
        repo=repo if repo is not None else project_root,
        git=GitInfo(top),
        now=now,
        scopes=[""],
        empty_sources=[] if files else [mdir.name],
        tz=tz,
        section_chars=section_chars,
    )


__all__ = ["SYSTEM", "kind_for", "memories_dir", "parse"]
