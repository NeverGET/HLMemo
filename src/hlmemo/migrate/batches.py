"""Parse a spec's sources with the real importers and cut them into per-file, chronological batches (D-248).

A batch is the month of a FILE's newest explicit date, read in the spec's zone; a file without any explicit
date goes to the `undated` batch, which runs last. All records of one file land in one batch, so a file never
spans two runs.
"""

from __future__ import annotations

import collections
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from hlmemo.importers.cli import parse_source, resolve_tz
from hlmemo.importers.common import ImportRecord, ParseResult
from hlmemo.migrate.spec import MigrationSpec, Source

UNDATED = "undated"


@dataclass
class Loaded:
    source: Source
    parsed: ParseResult
    #: file -> batch key (`YYYY-MM` or `undated`)
    buckets: dict[str, str] = field(default_factory=dict)

    def bucket(self, rec: ImportRecord) -> str:
        return self.buckets.get(rec.file, UNDATED)


def file_buckets(records: list[ImportRecord], tz_name: str) -> dict[str, str]:
    """file -> the month (in `tz_name`) of the file's newest explicit date; files without one are absent."""
    tz = resolve_tz(tz_name)
    newest: dict[str, datetime] = {}
    for r in records:
        if not r.evidenced_valid_from:
            continue
        d = datetime.fromisoformat(r.evidenced_valid_from.replace("Z", "+00:00"))
        if r.file not in newest or d > newest[r.file]:
            newest[r.file] = d
    return {f: d.astimezone(tz).strftime("%Y-%m") for f, d in newest.items()}


def load(spec: MigrationSpec, *, now: datetime | None = None) -> list[Loaded]:
    """Every source of the spec, parsed exactly as `hlm import` would (same keys, kinds and dates)."""
    tz = resolve_tz(spec.tz)
    now = now or datetime.now(UTC)
    out: list[Loaded] = []
    for src in spec.sources:
        pr = parse_source(
            src.importer,
            [src.path],
            now=now,
            tz=tz,
            section_chars=src.section_chars,
            base=src.base,
            repo=spec.repo,
        )
        out.append(Loaded(src, pr, file_buckets(pr.records, spec.tz)))
    return out


def batch_keys(loaded: list[Loaded]) -> list[str]:
    """Month keys oldest first, `undated` last."""
    keys = {ld.bucket(r) for ld in loaded for r in ld.parsed.records}
    return sorted(keys, key=lambda k: (k == UNDATED, k))


def subset(ld: Loaded, key: str) -> ParseResult:
    """The records of one batch, in source-key order (the importer writes in key order anyway)."""
    s = copy.copy(ld.parsed)
    s.records = sorted((r for r in ld.parsed.records if ld.bucket(r) == key), key=lambda r: r.key)
    return s


def batch_counts(loaded: list[Loaded]) -> dict[str, int]:
    counts: dict[str, int] = collections.Counter()
    for ld in loaded:
        for r in ld.parsed.records:
            counts[ld.bucket(r)] += 1
    return dict(sorted(counts.items(), key=lambda kv: (kv[0] == UNDATED, kv[0])))


def plan(loaded: list[Loaded]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in batch_keys(loaded):
        for ld in loaded:
            recs = subset(ld, key).records
            if not recs:
                continue
            dates = sorted({r.evidenced_valid_from for r in recs if r.evidenced_valid_from})
            rows.append(
                {
                    "batch": key,
                    "source": ld.source.importer,
                    "path": str(ld.source.path),
                    "files": len({r.file for r in recs}),
                    "items": len(recs),
                    "kinds": dict(collections.Counter(r.kind_guess for r in recs)),
                    "estimated": sum("date-estimated" in r.tags for r in recs),
                    "valid_from_min": dates[0] if dates else None,
                    "valid_from_max": dates[-1] if dates else None,
                }
            )
    return rows


__all__ = ["UNDATED", "Loaded", "batch_counts", "batch_keys", "file_buckets", "load", "plan", "subset"]
