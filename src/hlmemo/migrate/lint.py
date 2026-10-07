"""`hlm migrate lint`: check a curated tree before any import, statically and through the real importer.

The layout (D-248, protocol §4):
- `status/`: one current-state fact per file;
- `lessons/`: one lesson per file, with `## Mistake / ## Fix / ## Context`;
- `sessions/`: dated episodes grouped in files, one `## YYYY-MM-DD …` heading per episode and nothing above
  the first one;
- `decisions/`: decision rows `D-NNN | YYYY-MM-DD | STATUS | text`, no preamble.
Every item carries an explicit date, or an estimated one marked by the `date-estimated` tag and a marker line.
Topic tags come from the spec's closed list.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hlmemo.core.explicit_supersession import Doc, propose
from hlmemo.core.secret_guard import strong_secret_rule
from hlmemo.importers.common import DATED_HEADING_RE, DECISION_ROW_RE, HEADING_RE, parse_frontmatter
from hlmemo.migrate.batches import Loaded
from hlmemo.migrate.spec import MigrationSpec

#: top directory -> the kind the markdown importer gives its items (importers/markdown.py)
LAYOUT = {"status": "fact", "lessons": "lesson", "sessions": "episode", "decisions": "fact"}
GROUPED = ("sessions", "decisions")
LESSON_SECTIONS = ("## Mistake", "## Fix", "## Context")
TITLE_HARD = 200  # the server's title limit
TITLE_AIM = 80  # protocol R6: aim for a title of at most 80 characters
LESSON_TITLE_MAX = 120  # the brief shows a lesson's title line (R15)
#: a decision row that names another D-id next to "yerine" reads as a FULL reversal to `hlm links explicit`
_YERINE_DID = re.compile(r"\bD-\d{3,4}\b[^|]{0,40}\byerine\b|\byerine\b[^|]{0,40}\bD-\d{3,4}\b", re.I)
_DID = re.compile(r"\bD-\d{3,4}\b")
_PARTIAL = re.compile(r"kısmını değiştirir|partly changes|partially (?:changes|replaces)", re.I)


@dataclass
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def e(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def w(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.errors


def _dated_headings(body: str) -> list[int]:
    """Line indexes of headings whose text starts with a date (the importer's dated-entry rule)."""
    out = []
    for i, line in enumerate(body.splitlines()):
        m = HEADING_RE.match(line)
        if m and DATED_HEADING_RE.match(m.group(2)):
            out.append(i)
    return out


def _tags(meta: dict[str, Any]) -> list[str]:
    t = meta.get("tags")
    if isinstance(t, list):
        return [str(x).strip() for x in t if str(x).strip()]
    if isinstance(t, str) and t.strip():
        return [x.strip().strip("'\"") for x in t.strip("[]").split(",") if x.strip()]
    return []


def _secret_lines(text: str) -> list[tuple[int, str]]:
    if strong_secret_rule(text) is None:
        return []
    hits = [(i + 1, r) for i, line in enumerate(text.splitlines()) if (r := strong_secret_rule(line))]
    return hits or [(0, strong_secret_rule(text) or "secret")]


def _check_file(spec: MigrationSpec, res: LintResult, root: Path, p: Path) -> None:
    rel = p.relative_to(root).as_posix()
    text = p.read_text(encoding="utf-8", errors="replace")
    for line_no, rule in _secret_lines(text):
        res.e(
            rel, f"token-shaped string (rule {rule}) at line {line_no}: remove it or name where it is stored"
        )
    parts = p.relative_to(root).parts
    top = parts[0] if len(parts) > 1 else ""
    if p.suffix not in (".md", ".markdown"):
        res.w(rel, "not markdown: the markdown importer ignores it")
        return
    if top not in LAYOUT:
        res.w(rel, f"outside {sorted(LAYOUT)}: it imports as a doc_chunk, not a curated item")
        return
    meta, body = parse_frontmatter(text)
    tags = _tags(meta)
    for t in tags:
        if not spec.tag_allowed(t):
            res.e(rel, f"tag {t!r} is not in the spec's closed tag list")
    if "date-estimated" in tags and spec.estimated_marker not in body:
        res.e(rel, f"`date-estimated` tag without the marker line {spec.estimated_marker!r}")
    if top in GROUPED:
        _check_grouped(res, rel, top, text)
        return
    if not meta:
        res.e(rel, "no frontmatter (title, date, tags, source_path)")
    if meta and not (meta.get("date") or meta.get("valid_from")):
        res.e(rel, "frontmatter has no `date` (explicit, or estimated with the date-estimated tag)")
    if _dated_headings(body):
        res.e(rel, "a dated heading inside a one-item file splits it into episodes")
    if top == "lessons":
        _check_lesson(res, rel, body, tags)


def _check_grouped(res: LintResult, rel: str, top: str, text: str) -> None:
    lines = text.splitlines()
    if top == "sessions":
        heads = _dated_headings(text)
        if len(heads) >= 2 and any(x.strip() for x in lines[: heads[0]]):
            res.e(
                rel,
                "text above the first dated heading becomes an extra (often undated) item: "
                "remove the preamble",
            )
        if not heads:
            res.e(rel, "no `## YYYY-MM-DD …` heading: the file imports as one undated item")
        return
    rows = [i for i, x in enumerate(lines) if DECISION_ROW_RE.match(x)]
    if len(rows) < 2:
        res.e(rel, "a decision log needs at least 2 rows `D-NNN | YYYY-MM-DD | STATUS | text`")
        return
    if any(x.strip() for x in lines[: rows[0]]):
        res.e(rel, "text above the first decision row becomes an extra item: remove the preamble")
    for i, x in enumerate(lines):
        if not x.strip() or DECISION_ROW_RE.match(x):
            continue
        res.w(rel, f"line {i + 1} is not a decision row: it becomes part of the row above it")
    for i in rows:
        row = lines[i]
        others = {d for d in _DID.findall(row) if not row.startswith(d)}
        if others and _YERINE_DID.search(row) and not _PARTIAL.search(row):
            res.w(
                rel,
                f"line {i + 1}: 'yerine' next to a D-id reads as a full reversal; write 'kısmını değiştirir' "
                "for a partial change",
            )


def _check_lesson(res: LintResult, rel: str, body: str, tags: list[str]) -> None:
    for h in LESSON_SECTIONS:
        if not re.search(rf"(?m)^{re.escape(h)}\s*$", body):
            res.e(rel, f"lesson without a `{h}` section")
    status = [t for t in tags if t.casefold() in ("active", "resolved")]
    if len(status) != 1:
        res.e(rel, "a lesson carries exactly one of the tags active | resolved")
    folded = {t.casefold() for t in tags}
    if "historical" in folded and "active" in folded:
        res.e(rel, "active + historical is a contradiction: a historical lesson is resolved (D-234)")
    if not any("@" in t for t in tags):
        res.w(rel, "no `<stack>@<version>` scope tag (R15)")
    if len(re.findall(r"(?m)^# ", body)) > 1:
        res.e(rel, "more than one H1: two rules in one file?")


def _check_parse(spec: MigrationSpec, res: LintResult, ld: Loaded) -> None:
    pr = ld.parsed
    per_file = Counter(r.file for r in pr.records)
    for r in pr.records:
        top = Path(r.file).parts[0] if Path(r.file).parts else ""
        want = LAYOUT.get(top)
        if want and r.kind_guess != want:
            res.e(r.path, f"the importer makes a {r.kind_guess}, the layout expects a {want}")
        if not r.evidenced_valid_from:
            res.e(r.path, "the importer finds no date: the item would get the import time and look newest")
        title = r.title.rsplit(" · ", 1)[0]
        if len(title) >= TITLE_HARD:
            res.e(r.path, f"title {len(title)} chars: the server limit is {TITLE_HARD}")
        elif r.kind_guess == "lesson" and len(title) > LESSON_TITLE_MAX:
            res.e(r.path, f"lesson title {len(title)} chars > {LESSON_TITLE_MAX} (the brief shows it)")
        elif r.kind_guess in ("fact", "episode") and len(title) > TITLE_AIM and top != "decisions":
            res.w(r.path, f"title {len(title)} chars > {TITLE_AIM} (R6 aims at 80)")
        if top in GROUPED and per_file[r.file] > 1 and "#" not in r.path:
            res.e(r.path, "a preamble item: text above the first entry of a grouped file")
    for f, n in per_file.items():
        top = Path(f).parts[0] if Path(f).parts else ""
        if top in ("status", "lessons") and n != 1:
            res.e(
                f, f"the importer makes {n} items from a one-item file (split it, or remove inner headings)"
            )
    for s in pr.skipped:
        res.e(s.path, f"the importer skips it: {s.reason}")
    for rj in pr.rejected:
        res.e(rj.key, f"the importer rejects it: {rj.reason}")
    for g in pr.duplicate_groups:
        res.w(
            ", ".join(g.get("paths", [])), f"file names collide ({g.get('reason')}): rename for unique keys"
        )


def lint(spec: MigrationSpec, loaded: list[Loaded]) -> LintResult:
    res = LintResult()
    root = spec.curated_dir
    if not root.is_dir():
        res.e(str(root), "curated_dir does not exist")
        return res
    files = sorted(p for p in root.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.parts))
    names: dict[str, list[str]] = defaultdict(list)
    for p in files:
        names[p.name].append(p.relative_to(root).as_posix())
        _check_file(spec, res, root, p)
    for n, where in names.items():
        if len(where) > 1:
            res.e(
                where[0],
                f"file name {n!r} is not unique ({len(where)} files): the importer may merge or confuse them",
            )
    docs: list[Doc] = []
    for ld in loaded:
        _check_parse(spec, res, ld)
        for r in ld.parsed.records:
            docs.append(
                Doc(version_id=len(docs) + 1, logical_id=len(docs) + 100000, path=r.path, body=r.body)
            )
    for p in propose(docs):
        res.links.append({"newer": p.source_ref, "older": p.target_ref, "scope": p.scope, "marker": p.marker})
    kinds = Counter(r.kind_guess for ld in loaded for r in ld.parsed.records)
    res.summary = {
        "files": len(files),
        "items": sum(kinds.values()),
        "kinds": dict(kinds),
        "estimated": sum("date-estimated" in r.tags for ld in loaded for r in ld.parsed.records),
        "link_proposals": len(res.links),
        "errors": len(res.errors),
        "warnings": len(res.warnings),
    }
    return res


__all__ = ["LAYOUT", "LintResult", "lint"]
