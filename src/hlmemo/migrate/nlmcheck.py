"""`hlm migrate nlm-check`: flag NotebookLM notes that look cut off when they were written.

The second migration found that NotebookLM had cut many notes after their first `<` at WRITE time (rich-text
bodies: `<id>`, `Value<number>`), and nobody noticed for months (kit feedback #6, #19). The reader returns
what was stored, so every NotebookLM-first project probably holds cut notes. This is a DETECTOR for a human
to confirm, not a fixer: the cut text has to be recovered from the store written alongside (serena, git,
session files).

Signals, per note:
- `open-fence`: an odd number of ``` fence lines (a code block that never closes);
- `ends-mid-sentence`: the last line is prose that ends without terminal punctuation;
- `open-construct`: the last line leaves an inline-code span, a bracket or a parenthesis open;
- `cut-before-angle` (needs --originals): an original holds the note's last 8 words followed by a `<`-token;
- `original-continues` (needs --originals): the note is mostly found in one original (8-word shingles), the
  original holds the note's last 8 words, and continues well past them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hlmemo.importers.common import parse_frontmatter
from hlmemo.migrate.containment import TEXT_SUFFIXES, shingles, words
from hlmemo.migrate.redact import redact

TAIL_WORDS = 8
CONTAINED = 0.6  # share of the note's shingles found in its best original for `original-continues`
CONTINUES_CHARS = 200  # the original goes on for at least this many characters after the note's last words
_TERMINAL = tuple(".!?:;)]}…\"'`»”’*_|>")
_LIST_OR_TABLE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||#{1,6}\s|>)")
_ANGLE_TOKEN = re.compile(r"\s*<[A-Za-z/!][^>\n]{0,80}>?")


@dataclass
class NoteFlag:
    file: str
    note_id: str
    title: str
    reasons: list[str] = field(default_factory=list)
    original: str | None = None


def _note_files(export: Path) -> list[Path]:
    root = export.expanduser()
    if root.is_file():
        return [root]
    notes = root / "notes"
    base = notes if notes.is_dir() else root
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".markdown"))


def _originals(paths: list[Path]) -> list[tuple[str, str]]:
    """(shown name, text) per original file. The name is relative to the --originals argument it came from
    (`<dir name>/<path inside>`, or the file name): never the absolute path with its home directory."""
    out: list[tuple[str, str]] = []
    for p in paths:
        p = p.expanduser()
        files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file())
        for f in files:
            if f.suffix.lower() in TEXT_SUFFIXES:
                shown = f.name if f == p else f"{p.name}/{f.relative_to(p).as_posix()}"
                out.append((shown, f.read_text(encoding="utf-8", errors="replace")))
    return out


def structural_reasons(body: str) -> list[str]:
    """The signals that need no originals."""
    lines = [ln for ln in body.rstrip().splitlines()]
    reasons: list[str] = []
    if sum(1 for ln in lines if ln.lstrip().startswith("```")) % 2:
        reasons.append("open-fence")
    last = next((ln.rstrip() for ln in reversed(lines) if ln.strip()), "")
    if not last or _LIST_OR_TABLE.match(last) or last.lstrip().startswith("```"):
        return reasons
    if last.count("`") % 2 or last.count("[") > last.count("]") or last.count("(") > last.count(")"):
        reasons.append("open-construct")
    if re.search(r"\w$", last) and not last.endswith(_TERMINAL):
        reasons.append("ends-mid-sentence")
    return reasons


def _tail_regex(body: str) -> re.Pattern[str] | None:
    tail = words(body)[-TAIL_WORDS:]
    if len(tail) < TAIL_WORDS:
        return None
    return re.compile(r"\W+".join(re.escape(w) for w in tail), re.IGNORECASE)


def original_reasons(body: str, originals: list[tuple[str, str]]) -> tuple[list[str], str | None]:
    """`cut-before-angle` / `original-continues` against the originals, with the matched original's path."""
    rx = _tail_regex(body)
    if rx is None or not originals:
        return [], None
    for path, text in originals:
        for m in rx.finditer(text):
            if _ANGLE_TOKEN.match(text, m.end()):
                return ["cut-before-angle"], path
    note_sh = shingles(body, TAIL_WORDS)
    if not note_sh:
        return [], None
    best_path, best_share, best_text = None, 0.0, ""
    for path, text in originals:
        share = len(note_sh & shingles(text, TAIL_WORDS)) / len(note_sh)
        if share > best_share:
            best_path, best_share, best_text = path, share, text
    if best_path and best_share >= CONTAINED:
        # the last occurrence: what follows it is what the note may have lost
        matches = list(rx.finditer(best_text))
        m = matches[-1] if matches else None
        if m is not None and len(best_text) - m.end() >= CONTINUES_CHARS:
            return ["original-continues"], best_path
    return [], best_path if best_share >= CONTAINED else None


def check(
    export: Path, originals: list[Path] | None = None, mask: Callable[..., str] = redact
) -> tuple[list[NoteFlag], int]:
    """(flags, notes checked). Every shown value (file, note id, title, original) is masked in full before
    the title is cut (review 117)."""
    origs = _originals(originals or [])
    flags: list[NoteFlag] = []
    files = _note_files(export)
    root = export.expanduser()
    for f in files:
        meta, body = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        reasons = structural_reasons(body)
        extra, original = original_reasons(body, origs)
        reasons += extra
        if not reasons:
            continue
        rel = f.relative_to(root).as_posix() if root.is_dir() else f.name
        flags.append(
            NoteFlag(
                file=mask(rel),
                note_id=mask(str(meta.get("nlm_note_id") or meta.get("id") or "")),
                title=mask(str(meta.get("title") or f.stem), width=120),
                reasons=reasons,
                original=mask(original) if extra and original else None,
            )
        )
    return flags, len(files)


__all__ = ["NoteFlag", "check", "original_reasons", "structural_reasons"]
