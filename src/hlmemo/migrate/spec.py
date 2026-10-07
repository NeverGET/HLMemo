"""The migration spec (`migration.toml`): one file naming the project, the curated tree and the targets."""

from __future__ import annotations

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
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def seal_path(self) -> Path:
        return self.private_dir / "seal.json"

    def target(self, name: str) -> Target:
        if name not in self.targets:
            raise SpecError(f"the spec has no [{name}] table")
        return self.targets[name]

    def tag_allowed(self, tag: str) -> bool:
        return tag in self.tags or tag in STATUS_TAGS or bool(SCOPE_TAG_RE.match(tag))


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
        [tags]    closed = [...]
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
    known = {"slug", "tz", "estimated_marker", "paths", "sources", "tags", "local", "prod"}
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
        extra={k: v for k, v in raw.items() if k not in known},
    )


__all__ = ["MigrationSpec", "STATUS_TAGS", "Source", "SpecError", "Target", "is_loopback_url", "load_spec"]
