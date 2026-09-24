#!/usr/bin/env python3
"""Mechanical Phase-1 corpus importer for HLMemo (no LLM: no rewriting, no summarising).

Walks a project checkout, selects markdown memory/doc files by glob, optionally adds the git history as
one item per calendar day, and writes everything to an HLMemo server through `memory.write` over MCP
streamable HTTP (PHASE0-SPEC §3). Standard library only, so it runs under any Python >= 3.12.

Mapping (deterministic):
  * source type  = serena (.serena/memories/), <label> (--extra-root label=DIR), research (top-level dir
                   starting with "research"), git (--git-log), else doc
  * kind         = serena: lesson (lesson/footgun/feedback/rule in the file name), episode
                   (incident/outage), else fact; extra roots: lesson for `type: feedback` front matter,
                   else fact; doc/research: doc_chunk; git days: episode
  * title        = relative path (+ " § <heading>" when a file is split), ≤ 200 chars
  * tags         = [source type, top-level dir]
  * valid_from   = last commit author date of the file (`git log -1 --format=%aI`); mtime for untracked,
                   ignored or locally modified files and for extra roots; git days: that day 00:00
                   (--git-dir/--git-rev: --root is an export of that commit; dates and git days stop there)
  * files longer than --max-item-chars are split at markdown headings (whole sections where possible)

Temporal rule (--temporal-rule, PHASE2-4-ROADMAP W1.5 / Sol #6):
  * eval (default): valid_from = commit date / mtime as above. Acceptable for eval corpora only; it
    keeps the D-054/D-057 baselines reproducible.
  * prod: the production rule of `hlm import`: recorded_at is server time, the commit date / mtime
    become provenance only (`source.commit_date` / `source.mtime`, with `source.sha256`), and
    valid_from comes only from explicit evidence in the text — frontmatter `valid_from`/`date`,
    the piece's own dated first heading, or the piece's single dated decision row
    (`D-047 | 2026-09-23 |`); otherwise it is omitted (the server's import time). A date without a
    time is read in the machine's local zone; evidence more than 5 minutes in the future rejects
    the item (excluded as `future-evidence`). Git-day items keep their day (a dated heading).

Idempotency: request_id = uuid5(project + sorted (key, content sha256)) of the batch, so re-sending the
same batch replays. The manifest (JSONL) remembers what was written: unchanged items are skipped,
changed items are written as revisions (logical_id + expected_version_id).

Safety: files under excluded globs, binaries, oversized files and files matching a secret pattern are
never read into a payload. With --gitleaks (default: redact) every payload is dumped to a directory and
scanned with `gitleaks dir`; hits are redacted (or dropped) before anything is sent.

Credentials: the device token comes from $HLM_DEVICE_TOKEN, else `<config-dir>/credentials.toml`
(written by `hlm device register` when no keyring backend is used). It is never printed or written.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ITEM_BODY_MAX = 64000  # PHASE0-SPEC §3 Item.body maxLength
ITEM_TITLE_MAX = 200  # Item.title maxLength
BATCH_ITEMS_MAX = 50  # memory.write items maxItems
NAMESPACE = uuid.UUID("8f1d7f2e-3c55-4c1e-9a53-6b1f0c1e2a77")
CLIENT = "hlm-import-corpus/1"

DEFAULT_INCLUDE = ["*.md", ".serena/memories/*.md", "docs/**/*.md"]
DEFAULT_EXCLUDE = [
    "**/node_modules/**",
    "out/**",
    "storage/**",
    ".git/**",
    "**/.venv/**",
    "**/__pycache__/**",
    "**/dist/**",
    "**/build/**",
    "**/vendor/**",
    "**/.env*",
    "**/config.toml",
    "**/*.key",
    "**/*.pem",
    "**/*.p12",
    "**/secrets/**",
    "**/credentials*",
]
RETRY_STATUS = {408, 429, 502, 503, 504}

# Strong secret shapes: a file containing one is excluded outright (never enters a payload).
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "sk-api-key": re.compile(r"\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{24,}"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "hlm-token": re.compile(r"\bhlm_[A-Za-z0-9_-]{20,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # key = value with a literal, high-entropy-looking value (placeholders like <X>, ${X}, $X skipped)
    "assigned-secret": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key)"
        r"\b[\"']?\s*[:=]\s*[\"']?(?![<$\{*]|your|changeme|example|xxx)"
        r"(?=[^\s\"'`]*\d)(?=[^\s\"'`]*[A-Za-z])[A-Za-z0-9+/=_\-.!@#%^&]{16,}"
    ),
    "dsn-with-password": re.compile(r"\b[a-z][a-z0-9+]*://[^\s:/@<>{}$]+:(?![<$\{*])[^\s@<>{}$]{6,}@"),
}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


# --------------------------------------------------------------------------- selection


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass
class Selection:
    files: list[str] = field(default_factory=list)
    excluded: dict[str, list[str]] = field(default_factory=dict)
    pruned_dirs: list[str] = field(default_factory=list)

    def exclude(self, reason: str, rel: str) -> None:
        self.excluded.setdefault(reason, []).append(rel)


def select_files(root: Path, include: list[str], exclude: list[str], max_file_bytes: int) -> Selection:
    inc = [glob_to_regex(p) for p in include]
    exc = [glob_to_regex(p) for p in exclude]
    sel = Selection()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep = []
        for d in sorted(dirnames):
            probe = f"{rel_dir}/{d}/__probe__" if rel_dir else f"{d}/__probe__"
            if any(r.match(probe) for r in exc) or os.path.islink(os.path.join(dirpath, d)):
                sel.pruned_dirs.append(probe[: -len("/__probe__")])
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if not any(r.match(rel) for r in inc):
                continue
            path = root / rel
            if any(r.match(rel) for r in exc):
                sel.exclude("exclude-glob", rel)
            elif path.is_symlink() or not path.is_file():
                sel.exclude("not-regular-file", rel)
            elif path.stat().st_size > max_file_bytes:
                sel.exclude("too-large(generated?)", rel)
            else:
                sel.files.append(rel)
    return sel


def read_text(path: Path) -> tuple[str | None, str | None]:
    """(text, None) or (None, reason)."""
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        return None, "binary"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "binary"
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
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


# --------------------------------------------------------------------------- splitting


def headings(text: str) -> list[tuple[int, int, str]]:
    """(char offset, level, heading text) for ATX headings outside code fences."""
    out, pos, fence = [], 0, None
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


def _pack(segments: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for seg in segments:
        if out and len(out[-1]) + len(seg) <= limit:
            out[-1] += seg
        else:
            out.append(seg)
    return out


def split_text(text: str, limit: int, min_level: int = 1) -> list[str]:
    """Split at the coarsest heading level that helps; keep sections whole; hard-cut only as a last resort."""
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


def first_heading(piece: str) -> str | None:
    hs = headings(piece)
    return hs[0][2] if hs else None


# --------------------------------------------------------------------------- items


@dataclass
class Item:
    key: str  # stable logical key: path#ordinal
    path: str
    section: str | None
    title: str
    kind: str
    body: str
    tags: list[str]
    valid_from: str | None
    source: str
    provenance: str | None = None  # prod rule: the commit date / mtime, provenance only
    provenance_kind: str | None = None  # "commit_date" | "mtime"

    @property
    def sha(self) -> str:
        return hashlib.sha256(
            json.dumps([self.kind, self.title, self.body, self.tags, self.valid_from]).encode()
        ).hexdigest()


FM_DATE_RE = re.compile(r"^(?:valid_from|date):\s*[\"']?(\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+\-Z]+)?)", re.M)
# Same dated-record heading forms as src/hlmemo/importers/common.DATED_HEADING_RE (Sol 42 #3):
# the date first, or `SESSION <date>`; a date elsewhere in a heading is not evidence.
DATED_HEADING_RE = re.compile(r"^(?:session\s+)?(\d{4}-\d{2}-\d{2})(?=$|\s|[—–:|,.)-])", re.IGNORECASE)
DECISION_ROW_RE = re.compile(r"^D-\d{3,4} \| (\d{4}-\d{2}-\d{2}) \| ", re.M)
FUTURE_SLACK_S = 300


def evidence_iso(raw: str) -> str | None:
    """Explicit evidence → UTC ISO; a date without a time is read in the local zone."""
    s = raw.strip().replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T00:00:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # local zone of the writer's machine
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def prod_valid_from(piece: str, file_text: str) -> tuple[str | None, str]:
    """(valid_from, evidence) under the production temporal rule (never a commit date or mtime)."""
    fm = re.match(r"^---\n(.*?)\n---\n", file_text, re.S)
    if fm:
        m = FM_DATE_RE.search(fm.group(1))
        if m and evidence_iso(m.group(1)):
            return evidence_iso(m.group(1)), "frontmatter"
    hs = headings(piece)
    dated = [t for _o, _l, t in hs if DATED_HEADING_RE.match(t)]
    first = DATED_HEADING_RE.match(hs[0][2]) if hs else None
    if first and len(dated) == 1 and evidence_iso(first.group(1)):
        return evidence_iso(first.group(1)), "dated-heading"
    rows = DECISION_ROW_RE.findall(piece)
    if len(rows) == 1:
        return evidence_iso(rows[0]), "decision-row"
    return None, "none"


def apply_prod_rule(items: list[Item], file_text: str, origin: str) -> list[tuple[Item, str]]:
    """Move the eval date into provenance and set valid_from from evidence; returns rejects."""
    rejected: list[tuple[Item, str]] = []
    now = datetime.now(UTC).timestamp()
    for it in items:
        it.provenance, it.provenance_kind = it.valid_from, ("commit_date" if origin == "commit" else "mtime")
        it.valid_from, _why = prod_valid_from(it.body, file_text)
        if it.valid_from and datetime.fromisoformat(it.valid_from.replace("Z", "+00:00")).timestamp() > (
            now + FUTURE_SLACK_S
        ):
            rejected.append((it, it.valid_from))
    return rejected


def clip_title(t: str) -> str:
    return t if len(t) <= ITEM_TITLE_MAX else t[: ITEM_TITLE_MAX - 1] + "…"


def source_of(rel: str) -> str:
    if rel.startswith(".serena/memories/"):
        return "serena"
    top = rel.split("/", 1)[0] if "/" in rel else ""
    return "research" if top.lower().startswith("research") else "doc"


def kind_for(source: str, rel: str, text: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    if source == "serena":
        if re.search(r"lesson|footgun|feedback|(^|_)rule_", name):
            return "lesson"
        if re.search(r"incident|outage", name):
            return "episode"
        return "fact"
    if source in ("doc", "research"):
        return "doc_chunk"
    fm = re.match(r"^---\n(.*?)\n---", text, re.S)
    if fm and re.search(r"^\s*type:\s*feedback\s*$", fm.group(1), re.M):
        return "lesson"
    return "fact"


def file_items(rel: str, text: str, source: str, top: str, valid_from: str, limit: int) -> list[Item]:
    kind = kind_for(source, rel, text)
    pieces = split_text(text, limit)
    items, seen = [], {}
    for i, piece in enumerate(pieces):
        section = None
        title = rel
        if len(pieces) > 1:
            section = first_heading(piece) or f"part {i + 1}"
            seen[section] = seen.get(section, 0) + 1
            if seen[section] > 1:
                section = f"{section} ({seen[section]})"
            title = f"{rel} § {section}"
        items.append(
            Item(
                f"{rel}#{i}", rel, section, clip_title(title), kind, piece, [source, top], valid_from, source
            )
        )
    return items


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def is_git_repo(root: Path) -> bool:
    try:
        return git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def file_dates(
    root: Path, files: list[str], repo: Path | None = None, rev: str | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """{rel: iso date}, {rel: 'commit' | 'mtime(modified)' | 'mtime(untracked)'}

    With `rev`, --root is taken to be an exact export of `repo` at `rev` (e.g. `git archive`): tracked =
    files in that tree, nothing counts as modified, and dates are the last commit up to `rev`.
    """
    dates, origin = {}, {}
    tracked: set[str] = set()
    modified: set[str] = set()
    repo = repo or root
    if is_git_repo(repo):
        if rev:
            tracked = set(git(repo, "ls-tree", "-r", "--name-only", "-z", rev).split("\0"))
        else:
            tracked = set(git(repo, "ls-files", "-z").split("\0"))
            modified = set(git(repo, "diff", "--name-only", "-z", "HEAD").split("\0"))
    for rel in files:
        if rel in tracked and rel not in modified:
            d = git(repo, "log", "-1", "--format=%aI", *([rev] if rev else []), "--", rel).strip()
            if d:
                dates[rel], origin[rel] = d, "commit"
                continue
        dates[rel] = mtime_iso(root / rel)
        origin[rel] = "mtime(modified)" if rel in tracked else "mtime(untracked)"
    return dates, origin


def git_day_items(root: Path, limit: int, rev: str | None = None) -> list[Item]:
    log = git(root, "log", "--reverse", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e", *([rev] if rev else []))
    days: dict[str, list[tuple[str, str, str, str]]] = {}
    for rec in log.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, date, subject, body = (rec.split("\x1f") + ["", "", "", ""])[:4]
        days.setdefault(date[:10], []).append((sha, date, subject, body.strip()))
    items = []
    for day in sorted(days):
        commits = days[day]
        lines = [f"# git log {day} ({len(commits)} commits)\n"]
        for sha, date, subject, body in commits:
            lines.append(f"\n## {date[11:16]} {sha[:10]} {subject}\n")
            if body:
                lines.append(f"\n{body}\n")
        text = "".join(lines)
        offset = commits[0][1][19:] or "Z"
        valid_from = f"{day}T00:00:00{offset}"
        rel = f"git:{day}"
        pieces = split_text(text, limit)
        for i, piece in enumerate(pieces):
            section = None if len(pieces) == 1 else f"part {i + 1}"
            title = f"git log {day}" + (f" § {section}" if section else "")
            items.append(
                Item(
                    f"{rel}#{i}", rel, section, title, "episode", piece, ["git", "git-log"], valid_from, "git"
                )
            )
    return items


# --------------------------------------------------------------------------- gitleaks


def gitleaks_scan(items: list[Item], dump_dir: Path) -> list[dict[str, Any]]:
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
    dump_dir.mkdir(parents=True)
    for n, it in enumerate(items):
        (dump_dir / f"{n:05d}.txt").write_text(it.body, encoding="utf-8")
    fd, report = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run(
            [
                "gitleaks",
                "dir",
                str(dump_dir),
                "--no-banner",
                "--exit-code",
                "0",
                "--log-level",
                "error",
                "--report-format",
                "json",
                "--report-path",
                report,
            ],
            check=True,
            capture_output=True,
        )
        findings = json.loads(Path(report).read_text() or "[]")
    finally:
        os.unlink(report)  # holds raw secrets
    for f in findings:
        f["index"] = int(Path(f["File"]).stem)
    return findings


def apply_gitleaks(items: list[Item], mode: str, dump_dir: Path, log: dict[str, Any]) -> list[Item]:
    findings = gitleaks_scan(items, dump_dir)
    log["gitleaks_findings_initial"] = [
        {"title": items[f["index"]].title, "rule": f.get("RuleID"), "line": f.get("StartLine")}
        for f in findings
    ]
    if findings:
        if mode == "drop":
            bad = {f["index"] for f in findings}
            items = [it for n, it in enumerate(items) if n not in bad]
        else:
            for f in findings:
                it = items[f["index"]]
                secret = f.get("Secret") or f.get("Match") or ""
                if secret:
                    it.body = it.body.replace(secret, f"[REDACTED:{f.get('RuleID', 'secret')}]")
        findings = gitleaks_scan(items, dump_dir)  # re-dump exactly what will be sent
    log["gitleaks_findings_final"] = len(findings)
    if findings:
        raise SystemExit(f"gitleaks still reports {len(findings)} finding(s) after {mode}; aborting")
    return items


# --------------------------------------------------------------------------- MCP client


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool, status: int | None, details: Any = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retryable, self.status, self.details = (
            code,
            message,
            retryable,
            status,
            details,
        )


class Mcp:
    """Minimal MCP streamable-HTTP client (JSON-RPC over POST, JSON or SSE answers).

    ONE persistent HTTP(S) connection (TLS keep-alive, one SSL context) and ONE ``initialize`` per
    client: the production server is stateless (no ``Mcp-Session-Id``), and the e2e 2026-09-24 run
    found the old client opening a new TLS connection and re-initializing on every call (3 round
    trips, ~950 ms per call instead of ~115 ms). A keep-alive connection the server closed while
    idle is reopened once, transparently; ``connections`` counts the connections opened."""

    def __init__(self, server: str, token: str, timeout: float) -> None:
        base = server.rstrip("/")
        self.url = base if base.endswith("/mcp") else base + "/mcp"
        self.token, self.timeout = token, timeout
        self.session: str | None = None  # Mcp-Session-Id, when the server issues one
        self.initialized = False
        self.protocol = "2025-03-26"
        self.seq = 0
        self.connections = 0
        u = urllib.parse.urlsplit(self.url)
        self._https = u.scheme == "https"
        self._host = u.hostname or "localhost"
        self._port = u.port
        self._path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self._ssl = ssl.create_default_context() if self._https else None
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Mcp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            if self._https:
                self._conn = http.client.HTTPSConnection(
                    self._host, self._port, timeout=self.timeout, context=self._ssl
                )
            else:
                self._conn = http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)
            self.connections += 1
        return self._conn

    def _exchange(self, data: bytes, headers: dict[str, str]) -> tuple[int, Any, bytes]:
        """One request on the kept-alive connection: ``(status, headers, body)``."""
        for attempt in (1, 2):
            conn = self._connection()
            reused = conn.sock is not None
            try:
                conn.request("POST", self._path, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError) as exc:
                self.close()
                if reused and attempt == 1:  # the server closed the idle keep-alive connection
                    continue
                raise ToolError("E_UNAVAILABLE", f"transport: {exc}", True, None) from None
            except (OSError, http.client.HTTPException) as exc:  # timeouts, refused, TLS, protocol
                self.close()
                raise ToolError("E_UNAVAILABLE", f"transport: {exc}", True, None) from None
            if resp.will_close:
                self.close()
            return resp.status, resp.headers, raw
        raise AssertionError("unreachable")

    def _post(self, body: dict[str, Any]) -> Any:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": self.protocol,
            "User-Agent": CLIENT,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        status, resp_headers, payload = self._exchange(json.dumps(body).encode(), headers)
        raw = payload.decode(errors="replace")
        if status >= 400:
            try:
                env = json.loads(raw)
            except ValueError:
                env = {}
            if not isinstance(env, dict):
                env = {}
            err = env.get("error") if isinstance(env.get("error"), dict) else env
            raise ToolError(
                str(err.get("code", f"HTTP_{status}")),
                str(err.get("message", raw[:300])),
                status in RETRY_STATUS or bool(err.get("retryable")),
                status,
                err.get("details"),
            )
        self.session = resp_headers.get("Mcp-Session-Id", self.session)
        ctype = resp_headers.get("Content-Type", "")
        if not raw:
            return None
        if "text/event-stream" in ctype:
            for event in raw.split("\n\n"):
                data = "\n".join(ln[5:].lstrip() for ln in event.splitlines() if ln.startswith("data:"))
                if data:
                    parsed = json.loads(data)
                    if "result" in parsed or "error" in parsed:
                        return parsed
            raise ToolError("E_UNAVAILABLE", "SSE answer without JSON-RPC result", True, None)
        return json.loads(raw)

    def rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.seq += 1
            body["id"] = self.seq
        ans = self._post(body)
        if notify:
            return None
        if not ans or "error" in ans:
            e = (ans or {}).get("error") or {}
            raise ToolError("E_RPC", f"{method}: {e.get('message', 'no answer')}", False, None, e)
        return ans["result"]

    def initialize(self) -> None:
        self.session = None
        self.initialized = False
        init = self.rpc(
            "initialize",
            {
                "protocolVersion": self.protocol,
                "capabilities": {},
                "clientInfo": {"name": CLIENT, "version": "1"},
            },
        )
        self.protocol = init.get("protocolVersion", self.protocol)
        self.rpc("notifications/initialized", notify=True)
        self.initialized = True

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.initialized:
            self.initialize()
        res = self.rpc("tools/call", {"name": tool, "arguments": args})
        text = next((c.get("text") for c in res.get("content", []) if c.get("type") == "text"), "")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if res.get("isError"):
            if isinstance(data, dict) and "code" in data:
                raise ToolError(
                    data["code"],
                    data.get("message", ""),
                    bool(data.get("retryable")),
                    200,
                    data.get("details"),
                )
            raise ToolError("E_UNAVAILABLE", text[:300] or "tool error", True, 200)
        if not isinstance(data, dict):
            raise ToolError("E_UNAVAILABLE", f"non-JSON tool result: {text[:120]!r}", True, 200)
        return data

    def call_retry(
        self, tool: str, args: dict[str, Any], errors: list[dict[str, Any]], ctx: str, attempts: int = 8
    ) -> dict[str, Any]:
        delay = 1.0
        for n in range(1, attempts + 1):
            try:
                return self.call(tool, args)
            except ToolError as exc:
                errors.append(
                    {
                        "ctx": ctx,
                        "attempt": n,
                        "http": exc.status,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "retryable": exc.retryable,
                    }
                )
                print(
                    f"  ! {ctx} attempt {n}: http={exc.status} {exc.code} {exc.message[:160]}",
                    file=sys.stderr,
                )
                if exc.status == 404 or exc.code == "E_RPC":
                    self.session, self.initialized = None, False  # session reset: re-initialize
                elif not exc.retryable or n == attempts:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise AssertionError("unreachable")


# --------------------------------------------------------------------------- credentials


def load_token(config_dir: Path | None, server: str, device: str | None) -> str:
    tok = os.environ.get("HLM_DEVICE_TOKEN")
    if tok:
        return tok
    if config_dir is None:
        raise SystemExit("no token: set HLM_DEVICE_TOKEN or pass --config-dir")
    cred = config_dir / "credentials.toml"
    if not cred.is_file():
        raise SystemExit(
            f"no token: {cred} not found (register with keyring disabled, or set HLM_DEVICE_TOKEN)"
        )
    tokens = tomllib.loads(cred.read_text()).get("tokens", {})
    p = urllib.parse.urlsplit(server)
    base = f"{p.scheme}://{p.netloc}"
    if device:
        key = f"{device}@{base}"
        if key not in tokens:
            raise SystemExit(f"no token for {key} in {cred}")
        return tokens[key]
    matches = [v for k, v in tokens.items() if k.endswith("@" + base)]
    if len(matches) != 1:
        raise SystemExit(f"{len(matches)} tokens for {base} in {cred}; pass --device")
    return matches[0]


# --------------------------------------------------------------------------- main


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["key"]] = rec
    return out


def save_manifest(path: Path, recs: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs.values()))
    tmp.replace(path)


def batches(items: list[Item], max_items: int, max_chars: int) -> list[list[Item]]:
    out: list[list[Item]] = []
    cur: list[Item] = []
    size = 0
    for it in items:
        if cur and (len(cur) >= max_items or size + len(it.body) > max_chars):
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it.body)
    if cur:
        out.append(cur)
    return out


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path, help="project checkout to import (read-only)")
    ap.add_argument("--server", default="http://127.0.0.1:8765", help="HLMemo origin or /mcp URL")
    ap.add_argument("--project", required=True, help="target project slug")
    ap.add_argument("--config-dir", type=Path, help="hlm config dir holding credentials.toml")
    ap.add_argument("--device", help="device name (credentials key <device>@<origin>)")
    ap.add_argument(
        "--manifest", required=True, type=Path, help="JSONL manifest (read for idempotency, rewritten)"
    )
    ap.add_argument(
        "--include", nargs="+", default=None, help=f"globs relative to --root (default {DEFAULT_INCLUDE})"
    )
    ap.add_argument(
        "--exclude", nargs="+", default=[], help="extra exclude globs (added to the built-in list)"
    )
    ap.add_argument(
        "--extra-root",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="also import DIR/*.md with source type LABEL (e.g. an agent auto-memory dir)",
    )
    ap.add_argument(
        "--git-log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="import the git history as one item per calendar day",
    )
    ap.add_argument(
        "--git-dir",
        type=Path,
        help="git repository for file dates and --git-log when --root is not a checkout (e.g. an export)",
    )
    ap.add_argument(
        "--git-rev",
        help="take file dates and the git log as of this commit (--root must be an export of it)",
    )
    ap.add_argument("--max-item-chars", type=int, default=ITEM_BODY_MAX)
    ap.add_argument("--max-file-bytes", type=int, default=2_000_000)
    ap.add_argument("--batch-items", type=int, default=BATCH_ITEMS_MAX)
    ap.add_argument("--batch-chars", type=int, default=400_000, help="soft cap on body chars per request")
    ap.add_argument("--token-budget", type=int, default=8000, help="ack budget for memory.write")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--gitleaks", choices=["redact", "drop", "off"], default="redact")
    ap.add_argument(
        "--dump-dir", type=Path, help="where payloads are dumped for gitleaks (default: temp dir)"
    )
    ap.add_argument("--dry-run", action="store_true", help="select, split, scan and report; send nothing")
    ap.add_argument(
        "--temporal-rule",
        choices=["eval", "prod"],
        default="eval",
        help="eval (default): valid_from = commit date/mtime (baseline-compatible); prod: the W1.5 rule",
    )
    ap.add_argument(
        "--wait", action="store_true", help="after writing, poll memory.query until indexing_pending=false"
    )
    ap.add_argument("--wait-timeout", type=float, default=3600.0)
    ap.add_argument("--report", type=Path, help="write a JSON run report here")
    a = ap.parse_args(argv)
    if not 1 <= a.batch_items <= BATCH_ITEMS_MAX:
        ap.error(f"--batch-items must be 1..{BATCH_ITEMS_MAX}")
    if not 1 <= a.max_item_chars <= ITEM_BODY_MAX:
        ap.error(f"--max-item-chars must be 1..{ITEM_BODY_MAX}")
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    root = a.root.resolve()
    report: dict[str, Any] = {
        "temporal_rule": a.temporal_rule,
        "root_name": root.name,
        "project": a.project,
        "git_rev": a.git_rev,
        "errors": [],
    }

    # 1. select + read + map
    sel = select_files(root, a.include or DEFAULT_INCLUDE, DEFAULT_EXCLUDE + a.exclude, a.max_file_bytes)
    git_root = (a.git_dir or root).resolve()
    dates, origin = file_dates(root, sel.files, git_root, a.git_rev)
    items: list[Item] = []
    prod = a.temporal_rule == "prod"
    for rel in sel.files:
        text, why = read_text(root / rel)
        if why:
            sel.exclude(why, rel)
            continue
        src = source_of(rel)
        top = rel.split("/", 1)[0] if "/" in rel else "root"
        its = file_items(rel, text, src, top, dates[rel], a.max_item_chars)
        if prod:
            bad = {id(it) for it, _d in apply_prod_rule(its, text, origin[rel])}
            for it in its:
                if id(it) in bad:
                    sel.exclude("future-evidence", it.key)
            its = [it for it in its if id(it) not in bad]
        items.extend(its)
    for spec in a.extra_root:
        label, _, d = spec.partition("=")
        if not label or not d:
            raise SystemExit(f"--extra-root expects LABEL=DIR, got {spec!r}")
        for p in sorted(Path(d).expanduser().glob("*.md")):
            rel = f"{label}/{p.name}"
            text, why = read_text(p) if p.is_file() else (None, "not-regular-file")
            if why:
                sel.exclude(why, rel)
                continue
            its = file_items(rel, text, label, label, mtime_iso(p), a.max_item_chars)
            if prod:
                bad = {id(it) for it, _d in apply_prod_rule(its, text, "mtime")}
                for it in its:
                    if id(it) in bad:
                        sel.exclude("future-evidence", it.key)
                its = [it for it in its if id(it) not in bad]
            items.extend(its)
            origin[rel] = "mtime(untracked)"
    if a.git_log and is_git_repo(git_root):
        for it in git_day_items(git_root, a.max_item_chars, a.git_rev):
            why = secret_hit(it.body)
            if why:
                sel.exclude(f"secret-pattern:{why}", it.path)
            else:
                items.append(it)

    # 2. gitleaks over the exact payload text
    if a.gitleaks != "off":
        if not shutil.which("gitleaks"):
            raise SystemExit("gitleaks not on PATH (use --gitleaks off to skip explicitly)")
        dump = a.dump_dir or Path(tempfile.mkdtemp(prefix="hlm-import-"))
        items = apply_gitleaks(items, a.gitleaks, dump, report)
        if a.dump_dir is None:
            shutil.rmtree(dump, ignore_errors=True)

    by_source: dict[str, dict[str, int]] = {}
    for it in items:
        s = by_source.setdefault(it.source, {"items": 0, "chars": 0, "files": 0})
        s["items"] += 1
        s["chars"] += len(it.body)
        s["files"] += it.key.endswith("#0")
    report.update(
        selected_files=len(sel.files),
        items=len(items),
        chars=sum(len(it.body) for it in items),
        split_files=sorted({it.path for it in items if it.section and not it.path.startswith("git:")}),
        by_source=by_source,
        by_kind={k: sum(1 for it in items if it.kind == k) for k in sorted({it.kind for it in items})},
        excluded={k: len(v) for k, v in sel.excluded.items()},
        excluded_files=sel.excluded,
        pruned_dirs=sel.pruned_dirs,
        date_origin={k: sum(1 for v in origin.values() if v == k) for k in sorted(set(origin.values()))},
    )

    # 3. idempotency against the manifest
    manifest = load_manifest(a.manifest)
    todo = [it for it in items if manifest.get(it.key, {}).get("sha256") != it.sha]
    report["skipped_unchanged"] = len(items) - len(todo)
    report["revisions"] = sum(1 for it in todo if it.key in manifest)
    groups = batches(todo, a.batch_items, a.batch_chars)
    report["batches"] = len(groups)
    if a.dry_run:
        if a.report:
            a.report.parent.mkdir(parents=True, exist_ok=True)
            a.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in ("excluded_files", "pruned_dirs")},
                indent=1,
                ensure_ascii=False,
            )
        )
        return 0

    # 4. write
    token = load_token(a.config_dir, a.server, a.device)
    mcp = Mcp(a.server, token, a.timeout)
    t0 = time.monotonic()
    chunks = 0
    for bn, group in enumerate(groups, 1):
        keys = "\n".join(sorted(f"{it.key}:{it.sha}" for it in group))
        rid = str(uuid.uuid5(NAMESPACE, f"{a.project}\n{keys}"))
        wire = []
        for it in group:
            w: dict[str, Any] = {"kind": it.kind, "title": it.title, "body": it.body, "tags": it.tags}
            if it.valid_from:
                w["valid_from"] = it.valid_from
            if prod and it.source != "git":  # provenance only (W1.5 Item.source)
                w["source"] = {
                    "system": it.source,
                    "path": it.key,
                    "sha256": hashlib.sha256(it.body.encode()).hexdigest(),
                }
                if it.provenance and it.provenance_kind:
                    w["source"][it.provenance_kind] = it.provenance
            prev = manifest.get(it.key)
            if prev:
                w["logical_id"], w["expected_version_id"] = prev["logical_id"], prev["version_id"]
            wire.append(w)
        args = {
            "project": a.project,
            "request_id": rid,
            "client": CLIENT,
            "items": wire,
            "token_budget": a.token_budget,
        }
        if not prod:  # eval rule: the batch happened at its newest file date (baseline behaviour)
            args["occurred_at"] = max(group, key=lambda it: datetime.fromisoformat(it.valid_from)).valid_from
        ack = mcp.call_retry("memory.write", args, report["errors"], f"batch {bn}/{len(groups)}")
        for v in ack["versions"]:
            it = group[v["index"]]
            chunks += v["chunk_count"]
            manifest[it.key] = {
                "key": it.key,
                "logical_id": v["logical_id"],
                "version_id": v["version_id"],
                "path": it.path,
                "section": it.section,
                "title": it.title,
                "kind": it.kind,
                "source": it.source,
                "chars": len(it.body),
                "chunk_count": v["chunk_count"],
                "occurred_at": it.valid_from,
                "sha256": it.sha,
                "request_id": rid,
            }
        save_manifest(a.manifest, manifest)
        print(
            f"batch {bn}/{len(groups)}: {len(group)} items, replayed={ack['replayed']}, "
            f"{time.monotonic() - t0:.1f}s",
            file=sys.stderr,
        )
    report["write_seconds"] = round(time.monotonic() - t0, 2)
    report["chunks_written"] = chunks

    # 5. wait for embeddings
    if a.wait:
        t1 = time.monotonic()
        polls = 0
        while True:
            polls += 1
            q = mcp.call_retry(
                "memory.query",
                {"project": a.project, "query": "indexing status", "token_budget": 256},
                report["errors"],
                "wait-poll",
            )
            if not q["indexing_pending"]:
                break
            if time.monotonic() - t1 > a.wait_timeout:
                report["wait_timed_out"] = True
                break
            time.sleep(5)
        report["embed_wait_seconds"] = round(time.monotonic() - t1, 2)
        report["wait_polls"] = polls

    out = {k: v for k, v in report.items() if k not in ("excluded_files", "pruned_dirs")}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
