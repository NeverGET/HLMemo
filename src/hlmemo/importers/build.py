"""The file → records pipeline shared by every source (``hlm import <source>``).

``build(system, candidates, ...)`` applies, in this order: read (binary / empty / too large /
secret → skipped), ``@import`` stubs (resolved when the target is imported too, else skipped),
content-hash dedupe of identical copies (the shallowest path is kept, inventory §3), the export
format (``hlm_export: 1`` frontmatter → ``exportfmt``), sectioning (decision-log rows, dated
headings, one lesson per independent rule of a lesson file (``importers.lessons``), size split at
headings) and the temporal rule (explicit evidence only; > now + 5 min
rejects the item). Name-duplicates (same normalised name, different content, inventory §2/§4)
are reported, never merged. Output order is sorted by source key: the dry-run report is golden.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from hlmemo.importers import exportfmt, lessons
from hlmemo.importers.common import (
    SECTION_CHARS,
    GitInfo,
    ImportRecord,
    ParseResult,
    Reject,
    Section,
    Skip,
    dated_sections,
    decision_sections,
    dedupe_name,
    derive_title,
    evidence_instant,
    find_describes,
    is_future,
    iso_mtime,
    parse_frontmatter,
    read_text,
    section_split,
    sha256_text,
    stub_targets,
)

KindFn = Callable[[str, dict[str, Any], str], str]


@dataclass(slots=True)
class Candidate:
    path: Path  # on disk
    rel: str  # source path relative to the source's base (posix)


def _file_evidence(meta: dict[str, Any], tz: tzinfo | None) -> tuple[str | None, str]:
    for key in ("valid_from", "date"):
        if key in meta:
            iso = evidence_instant(meta[key], tz)
            if iso is not None:
                return iso, f"frontmatter:{key}"
    return None, "none"


def _parent_tag(rel: str) -> str | None:
    parts = rel.split("/")
    return parts[-2].lower() if len(parts) > 1 else None


def _tags(system: str, rel: str, meta: dict[str, Any]) -> list[str]:
    tags = [system, "imported"]
    parent = _parent_tag(rel)
    if parent:
        tags.append(parent)
    extra = meta.get("tags")
    if isinstance(extra, list):
        tags += [str(t) for t in extra if isinstance(t, str | int)]
    out: list[str] = []
    for t in tags:
        t = t.strip()[:64]
        if t and t not in out:
            out.append(t)
    return out[:32]


def build(
    system: str,
    candidates: list[Candidate],
    *,
    kind_fn: KindFn,
    repo: Path | None,
    git: GitInfo | None = None,
    now: datetime | None = None,
    scopes: list[str] | None = None,
    empty_sources: list[str] | None = None,
    tz: tzinfo | None = None,
    section_chars: int = SECTION_CHARS,
) -> ParseResult:
    now = now or datetime.now(UTC)
    res = ParseResult(scopes=sorted(scopes or []))
    for s in empty_sources or []:
        res.skipped.append(Skip(s, "empty-source"))
    texts: dict[str, tuple[Candidate, str]] = {}
    for c in sorted(candidates, key=lambda c: c.rel):
        text, why = read_text(c.path)
        if why:
            res.skipped.append(Skip(c.rel, why))
            continue
        assert text is not None
        texts[c.rel] = (c, text)

    # @import stubs (inventory §5): resolved when the target is imported in this run
    for rel in sorted(texts):
        c, text = texts[rel]
        targets = stub_targets(text)
        if targets is None:
            continue
        here = posixpath.dirname(rel)
        resolved = [
            t
            for t in targets
            if posixpath.normpath(posixpath.join(here, t)) in texts or t.lstrip("/") in texts
        ]
        reason = "stub-of:" if len(resolved) == len(targets) else "stub-unresolved:"
        res.skipped.append(Skip(rel, reason + ",".join(targets)))
        del texts[rel]

    # identical copies (inventory §3): keep the shallowest path, report the group
    by_sha: dict[str, list[str]] = {}
    for rel, (_c, text) in texts.items():
        by_sha.setdefault(sha256_text(text), []).append(rel)
    for _sha, rels in sorted(by_sha.items(), key=lambda kv: min(kv[1])):
        if len(rels) < 2:
            continue
        rels = sorted(rels, key=lambda r: (r.count("/"), r))
        kept, dups = rels[0], rels[1:]
        res.duplicate_groups.append({"reason": "same_content", "kept": kept, "paths": rels})
        for d in dups:
            res.skipped.append(Skip(d, f"duplicate-of:{kept}"))
            del texts[d]

    # same normalised name, different content (inventory §2/§4): reported, never merged
    by_name: dict[str, list[str]] = {}
    for rel in texts:
        by_name.setdefault(dedupe_name(rel), []).append(rel)
    for name, rels in sorted(by_name.items()):
        if len(rels) > 1:
            res.duplicate_groups.append({"reason": "same_name", "key": name, "paths": sorted(rels)})

    for rel in sorted(texts):
        c, text = texts[rel]
        mtime = iso_mtime(c.path)
        commit, commit_date = git.commit_of(c.path) if git else (None, None)
        meta, after = parse_frontmatter(text)
        if exportfmt.is_export(meta):
            rec = exportfmt.record_from_export(system, rel, meta, after)
            if rec is None:
                res.skipped.append(Skip(rel, "export-frontmatter-invalid"))
            else:
                _admit(res, rec, now)
            continue
        if exportfmt.is_index(text):
            res.skipped.append(Skip(rel, "export-index"))
            continue
        file_date, file_evidence = _file_evidence(meta, tz)
        sections = decision_sections(text, tz)
        if sections is None:
            dated, heading_date = dated_sections(text, tz)
            if heading_date is not None and file_date is None:
                file_date, file_evidence = heading_date, "dated-heading"
            sections = dated or [Section(None, text)]
        kind_default = kind_fn(rel, meta, text)
        tags = _tags(system, rel, meta)
        doc_title = derive_title(meta, text, rel).rsplit(" · ", 1)[0]
        if kind_default == "lesson" and sections == [Section(None, text)]:
            sections = lessons.split(after, meta, doc_title) or sections  # one lesson per rule
        used: set[str] = set()
        for sec in [p for s in sections for p in section_split(s, section_chars, doc_title)]:
            path = rel if sec.anchor is None else f"{rel}#{sec.anchor}"
            n, base_path = 1, path
            while path in used:  # a sub-section slug equal to a dated-entry anchor: keep keys unique
                n += 1
                path = f"{base_path}-{n}"
            used.add(path)
            date, evidence = (sec.date, sec.evidence) if sec.date else (file_date, file_evidence)
            rec = ImportRecord(
                system=system,
                path=path[:512],
                file=rel,
                sha256=sha256_text(sec.body),
                title=derive_title(meta if sec.anchor is None else {}, sec.body, rel, lead=sec.lead),
                body=sec.body,
                kind_guess=sec.kind or kind_default,
                tags=tags,
                describes=find_describes(sec.body, repo, self_path=rel),
                evidenced_valid_from=date,
                evidence=evidence if date else "none",
                mtime=mtime,
                commit=commit,
                commit_date=commit_date,
            )
            _admit(res, rec, now)
    res.records.sort(key=lambda r: r.key)
    res.skipped.sort(key=lambda s: (s.path, s.reason))
    res.rejected.sort(key=lambda r: r.key)
    return res


def _admit(res: ParseResult, rec: ImportRecord, now: datetime) -> None:
    """Sol #6: evidence more than 5 minutes in the future rejects the item (reason reported)."""
    dates = [rec.evidenced_valid_from]
    if rec.export is not None:
        dates.append(rec.export.get("valid_from"))
    for d in dates:
        if d is not None and is_future(d, now):
            res.rejected.append(Reject(rec.key, "future_evidence_date", d))
            return
    if not rec.body.strip():
        res.skipped.append(Skip(rec.path, "empty"))
        return
    res.records.append(rec)


__all__ = ["Candidate", "KindFn", "build"]
