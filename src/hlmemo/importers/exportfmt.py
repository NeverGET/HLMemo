"""The ``hlm export`` Markdown format and its re-import (W1.5, D-021 cold-start fallback).

One file per current item ``<kind>/<slug of title>.md`` plus ``CARD.md`` (the project card) and
``INDEX.md``. Each item file is frontmatter + the exact body::

    ---
    hlm_export: 1
    clue: "v903"            # ids: the only lines that differ between two projects
    logical_id: 17
    version_id: 903
    kind: "fact"
    title: "..."
    origin: "hlmemo/17"     # items without a real provenance: <project>/<logical_id> of the original
    valid_from: "2026-09-23T00:00:00.000000Z"
    valid_to: null
    tags: ["serena","imported"]
    source: {...}           # a real provenance (then no origin); likewise every optional key below
    describes: [...]
    links: [{"rel":"relates_to","target":"fact/other.md"}]
    ...
    ---
    <body, byte for byte>

Values are JSON (valid YAML flow scalars), keys in a fixed order, file names derived from content
(never from ids) and ``INDEX.md`` carries no ids or timestamps, so export → import into a fresh
project → export is byte-identical except the three id lines (gate G-I3).

Identity (Sol 42 #5): ``origin`` names the item an export file came from and never changes. Imported
into another project, an origin item is stored with the source ``{system: "hlm", path: origin}`` —
the persistent, ownership-checked mapping — and exported again with the same ``origin`` line. Only
in the origin project does ``hlm import markdown`` map a file back onto ``logical_id`` = the origin's
id (see ``plan.classify``). The ``logical_id``/``version_id`` lines are informational.
"""

from __future__ import annotations

import json
from typing import Any

from hlmemo.importers.common import ImportRecord, sha256_text, slug

MARKER = "hlm_export"
VERSION = 1
INDEX_MARK = "<!-- hlm-export-index -->"
ID_KEYS = ("clue", "logical_id", "version_id")
KINDS = ("fact", "episode", "lesson", "experience", "project_card", "session_note", "doc_chunk")
ORDER = (
    MARKER,
    "clue",
    "logical_id",
    "version_id",
    "kind",
    "title",
    "origin",
    "valid_from",
    "valid_to",
    "tags",
    "source",
    "describes",
    "links",
    "pinned",
    "stability",
    "importance",
    "device_scope",
    "also_in",
    "status",
)
DEFAULTS: dict[str, Any] = {
    "origin": None,
    "source": None,
    "describes": [],
    "links": [],
    "pinned": False,
    "stability": "volatile",
    "importance": None,
    "device_scope": "all",
    "also_in": [],
    "status": "active",
}


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_export(meta: dict[str, Any]) -> bool:
    return meta.get(MARKER) == VERSION


def is_index(text: str) -> bool:
    return text.startswith(INDEX_MARK)


# --------------------------------------------------------------------------- writing
def file_names(items: list[dict[str, Any]]) -> dict[int, str]:
    """``logical_id → relative file``; content-derived and collision-free in a deterministic order
    that does not depend on ids (kind, title slug, source key, body hash, valid_from)."""

    def sort_key(it: dict[str, Any]) -> tuple[str, ...]:
        src = it.get("source") or {}
        return (
            it["kind"],
            slug(it["title"], 60),
            f"{src.get('system', '')}:{src.get('path', '')}",
            it.get("body_sha256", ""),
            it.get("valid_from") or "",
        )

    out: dict[int, str] = {}
    used: set[str] = set()
    for it in sorted(items, key=sort_key):
        if it["kind"] == "project_card":
            out[it["logical_id"]] = "CARD.md"
            continue
        stem = f"{it['kind']}/{slug(it['title'], 60)}"
        name, n = f"{stem}.md", 1
        while name in used:
            n += 1
            name = f"{stem}-{n}.md"
        used.add(name)
        out[it["logical_id"]] = name
    return out


ORIGIN_SYSTEM = "hlm"


def identity(it: dict[str, Any], project: str) -> tuple[str | None, dict[str, Any] | None]:
    """``(origin, source)`` of an exported item: a real provenance is kept as ``source``; an item
    imported from an export keeps its ``origin``; a native item's origin is itself."""
    src = it.get("source")
    if src is None:
        return f"{project}/{it['logical_id']}", None
    if src.get("system") == ORIGIN_SYSTEM:
        return str(src.get("path")), None
    return None, src


