"""The migration spec (`migration.toml`): one file naming the project, the curated tree and the targets."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from hlmemo.importers.cli import SOURCES

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
#: tags every curated item may carry besides the spec's closed topic list (status, era, date provenance)
STATUS_TAGS = frozenset({"active", "resolved", "historical", "date-estimated"})
#: a lesson's scope tag: `<stack>@<version>` (R15)
SCOPE_TAG_RE = re.compile(r"^[A-Za-z0-9][\w.+-]*@[\w.+-]+$")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
#: an accepted scan hit: `<rule>:<path>:<line>`
SCAN_ALLOW_RE = re.compile(r"^[a-z0-9][a-z0-9-]*:[^:\s][^\s]*:[1-9][0-9]*$")


class SpecError(ValueError):
    """The spec is missing a field or holds a bad value."""


@dataclass(frozen=True)
class Source:
    importer: str
    path: Path
    section_chars: int = 8000

    @property
    def base(self) -> Path | None:
        """The importer's key base: a markdown dir keys paths relative to itself, a context file to its
        directory."""
        if self.importer == "markdown":
            return self.path if self.path.is_dir() else self.path.parent
        if self.importer == "context":
            return self.path.parent if self.path.is_file() else self.path
        return None


@dataclass(frozen=True)
class Target:
    name: str
    server: str
    device: str

    @property
    def is_loopback(self) -> bool:
        return is_loopback_url(self.server)


def is_loopback_url(url: str) -> bool:
    """True only for an http(s) URL whose real host is the loopback interface and that carries no userinfo:
    `http://127.0.0.1@prod.example/mcp` names the host prod.example and is NOT local (review 113)."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port_ok = parts.port is None or 0 < parts.port < 65536
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and parts.username is None
        and parts.password is None
        and "@" not in parts.netloc
        and host in LOOPBACK_HOSTS
        and port_ok
    )


@dataclass(frozen=True)
class ScanPattern:
    """A project-specific personal-data rule for `tools/migrate/scan.py` (account ids, tax ids, ...)."""

    id: str
    regex: re.Pattern[str]
    mask: str


