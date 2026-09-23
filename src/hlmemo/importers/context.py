"""``hlm import context <files|dirs>``: agent context files — ``CLAUDE.md``, ``AGENTS.md``,
``GEMINI.md`` and ``.mcp.json`` (inventory §1). Directories are searched for exactly these names
(junk and hidden directories pruned). Keys are relative to the git work tree (else the given
directory). ``@import`` one-line stubs (``@AGENTS.md``) are resolved or skipped (inventory §5).
Every file is a ``fact``; a ``.mcp.json`` that matches a secret pattern is never read into a
payload (skipped with the pattern's name).
"""

from __future__ import annotations

import os
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers.build import Candidate, build
from hlmemo.importers.common import JUNK_DIRS, GitInfo, ParseResult, git_toplevel
from hlmemo.importers.markdown import resolve_base

SYSTEM = "context"
NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".mcp.json")


def kind_for(rel: str, meta: dict[str, Any], text: str) -> str:
    return "fact"


def _find(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in JUNK_DIRS and not d.startswith("."))
        out += [Path(dirpath) / n for n in sorted(filenames) if n in NAMES]
    return sorted(p for p in out if p.is_file() and not p.is_symlink())


def parse(
    paths: list[Path],
    *,
    base: Path | None = None,
    repo: Path | None = None,
    now: datetime | None = None,
    system: str = SYSTEM,
    tz: tzinfo | None = None,
) -> ParseResult:
    base_dir = resolve_base(paths, base)
    candidates: list[Candidate] = []
    scopes: list[str] = []
    empty: list[str] = []
    for p in paths:
        try:
            rel_root = p.resolve().relative_to(base_dir).as_posix()
        except ValueError:
            rel_root = p.name
        scopes.append("" if rel_root == "." else (rel_root + "/" if p.is_dir() else rel_root))
        found = _find(p)
        if not found:
            empty.append(rel_root)
        for f in found:
            try:
                rel = f.resolve().relative_to(base_dir).as_posix()
            except ValueError:
                rel = f.name
            candidates.append(Candidate(f, rel))
    seen: set[str] = set()
    uniq = [c for c in candidates if not (c.rel in seen or seen.add(c.rel))]
    return build(
        system,
        uniq,
        kind_fn=kind_for,
        repo=repo if repo is not None else base_dir,
        git=GitInfo(git_toplevel(base_dir)),
        now=now,
        scopes=scopes,
        empty_sources=empty,
        tz=tz,
    )


__all__ = ["NAMES", "SYSTEM", "kind_for", "parse"]