def render_item(it: dict[str, Any], names: dict[int, str], project: str) -> str:
    """One export file. ``it`` is a full-view ``hlm.export`` item with its joined body."""
    origin, source = identity(it, project)
    links: list[dict[str, Any]] = []
    for ln in it.get("links") or []:
        target = names.get(ln["dst_logical_id"])
        link: dict[str, Any] = {"rel": ln["rel"]}
        if target is not None:
            link["target"] = target
        else:  # outside this export (another project or not live): kept by id
            link["target_logical_id"] = ln["dst_logical_id"]
        links.append(link)
    links.sort(key=lambda d: (d["rel"], d.get("target", ""), d.get("target_logical_id", 0)))
    values: dict[str, Any] = {
        MARKER: VERSION,
        "clue": f"v{it['version_id']}",
        "logical_id": it["logical_id"],
        "version_id": it["version_id"],
        "kind": it["kind"],
        "title": it["title"],
        "origin": origin,
        "valid_from": it["valid_from"],
        "valid_to": it["valid_to"],
        "tags": list(it.get("tags") or []),
        "source": source,
        "describes": list(it.get("describes") or []),
        "links": links,
        "pinned": bool(it.get("pinned")),
        "stability": it.get("stability") or "volatile",
        "importance": it.get("importance"),
        "device_scope": it.get("device_scope") or "all",
        "also_in": list(it.get("also_in") or []),
        "status": it.get("status") or "active",
    }
    lines = ["---"]
    for key in ORDER:
        if key in DEFAULTS and values[key] == DEFAULTS[key]:
            continue
        lines.append(f"{key}: {_json(values[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n" + it["body"]


def render_index(items: list[dict[str, Any]], names: dict[int, str]) -> str:
    def cell(s: Any) -> str:
        return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")

    rows = sorted(((names[it["logical_id"]], it) for it in items), key=lambda r: (r[0] != "CARD.md", r[0]))
    out = [
        INDEX_MARK,
        "# HLMemo export",
        "",
        "One file per current item (frontmatter + body); `CARD.md` is the project card. Re-import with",
        "`hlm import markdown <this dir> --project <slug>`.",
        "",
        "| file | kind | title | valid_from | source |",
        "|---|---|---|---|---|",
    ]
    for name, it in rows:
        src = it.get("source") or {}
        # real provenance only: an origin names ids and would differ between two projects (G-I3)
        where = f"{src.get('system')}:{src.get('path')}" if src and src.get("system") != ORIGIN_SYSTEM else ""
        out.append(
            f"| {cell(name)} | {it['kind']} | {cell(it['title'])} | {it['valid_from']} | {cell(where)} |"
        )
    return "\n".join(out) + "\n"


def strip_ids(text: str) -> str:
    """The G-I3 comparison form: an export file without its id lines."""
    return "\n".join(ln for ln in text.split("\n") if not any(ln.startswith(f"{k}: ") for k in ID_KEYS))


# --------------------------------------------------------------------------- reading
def record_from_export(system: str, rel: str, meta: dict[str, Any], body: str) -> ImportRecord | None:
    kind = meta.get("kind")
    title = meta.get("title")
    if kind not in KINDS or not isinstance(title, str) or not title.strip() or not body.strip():
        return None
    tags = meta.get("tags") or []
    describes = meta.get("describes") or []
    if not isinstance(tags, list) or not isinstance(describes, list):
        return None
    export = {k: meta.get(k, DEFAULTS.get(k)) for k in ORDER if k != MARKER}
    export["tags"] = [str(t) for t in tags]
    export["describes"] = [str(d) for d in describes]
    src = export.get("source")
    if src is not None and not (isinstance(src, dict) and {"system", "path", "sha256"} <= set(src)):
        return None
    if isinstance(src, dict) and src.get("system") == ORIGIN_SYSTEM:  # normalise: hlm source = origin
        export["origin"], export["source"] = str(src.get("path")), None
    origin = export.get("origin")
    if export.get("source") is None:
        from hlmemo.importers.plan import split_origin

        if not isinstance(origin, str) or split_origin(origin) is None:
            return None
    elif origin is not None:
        return None  # an item has either a real source or an origin, never both
    if not isinstance(export.get("links"), list):
        return None
    return ImportRecord(
        system=system,
        path=rel,
        file=rel,
        sha256=sha256_text(body),
        title=title,
        body=body,
        kind_guess=kind,
        tags=export["tags"],
        describes=export["describes"],
        evidenced_valid_from=export.get("valid_from"),
        evidence="export",
        export=export,
    )


__all__ = [
    "ID_KEYS",
    "INDEX_MARK",
    "file_names",
    "is_export",
    "is_index",
    "record_from_export",
    "render_index",
    "render_item",
    "strip_ids",
]
