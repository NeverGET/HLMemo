"""Shared, deterministic building blocks of the W1.5 importers (no network, no LLM).

The rules come from ``docs/research/03-legacy-memory-inventory-public.md`` §1–10 and the W1.5
temporal rule (Sol #6, PHASE2-4-ROADMAP W1.5):

* titles are derived (frontmatter ``title``/``name`` > first H1 > humanised file name) and carry
  the source path so identifier queries can find them (D-055 title list);
* dedupe keys normalise separators and strip numeric prefixes (``01_auth-flow.md`` ≙ ``auth_flow``);
* identical content (monorepo copies) is imported once; ``@import`` stubs are resolved or skipped;
  empty files and empty sources are skipped;
* ``valid_from`` only from explicit evidence — frontmatter ``valid_from``/``date``, a dated
  decision-log row (``D-047 | 2026-09-23 |``) or a dated heading — never from mtime or a commit
  date (those are provenance). Evidence more than 5 minutes in the future rejects the item;
* bodies above the 64,000-character item limit are split at headings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

ITEM_BODY_MAX = 64000
TITLE_MAX = 200
SOURCE_PATH_MAX = 512
DESCRIBES_MAX = 16
FUTURE_SLACK = timedelta(minutes=5)
MAX_FILE_BYTES = 2_000_000
JUNK_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "vendor", ".tox", ".mypy_cache"}
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DECISION_ROW_RE = re.compile(r"^(D-\d{3,4}) \| (\d{4}-\d{2}-\d{2}) \| ")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
STUB_LINE_RE = re.compile(r"^@\S+$")
NUM_PREFIX_RE = re.compile(r"^\d+[-_. ]*")

# Strong secret shapes (from eval/realdata/import_corpus.py): a file with one never enters a payload.
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "sk-api-key": re.compile(r"\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{24,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "hlm-token": re.compile(r"\bhlm_[A-Za-z0-9_-]{43}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "assigned-secret": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key)"
        r"\b[\"']?\s*[:=]\s*[\"']?(?![<$\{*]|your|changeme|example|xxx)"
        r"(?=[^\s\"'`]*\d)(?=[^\s\"'`]*[A-Za-z])[A-Za-z0-9+/=_\-.!@#%^&]{16,}"
    ),
    "dsn-with-password": re.compile(r"\b[a-z][a-z0-9+]*://[^\s:/@<>{}$]+:(?![<$\{*])[^\s@<>{}$]{6,}@"),
}


# --------------------------------------------------------------------------- record
@dataclass(slots=True)
class ImportRecord:
    """One memory item derived from a source file (or a part of one).

    ``path`` is the source path relative to the source's base, plus ``#anchor`` for a part
    (``DECISIONS.md#D-047``); ``sha256`` hashes exactly ``body``. ``source_key`` =
    ``system:path`` is what the server's ownership rule and the manifest key on."""

    system: str
    path: str
    file: str
    sha256: str
    title: str
    body: str
    kind_guess: str
    tags: list[str]
    describes: list[str] = field(default_factory=list)
    evidenced_valid_from: str | None = None
    evidence: str = "none"
    mtime: str | None = None
    commit: str | None = None
    commit_date: str | None = None
    # hlm export round-trip (exportfmt): values recorded by the export, applied verbatim
    export: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        if self.export is not None and self.export.get("source") is None:
            return f"export:{self.path}"
        src = self.source()
        return f"{src['system']}:{src['path']}"

    def source(self) -> dict[str, Any]:
        if self.export is not None and self.export.get("source") is not None:
            return dict(self.export["source"])
        out: dict[str, Any] = {"system": self.system, "path": self.path, "sha256": self.sha256}
        if self.mtime:
            out["mtime"] = self.mtime
        if self.commit:
            out["commit"] = self.commit
        if self.commit_date:
            out["commit_date"] = self.commit_date
        return out


@dataclass(slots=True)
class Skip:
    path: str
    reason: str


@dataclass(slots=True)
class Reject:
    key: str
    reason: str
    date: str | None = None


@dataclass(slots=True)
class ParseResult:
    records: list[ImportRecord] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    rejected: list[Reject] = field(default_factory=list)
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)  # path prefixes this run covers (missing-item report)


