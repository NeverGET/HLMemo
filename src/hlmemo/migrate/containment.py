"""`hlm migrate containment`: how much of each exported file is found in the original stores.

For layered legacy memory (PLAYBOOK §5.9): a newer store (often a NotebookLM notebook) bundled an older,
finer-grained store. Line-exact comparison fails because the export reflows markdown, while word shingles
still find the shared text (kit feedback #4). A file whose shingles are mostly found in the originals is a
bundle of them (use the originals, cross-check only); a low share marks text written only in the newer store
(import it from there).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from hlmemo.migrate.redact import redact

WORD_RE = re.compile(r"\w+", re.UNICODE)
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".json")


def words(text: str) -> list[str]:
    return [w.casefold() for w in WORD_RE.findall(text)]


def shingles(text: str, n: int) -> set[tuple[str, ...]]:
    ws = words(text)
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def _files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = p.expanduser()
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(
                f
                for f in p.rglob("*")
                if f.is_file()
                and f.suffix.lower() in TEXT_SUFFIXES
                and not any(x.startswith(".") and x not in (".serena",) for x in f.relative_to(p).parts)
            )
    return out


@dataclass(frozen=True)
class FileShare:
    file: str
    shingles: int
    found: int

    @property
    def share(self) -> float | None:
        return self.found / self.shingles if self.shingles else None


def containment(export: Path, originals: list[Path], n: int = 8) -> tuple[list[FileShare], dict[str, object]]:
    """Per export file: its n-word shingles and how many occur anywhere in the originals."""
    pool: set[tuple[str, ...]] = set()
    for f in _files(originals):
        pool |= shingles(f.read_text(encoding="utf-8", errors="replace"), n)
    rows: list[FileShare] = []
    root = export.expanduser()
    for f in _files([root]):
        sh = shingles(f.read_text(encoding="utf-8", errors="replace"), n)
        rel = f.relative_to(root).as_posix() if root.is_dir() else f.name
        rows.append(FileShare(redact(rel), len(sh), len(sh & pool)))  # a shown name is masked (review 117)
    shares = [r.share for r in rows if r.share is not None]
    summary: dict[str, object] = {
        "files": len(rows),
        "too_short": sum(r.share is None for r in rows),
        "shingle": n,
        "original_shingles": len(pool),
        "mean": round(statistics.mean(shares), 3) if shares else None,
        "median": round(statistics.median(shares), 3) if shares else None,
        "at_least_0.85": sum(s >= 0.85 for s in shares),
        "below_0.50": sum(s < 0.5 for s in shares),
    }
    return sorted(rows, key=lambda r: (r.share is None, -(r.share or 0.0), r.file)), summary


__all__ = ["FileShare", "containment", "shingles", "words"]
