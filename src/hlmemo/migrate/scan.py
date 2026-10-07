"""The personal-data and credential scan of a curated tree (`tools/migrate/scan.py`, PLAYBOOK §10).

gitleaks and the importer's filter look for credentials. The second migration's real exposure was personal
data: mail addresses, device ids, IP addresses, account and tax ids, local key paths (kit feedback #5).
This scan covers both, plus a known-value compare: a 0600 file of literal values (the owner's own address, an
id, a token) that must not appear anywhere. Every report line names the rule and file:line and MASKS every
value (`migrate/redact`, the kit's one masking function, on the full line before any cut), so the scan's own
output can be pasted into a chat or a review package.

Accepting a hit: `[scan].allow = ["<rule>:<path>:<line>"]` for one place, `[scan].allow_values = [...]` for a
value accepted wherever it occurs (a public support address). Project rules: `[[scan.patterns]] id, regex,
mask`. An accepted value is still masked in the printed line.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hlmemo.migrate.redact import KNOWN_VALUES_ENV, apply, find, load_known_values, tckn_valid
from hlmemo.migrate.spec import MigrationSpec

MAX_FILE_BYTES = 2_000_000
CONTEXT_WIDTH = 160


@dataclass(frozen=True)
class Hit:
    """One finding. `context` is the MASKED line: a hit never carries the raw value."""

    rule: str
    path: str
    line: int
    context: str
    mask: str

    @property
    def where(self) -> str:
        return f"{self.rule}:{self.path}:{self.line}"


def _text_files(spec: MigrationSpec) -> Iterator[tuple[str, Path]]:
    root = spec.curated_dir
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part.startswith(".") for part in p.relative_to(root).parts) or spec.excluded(rel):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        yield rel, p


def scan(spec: MigrationSpec, known: tuple[str, ...] = ()) -> tuple[list[Hit], dict[str, int]]:
    """Every finding in the curated tree that no allow entry accepts, plus counts (files, lines, allowed)."""
    hits: list[Hit] = []
    stats = {"files": 0, "binary": 0, "lines": 0, "allowed": 0}
    for rel, p in _text_files(spec):
        data = p.read_bytes()
        if b"\x00" in data:
            stats["binary"] += 1
            continue
        stats["files"] += 1
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            stats["lines"] += 1
            found = find(line, patterns=spec.scan_patterns, known=known)
            if not found:
                continue
            text = line.strip()
            masked = apply(text, find(text, patterns=spec.scan_patterns, known=known), CONTEXT_WIDTH)
            shown = apply(rel, find(rel, patterns=spec.scan_patterns, known=known))
            for f in found:
                value = line[f.start : f.end]
                if f"{f.rule}:{rel}:{n}" in spec.scan_allow or value in spec.scan_allow_values:
                    stats["allowed"] += 1
                    continue
                hits.append(Hit(f.rule, shown, n, masked, f.mask))
    return hits, stats


def known_values_file() -> Path | None:
    """The HLM_SCAN_KNOWN_VALUES file, when set."""
    v = os.environ.get(KNOWN_VALUES_ENV)
    return Path(v).expanduser() if v else None


__all__ = ["Hit", "known_values_file", "load_known_values", "scan", "tckn_valid"]
