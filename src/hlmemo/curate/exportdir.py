"""Read an ``hlm export`` directory (``importers/exportfmt``) into the heads the curation gate needs.

One file per CURRENT item: frontmatter (``clue``, ``logical_id``, ``version_id``, ``kind``, ``title``,
``valid_from``, ``source``, ``links`` ...) then the body, byte for byte. ``INDEX.md`` is skipped; the
project card (``CARD.md``) is read for its links but is never a link endpoint (a living document is
edited, not linked).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hlmemo.importers.common import parse_frontmatter
from hlmemo.importers.exportfmt import is_index

CARD = "CARD.md"


@dataclass(frozen=True)
class Item:
    logical_id: int
    version_id: int
    kind: str
    title: str
    valid_from: str
    file: str  # path relative to the export root
    body: str
    source: dict[str, Any] | None
    links: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def clue(self) -> str:
        return f"v{self.version_id}"

    @property
    def source_path(self) -> str | None:
        """``source.path`` (anchor included), or None for an item with only an ``origin``."""
        if not isinstance(self.source, dict):
            return None
        p = self.source.get("path")
        return str(p) if p else None


@dataclass
class Export:
    root: Path
    items: dict[int, Item]  # linkable heads by logical_id (the project card excluded)
    card_ids: set[int]
    live_supersedes: set[tuple[int, int]]  # (src, dst) of the live ``supersedes`` links in the export

    def head(self, logical_id: int) -> Item | None:
        return self.items.get(int(logical_id))


def read_item(path: Path, root: Path) -> Item | None:
    text = path.read_text(encoding="utf-8")
    if is_index(text):
        return None
    meta, body = parse_frontmatter(text)
    if "logical_id" not in meta or "version_id" not in meta:
        return None
    return Item(
        logical_id=int(meta["logical_id"]),
        version_id=int(meta["version_id"]),
        kind=str(meta.get("kind") or ""),
        title=str(meta.get("title") or ""),
        valid_from=str(meta.get("valid_from") or ""),
        file=path.relative_to(root).as_posix(),
        body=body,
        source=meta.get("source") if isinstance(meta.get("source"), dict) else None,
        links=tuple(ln for ln in (meta.get("links") or []) if isinstance(ln, dict)),
    )


def load(root: Path) -> Export:
    root = Path(root)
    every: list[Item] = []
    for f in sorted(root.rglob("*.md")):
        it = read_item(f, root)
        if it is not None:
            every.append(it)
    by_file = {it.file: it.logical_id for it in every}
    live: set[tuple[int, int]] = set()
    for it in every:
        for ln in it.links:
            if ln.get("rel") != "supersedes":
                continue
            if "target" in ln and ln["target"] in by_file:
                live.add((it.logical_id, by_file[ln["target"]]))
            elif ln.get("target_logical_id") is not None:
                live.add((it.logical_id, int(ln["target_logical_id"])))
    cards = {it.logical_id for it in every if it.file == CARD or it.kind == "project_card"}
    items = {it.logical_id: it for it in every if it.logical_id not in cards}
    return Export(root=root, items=items, card_ids=cards, live_supersedes=live)


def fingerprint(root: Path) -> str:
    """sha256 over every ``*.md`` (relative path + content hash): workers must not change the export."""
    h = hashlib.sha256()
    root = Path(root)
    for f in sorted(root.rglob("*.md")):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


__all__ = ["CARD", "Export", "Item", "fingerprint", "load", "read_item"]