# --------------------------------------------------------------------------- text
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> tuple[str | None, str | None]:
    """``(text, None)`` or ``(None, reason)``: binary, not UTF-8, empty, too large or a secret."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "too-large"
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"unreadable:{type(exc).__name__}"
    if b"\x00" in raw[:8192]:
        return None, "binary"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "binary"
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return None, "empty"
    hit = secret_hit(text)
    if hit:
        return None, f"secret-pattern:{hit}"
    return text, None


def secret_hit(text: str) -> str | None:
    for name, rx in SECRET_PATTERNS.items():
        if rx.search(text):
            return name
    return None


def stub_targets(text: str) -> list[str] | None:
    """``@import`` stubs (inventory §5): every non-empty line is ``@<path>`` and the file is tiny."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or len(text) > 400 or not all(STUB_LINE_RE.match(ln) for ln in lines):
        return None
    return [ln[1:] for ln in lines]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """A restricted YAML subset: ``key: value`` lines; JSON values are decoded (the export format
    writes JSON flow values), other values are unquoted strings. Returns ``({}, text)`` if absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line or line[:1].isspace():
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            continue
        try:
            meta[key] = json.loads(raw) if raw else ""
        except ValueError:
            if raw.startswith("[") and raw.endswith("]"):  # YAML flow list of bare words
                meta[key] = [w.strip().strip("'\"") for w in raw[1:-1].split(",") if w.strip()]
                continue
            meta[key] = raw.strip("'\"") if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else raw
    return meta, text[m.end() :]


def headings(text: str) -> list[tuple[int, int, str]]:
    """``(char offset, level, text)`` of ATX headings outside code fences."""
    out: list[tuple[int, int, str]] = []
    pos, fence = 0, None
    for line in text.splitlines(keepends=True):
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        elif fence is None:
            h = HEADING_RE.match(line.rstrip("\n"))
            if h:
                out.append((pos, len(h.group(1)), h.group(2).strip()))
        pos += len(line)
    return out


def slug(text: str, max_len: int = 48) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return (s[:max_len].rstrip("-")) or "section"


def dedupe_name(name: str) -> str:
    """Inventory §2: separator-normalised, numeric-prefix-stripped key
    (``01_Auth-Flow.md`` → ``auth-flow``)."""
    stem = name.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(md|markdown|txt|json)$", "", stem, flags=re.I)
    stem = NUM_PREFIX_RE.sub("", stem.lower())
    return re.sub(r"[-_. ]+", "-", stem).strip("-")


def humanize(name: str) -> str:
    if name.rsplit("/", 1)[-1] == ".mcp.json":
        return "MCP server configuration"
    stem = re.sub(r"\.(md|markdown|txt|json)$", "", name.rsplit("/", 1)[-1], flags=re.I)
    stem = NUM_PREFIX_RE.sub("", stem) or stem
    return re.sub(r"[-_]+", " ", stem).strip() or name


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def derive_title(meta: dict[str, Any], body: str, file: str, lead: str | None = None) -> str:
    """frontmatter title/name > ``lead`` (a part's own heading) > first H1 > humanised name, then
    `` · <file>`` so the path is part of the indexed title (D-055)."""
    base = None
    for k in ("title", "name"):
        if isinstance(meta.get(k), str) and meta[k].strip():
            base = meta[k].strip()
            break
    if lead:
        base = lead
    if base is None:
        h1 = next((t for _o, lvl, t in headings(body) if lvl == 1), None)
        base = h1 or humanize(file)
    base = re.sub(r"\s+", " ", base).strip()
    suffix = f" · {file}"
    if file in base:
        return clip(base, TITLE_MAX)
    return clip(clip(base, TITLE_MAX - min(len(suffix), 120)) + suffix, TITLE_MAX)


# --------------------------------------------------------------------------- dates (Sol #6)
def evidence_instant(raw: Any, tz: tzinfo | None = None) -> str | None:
    """An explicit evidence date → canonical UTC ISO, else None.

    A date without a time (``2026-09-24``) or a naive timestamp is read in ``tz`` — the calendar of
    the person who wrote it (``hlm import --tz``, default the machine's local zone; tests pass UTC).
    Reading it as UTC midnight would push a date written "today" east of UTC into the future and
    reject it (found on the owner's machine at UTC+3)."""
    zone = tz or UTC
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            s += "T00:00:00"
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    dt = dt.astimezone(UTC)
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if dt.microsecond else "%Y-%m-%dT%H:%M:%SZ"
    return dt.strftime(fmt)


def is_future(iso: str, now: datetime) -> bool:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) > now + FUTURE_SLACK


# --------------------------------------------------------------------------- splitting
def _pack(segments: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for seg in segments:
        if out and len(out[-1]) + len(seg) <= limit:
            out[-1] += seg
        else:
            out.append(seg)
    return out


def split_text(text: str, limit: int = ITEM_BODY_MAX, min_level: int = 1) -> list[str]:
    """Split at the coarsest heading level that helps, keep sections whole, hard-cut last."""
    if len(text) <= limit:
        return [text]
    hs = headings(text)
    for lvl in range(min_level, 7):
        cuts = [off for off, level, _ in hs if level <= lvl and off > 0]
        if not cuts:
            continue
        bounds = [0, *cuts, len(text)]
        segs: list[str] = []
        for a, b in zip(bounds, bounds[1:], strict=False):
            seg = text[a:b]
            segs.extend(split_text(seg, limit, lvl + 1) if len(seg) > limit else [seg])
        return _pack(segs, limit)
    paras = re.split(r"(?<=\n\n)", text)
    segs = []
    for p in paras:
        while len(p) > limit:
            cut = p.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            segs.append(p[:cut])
            p = p[cut:]
        if p:
            segs.append(p)
    return _pack(segs, limit)


@dataclass(slots=True)
class Section:
    anchor: str | None  # None: the file's main item (bare path)
    body: str
    lead: str | None = None  # the section's own title
    date: str | None = None  # explicit evidence (canonical ISO) or None
    evidence: str = "none"
    kind: str | None = None  # a structural kind override (decision row → fact, log entry → episode)


def _unique(anchor: str, seen: dict[str, int]) -> str:
    seen[anchor] = seen.get(anchor, 0) + 1
    return anchor if seen[anchor] == 1 else f"{anchor}-{seen[anchor]}"


def decision_sections(text: str, tz: tzinfo | None = None) -> list[Section] | None:
    """≥ 2 decision-log rows → preamble + one section per row (continuation lines attach to it)."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if DECISION_ROW_RE.match(ln)]
    if len(starts) < 2:
        return None
    out: list[Section] = []
    pre = "".join(lines[: starts[0]])
    if pre.strip():
        out.append(Section(None, pre))
    seen: dict[str, int] = {}
    for n, i in enumerate(starts):
        j = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = "".join(lines[i:j])
        m = DECISION_ROW_RE.match(lines[i])
        assert m is not None
        cells = [c.strip() for c in lines[i].split(" | ")]
        status = cells[2] if len(cells) > 2 else ""
        decision = re.sub(r"[*`]", "", cells[3]) if len(cells) > 3 else ""
        lead = clip(f"{m.group(1)} · {status}: {decision}".strip(), 150)
        out.append(
            Section(
                _unique(m.group(1), seen),
                body,
                lead=lead,
                date=evidence_instant(m.group(2), tz),
                evidence="decision-row",
                kind="fact",
            )
        )
    return out


def dated_sections(text: str, tz: tzinfo | None = None) -> tuple[list[Section] | None, str | None]:
    """Dated headings. Returns ``(sections, file_date)``: a single dated heading that is the file's
    first heading dates the whole file; otherwise the dated headings of the shallowest level that
    has one split the file into preamble + one entry per dated heading."""
    hs = headings(text)
    dated = [(off, lvl, t, DATE_RE.search(t)) for off, lvl, t in hs]
    dated = [(off, lvl, t, m.group(1)) for off, lvl, t, m in dated if m]
    if not dated:
        return None, None
    if len(dated) == 1 and hs and dated[0][0] == hs[0][0]:
        return None, evidence_instant(dated[0][3], tz)
    level = min(lvl for _o, lvl, _t, _d in dated)
    marks = [(off, t, d) for off, lvl, t, d in dated if lvl == level]
    stops = [off for off, lvl, _t in hs if lvl < level]
    out: list[Section] = []
    if marks[0][0] > 0 and text[: marks[0][0]].strip():
        out.append(Section(None, text[: marks[0][0]]))
    seen: dict[str, int] = {}
    for n, (off, title, date) in enumerate(marks):
        nxt = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        stop = next((s for s in stops if off < s < nxt), nxt)  # a shallower heading ends the entry
        # the anchor is the heading's slug (it contains the date); same date + title → -2, -3
        entry = Section(
            _unique(slug(title, 48), seen),
            text[off:stop],
            lead=title,
            date=evidence_instant(date, tz),
            evidence="dated-heading",
            kind="episode",
        )
        out.append(entry)
        tail = text[stop:nxt]
        if stop < nxt and tail.strip():  # undated text after the entry keeps its own item
            out.append(Section(_unique(f"after-{slug(title, 40)}", seen), tail))
    return out, None


def size_split(sec: Section) -> list[Section]:
    """Sections above the item limit → heading-split parts; part 0 keeps the section's anchor."""
    parts = split_text(sec.body)
    if len(parts) == 1:
        return [sec]
    out: list[Section] = []
    seen: dict[str, int] = {}
    for n, part in enumerate(parts):
        if n == 0:
            anchor = sec.anchor
        else:
            first = next((t for _o, _l, t in headings(part)), None)
            base = slug(first) if first else f"part-{n + 1}"
            anchor = _unique(f"{sec.anchor}~{base}" if sec.anchor else base, seen)
        out.append(Section(anchor, part, lead=sec.lead, date=sec.date, evidence=sec.evidence, kind=sec.kind))
    return out


# --------------------------------------------------------------------------- describes
PATH_TOKEN_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,8})(?::\d+(?:-\d+)?)?(?![\w/])")


def find_describes(body: str, repo: Path | None, self_path: str | None = None) -> list[str]:
    """Repo-relative files mentioned in the body that exist in ``repo`` (≤ 16, first-seen order)."""
    if repo is None:
        return []
    out: list[str] = []
    for m in PATH_TOKEN_RE.finditer(body):
        p = m.group(1).lstrip("./")
        if p in out or p == self_path or len(p) > 256 or ".." in p.split("/"):
            continue
        if (repo / p).is_file():
            out.append(p)
            if len(out) >= DESCRIBES_MAX:
                break
    return out


# --------------------------------------------------------------------------- provenance
def iso_mtime(path: Path) -> str:
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def git_toplevel(path: Path) -> Path | None:
    d = path if path.is_dir() else path.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


class GitInfo:
    """Last commit (and its date) of each clean tracked file: provenance only (Sol #6)."""

    def __init__(self, repo: Path | None) -> None:
        self.repo = repo
        self._dirty: set[str] | None = None

    def _git(self, *args: str) -> str | None:
        if self.repo is None:
            return None
        try:
            out = subprocess.run(
                ["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    def dirty(self) -> set[str]:
        if self._dirty is None:
            listed = self._git("status", "--porcelain", "-z", "--untracked-files=all") or ""
            self._dirty = {rec[3:] for rec in listed.split("\0") if len(rec) > 3}
        return self._dirty

    def commit_of(self, file: Path) -> tuple[str | None, str | None]:
        if self.repo is None:
            return None, None
        try:
            rel = file.resolve().relative_to(self.repo.resolve()).as_posix()
        except ValueError:
            return None, None
        if rel in self.dirty():
            return None, None
        out = self._git("log", "-1", "--format=%H%x1f%aI", "--", rel)
        if not out or "\x1f" not in out:
            return None, None
        sha, date = out.strip().split("\x1f", 1)
        return sha, evidence_instant(date)  # %aI always carries its offset


def walk_files(root: Path, suffixes: tuple[str, ...], *, recursive: bool = True) -> list[Path]:
    """Regular files under ``root`` with one of ``suffixes``; junk and hidden dirs pruned; sorted."""
    if root.is_file():
        return [root]
    out: list[Path] = []
    if not recursive:
        return sorted(
            p for p in root.iterdir() if p.is_file() and not p.is_symlink() and p.suffix in suffixes
        )
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in JUNK_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix in suffixes and p.is_file() and not p.is_symlink():
                out.append(p)
    return sorted(out)


__all__ = [
    "DESCRIBES_MAX",
    "ITEM_BODY_MAX",
    "GitInfo",
    "ImportRecord",
    "ParseResult",
    "Reject",
    "Section",
    "Skip",
    "decision_sections",
    "dated_sections",
    "dedupe_name",
    "derive_title",
    "evidence_instant",
    "find_describes",
    "git_toplevel",
    "headings",
    "is_future",
    "iso_mtime",
    "parse_frontmatter",
    "read_text",
    "secret_hit",
    "sha256_text",
    "size_split",
    "slug",
    "split_text",
    "stub_targets",
    "walk_files",
]
