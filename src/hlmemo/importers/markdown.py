"""``hlm import markdown <paths>``: Markdown files and directories (docs, notes, ``hlm export`` dirs).

Paths are keyed relative to ``base`` — the git work tree of the first path when it is inside
one (stable wherever the command runs), else the given directory. Kinds come from the path:
``decisions``/``adr`` → fact (one per decision row), ``consults``/``reviews``/``sessions`` →
episode, ``lesson*`` → lesson, ``status`` → fact, otherwise doc_chunk; dated-log entries → episode.
Files carrying the ``hlm_export: 1`` frontmatter are re-imported by ``logical_id`` (exportfmt).
"""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers.build import Candidate, build
from hlmemo.importers.common import GitInfo, ParseResult, git_toplevel, walk_files

SYSTEM = "markdown"
SUFFIXES = (".md", ".markdown")


def kind_for(rel: str, meta: dict[str, Any], text: str) -> str:
    low = rel.lower()
    dirs = low.split("/")[:-1]
    name = low.rsplit("/", 1)[-1]
    if any(d in ("decisions", "adr", "adrs") for d in dirs):
        return "fact"
    if any(d in ("consults", "reviews", "sessions", "journal") for d in dirs):
        return "episode"
    if re.search(r"lesson|footgun|gotcha", name) or any(d.startswith("lesson") for d in dirs):
        return "lesson"
    if any(d == "status" for d in dirs):
        return "fact"
    return "doc_chunk"


def resolve_base(paths: list[Path], base: Path | None) -> Path:
    if base is not None:
        return base.resolve()
    first = paths[0].resolve()
    top = git_toplevel(first)
    if top is not None:
        return top.resolve()
    return first if first.is_dir() else first.parent


def _rel(p: Path, base: Path) -> str:
    try:
        return p.resolve().relative_to(base).as_posix()
    except ValueError:
        return p.name


def parse(
    paths: list[Path],
    *,
    base: Path | None = None,
    repo: Path | None = None,
    now: datetime | None = None,
    use_git: bool = True,
    system: str = SYSTEM,
    tz: tzinfo | None = None,
) -> ParseResult:
    base_dir = resolve_base(paths, base)
    candidates: list[Candidate] = []
    scopes: list[str] = []
    empty: list[str] = []
    for p in paths:
        rel_root = _rel(p, base_dir)
        scopes.append("" if rel_root == "." else (rel_root + "/" if p.is_dir() else rel_root))
        files = walk_files(p, SUFFIXES)
        if not files:
            empty.append(rel_root)
        candidates += [Candidate(f, _rel(f, base_dir)) for f in files]
    seen: set[str] = set()
    uniq = [c for c in candidates if not (c.rel in seen or seen.add(c.rel))]
    git = GitInfo(git_toplevel(base_dir)) if use_git else None
    return build(
        system,
        uniq,
        kind_fn=kind_for,
        repo=repo if repo is not None else base_dir,
        git=git,
        now=now,
        scopes=scopes,
        empty_sources=empty,
        tz=tz,
    )


__all__ = ["SYSTEM", "kind_for", "parse", "resolve_base"]