@dataclass(frozen=True)
class MigrationSpec:
    path: Path
    slug: str
    tz: str
    curated_dir: Path
    private_dir: Path
    sources: tuple[Source, ...]
    targets: dict[str, Target]
    repo: Path | None = None
    tags: frozenset[str] = frozenset()
    estimated_marker: str = "Date estimated"
    #: `[tags].scopes`: when set, the only allowed `<stack>@<version>` scope tags
    scopes: frozenset[str] = frozenset()
    #: `[lint].exclude`: globs (relative to the curated dir) of side files that are not part of the migration;
    #: lint, plan, seal and run all leave them out, so an excluded file is never imported
    exclude: tuple[str, ...] = ()
    #: `[scan].allow`: accepted scan hits, each `<rule>:<path>:<line>` (path relative to the curated dir)
    scan_allow: frozenset[str] = frozenset()
    #: `[scan].allow_values`: literal values accepted wherever they occur (e.g. a public support address)
    scan_allow_values: frozenset[str] = frozenset()
    #: `[[scan.patterns]]`: project-specific personal-data rules
    scan_patterns: tuple[ScanPattern, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def seal_path(self) -> Path:
        return self.private_dir / "seal.json"

    def target(self, name: str) -> Target:
        if name not in self.targets:
            raise SpecError(f"the spec has no [{name}] table")
        return self.targets[name]

    def tag_allowed(self, tag: str) -> bool:
        if tag in self.tags or tag in STATUS_TAGS:
            return True
        return bool(SCOPE_TAG_RE.match(tag)) and (not self.scopes or tag in self.scopes)

    def excluded(self, rel: str) -> bool:
        """True when a curated-relative path matches one of the `[lint].exclude` globs."""
        return any(fnmatch.fnmatchcase(rel, g) for g in self.exclude)


def _path(root: Path, value: Any, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"`{name}` must be a non-empty path string")
    p = Path(value).expanduser()
    return (p if p.is_absolute() else root / p).resolve()


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    t = raw.get(name) or {}
    if not isinstance(t, dict):
        raise SpecError(f"[{name}] must be a table")
    return t


def load_spec(path: Path | str) -> MigrationSpec:
    """Read and validate a spec in the layout of docs/migration/templates/migration.toml. Relative paths
    resolve against the spec file's directory.

        slug, tz, [estimated_marker]
        [paths]   curated, private, repo
        [[sources]] importer, path, section_chars
        [tags]    closed = [...], scopes = [...]                      (scopes optional)
        [lint]    exclude = ["glob", ...]                              (optional)
        [scan]    allow = ["<rule>:<path>:<line>"], allow_values = [...]; [[scan.patterns]] id, regex, mask
                  (all optional)
        [local]   server_url, device
        [prod]    server_url, device
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SpecError(f"spec not found: {p}")
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"spec is not valid TOML: {exc}") from None
    root = p.parent
    slug = raw.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise SpecError("`slug` must match ^[a-z0-9][a-z0-9-]{1,63}$")
    tz = raw.get("tz") or "local"
    if not isinstance(tz, str):
        raise SpecError("`tz` must be an IANA zone name, `UTC` or `local`")
    paths = _table(raw, "paths")
    curated = _path(root, paths.get("curated"), "paths.curated")
    private = _path(root, paths.get("private", "."), "paths.private")
    repo = _path(root, paths["repo"], "paths.repo") if paths.get("repo") else None
    tags = _table(raw, "tags").get("closed") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise SpecError("[tags].closed must be a list of non-empty strings")
    scopes = _table(raw, "tags").get("scopes") or []
    if not isinstance(scopes, list) or not all(isinstance(t, str) and SCOPE_TAG_RE.match(t) for t in scopes):
        raise SpecError("[tags].scopes must be a list of `<stack>@<version>` tags")
    exclude = _table(raw, "lint").get("exclude") or []
    if not isinstance(exclude, list) or not all(isinstance(g, str) and g.strip() for g in exclude):
        raise SpecError("[lint].exclude must be a list of glob strings")
    scan = _table(raw, "scan")
    allow = scan.get("allow") or []
    if not isinstance(allow, list) or not all(isinstance(v, str) and SCAN_ALLOW_RE.match(v) for v in allow):
        raise SpecError('[scan].allow must be a list of "<rule>:<path>:<line>" strings')
    allow_values = scan.get("allow_values") or []
    if not isinstance(allow_values, list) or not all(isinstance(v, str) and v for v in allow_values):
        raise SpecError("[scan].allow_values must be a list of non-empty strings")
    raw_patterns = scan.get("patterns") or []
    if not isinstance(raw_patterns, list):
        raise SpecError("[[scan.patterns]] must be an array of tables")
    patterns: list[ScanPattern] = []
    for i, sp in enumerate(raw_patterns):
        if not isinstance(sp, dict) or not all(isinstance(sp.get(k), str) and sp[k] for k in ("id", "regex")):
            raise SpecError(f"[[scan.patterns]] {i} needs `id` and `regex` strings")
        try:
            rx = re.compile(sp["regex"])
        except re.error as exc:
            raise SpecError(f"[[scan.patterns]] {sp['id']}: bad regex: {exc}") from None
        patterns.append(ScanPattern(sp["id"], rx, str(sp.get("mask") or f"<{sp['id'].upper()}>")))
    marker = raw.get("estimated_marker", "Date estimated")
    if not isinstance(marker, str) or not marker.strip():
        raise SpecError("`estimated_marker` must be a non-empty string")
    raw_sources = raw.get("sources") or [{"importer": "markdown", "path": str(curated)}]
    if not isinstance(raw_sources, list):
        raise SpecError("`sources` must be an array of tables ([[sources]])")
    sources: list[Source] = []
    for i, s in enumerate(raw_sources):
        if not isinstance(s, dict):
            raise SpecError(f"sources[{i}] must be a table")
        imp = s.get("importer")
        if imp not in SOURCES:
            raise SpecError(f"sources[{i}].importer must be one of {SOURCES}")
        chars = s.get("section_chars", 8000)
        if not isinstance(chars, int) or chars < 1000:
            raise SpecError(f"sources[{i}].section_chars must be an integer >= 1000")
        sources.append(Source(imp, _path(root, s.get("path"), f"sources[{i}].path"), chars))
    targets: dict[str, Target] = {}
    for name in ("local", "prod"):
        t = raw.get(name)
        if t is None:
            continue
        if (
            not isinstance(t, dict)
            or not isinstance(t.get("server_url"), str)
            or not isinstance(t.get("device"), str)
        ):
            raise SpecError(f"[{name}] needs `server_url` and `device` strings")
        targets[name] = Target(name, t["server_url"].strip(), t["device"].strip())
    if "local" in targets and not targets["local"].is_loopback:
        raise SpecError("[local].server_url must be a loopback URL (http://127.0.0.1:…)")
    known = {"slug", "tz", "estimated_marker", "paths", "sources", "tags", "lint", "scan", "local", "prod"}
    return MigrationSpec(
        path=p,
        slug=slug,
        tz=tz,
        curated_dir=curated,
        private_dir=private,
        sources=tuple(sources),
        targets=targets,
        repo=repo,
        tags=frozenset(t.strip() for t in tags),
        estimated_marker=marker,
        scopes=frozenset(scopes),
        exclude=tuple(g.strip() for g in exclude),
        scan_allow=frozenset(allow),
        scan_allow_values=frozenset(allow_values),
        scan_patterns=tuple(patterns),
        extra={k: v for k, v in raw.items() if k not in known},
    )


__all__ = [
    "MigrationSpec",
    "STATUS_TAGS",
    "ScanPattern",
    "Source",
    "SpecError",
    "Target",
    "is_loopback_url",
    "load_spec",
]
