"""The network half of ``hlm import`` / ``hlm export``: manifest paging, writes, export files.

Everything goes through one injected ``call(tool, arguments) -> dict`` (an MCP session in the CLI,
an in-process caller in tests), which raises ``ToolCallError`` for tool errors. Writes are one
``memory.write`` per record with ``request_id = uuid5(project ‖ source_key ‖ sha256 ‖
expected_version_id ‖ action)`` (``plan.request_id``), so an interrupted run resumes (a re-sent
step replays) and an A→B→A content cycle never reuses an id. Export-format records with links are
written targets-first; links that close a cycle are added by a second revision. Missing items are
re-mapped or closed (``plan.remap``); a close is a ``close`` revision of the item's own content
whose validity ends now.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.importers import exportfmt
from hlmemo.importers.common import ParseResult
from hlmemo.importers.plan import CLIENT, Entry, Plan, classify, remap, request_id, source_key, split_origin

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
EXPORT_TOOL = "hlm.export"
PAGE_BUDGET = 32000
WRITE_BUDGET = 2000
LOOKUP_BATCH = 200


async def fetch_items(
    call: Call,
    project: str,
    *,
    view: str = "manifest",
    kinds: list[str] | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
    logical_ids: list[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every page of ``hlm.export``; continued bodies (``body_range``) are joined."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    as_of: dict[str, Any] = {}
    while True:
        args: dict[str, Any] = {"project": project, "token_budget": PAGE_BUDGET, "view": view}
        if kinds:
            args["kinds"] = kinds
        if valid_at:
            args["valid_at"] = valid_at
        if known_at:
            args["known_at"] = known_at
        if logical_ids:
            args["logical_ids"] = logical_ids
        if cursor:
            args["cursor"] = cursor
        page = await call(EXPORT_TOOL, args)
        as_of = page.get("as_of", as_of)
        for it in page["items"]:
            prev = items[-1] if items else None
            if (
                view == "full"
                and prev is not None
                and (prev["logical_id"], prev["version_id"]) == (it["logical_id"], it["version_id"])
                and it["body_range"][0] == prev["body_range"][1]
            ):
                prev["body"] += it["body"]
                prev["body_range"] = [prev["body_range"][0], it["body_range"][1]]
            else:
                items.append(dict(it))
        cursor = page.get("next_cursor")
        if not cursor:
            return items, as_of


async def fetch_full(call: Call, project: str, logical_ids: list[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(logical_ids), LOOKUP_BATCH):
        part, _ = await fetch_items(call, project, view="full", logical_ids=logical_ids[i : i + LOOKUP_BATCH])
        out += part
    return out


async def resolve_missing(call: Call, plan: Plan, *, confirm_close: bool = False) -> None:
    """Re-map missing items onto new records, else close them (bodies fetched by id)."""
    if not plan.missing:
        return
    full = await fetch_full(call, plan.project, sorted({it["logical_id"] for it in plan.missing}))
    heads: dict[int, dict[str, Any]] = {}
    for it in full:  # one logical item may have several live segments: the head version
        if heads.get(it["logical_id"], {}).get("version_id", 0) < it["version_id"]:
            heads[it["logical_id"]] = it
    remap(plan, list(heads.values()), confirm_close=confirm_close)


# --------------------------------------------------------------------------- import
def _item(e: Entry, links: list[dict[str, Any]] | None) -> dict[str, Any]:
    rec = e.record
    item: dict[str, Any] = {
        "kind": rec.kind_guess,
        "title": rec.title,
        "body": rec.body,
        "tags": list(rec.tags),
    }
    ex = rec.export
    item["source"] = rec.source()
    if rec.describes:
        item["describes"] = list(rec.describes)
    if ex is not None:
        if ex.get("valid_from"):
            item["valid_from"] = ex["valid_from"]
        if ex.get("valid_to"):
            item["valid_to"] = ex["valid_to"]
        for key, default in (
            ("pinned", False),
            ("stability", "volatile"),
            ("importance", None),
            ("device_scope", "all"),
        ):
            if ex.get(key, default) != default:
                item[key] = ex[key]
        if links:
            item["links"] = links
    elif rec.evidenced_valid_from:
        item["valid_from"] = rec.evidenced_valid_from
    if e.action == "changed" and rec.kind_guess != "project_card":
        item["logical_id"] = e.logical_id
    if e.action == "changed" or rec.kind_guess == "project_card":
        item["expected_version_id"] = e.expected
    return item


def _close_item(project: str, old: dict[str, Any]) -> dict[str, Any]:
    """A ``close`` revision: the item's own content, validity [valid_from, server now)."""
    item: dict[str, Any] = {
        "kind": old["kind"],
        "title": old["title"],
        "body": old["body"],
        "tags": list(old.get("tags") or []),
        "logical_id": old["logical_id"],
        "expected_version_id": old["version_id"],
        "valid_from": old["valid_from"],
        "close": True,
    }
    if old.get("source") is not None:
        item["source"] = old["source"]
    if old.get("describes"):
        item["describes"] = list(old["describes"])
    for key, default in (("pinned", False), ("stability", "volatile"), ("importance", None)):
        if old.get(key, default) != default:
            item[key] = old[key]
    if old.get("device_scope", "all") != "all":
        item["device_scope"] = old["device_scope"]
    if old.get("also_in"):
        item["project_ids"] = [project, *old["also_in"]]
    return item


def _export_order(entries: list[Entry]) -> list[Entry]:
    """Targets before sources (a DFS topological order; cycles keep file order)."""
    by_file = {e.record.file: e for e in entries}
    out: list[Entry] = []
    state: dict[str, int] = {}

    def visit(e: Entry) -> None:
        f = e.record.file
        if state.get(f):
            return
        state[f] = 1
        for ln in (e.record.export or {}).get("links") or []:
            t = by_file.get(ln.get("target", ""))
            if t is not None and not state.get(t.record.file):
                visit(t)
        state[f] = 2
        out.append(e)

    for e in sorted(entries, key=lambda e: e.record.file):
        visit(e)
    return out


def _resolve_links(
    project: str, e: Entry, file_lid: dict[str, int], known_lids: set[int]
) -> tuple[list[dict[str, Any]], bool, int]:
    """(links, complete, dropped): targets written in this run, or — only when the file comes from
    this very project — targets named by id that exist here (Sol 42 #5: a foreign id never binds)."""
    out: list[dict[str, Any]] = []
    complete, dropped = True, 0
    ex = e.record.export or {}
    origin = split_origin(ex.get("origin") or "")
    home = origin is not None and origin[0] == project
    for ln in ex.get("links") or []:
        if "target" in ln:
            lid = file_lid.get(ln["target"])
            if lid is None:
                complete = False
                continue
        else:
            lid = ln.get("target_logical_id")
            if not home or lid not in known_lids:
                dropped += 1
                continue
        if lid == e.logical_id:
            continue
        out.append({"rel": ln["rel"], "target": lid})
    return out, complete, dropped


async def run_import(
    call: Call,
    plan: Plan,
    *,
    manifest: list[dict[str, Any]],
    meter: Any,
    progress: bool = False,
    close: bool = True,
) -> dict[str, Any]:
    """Write every new/changed record and close the removed ones; returns write statistics."""
    project = plan.project
    stats: dict[str, Any] = {"written": 0, "replayed": 0, "revisions": 0, "closed": 0, "link_revisions": 0}
    failures: list[dict[str, Any]] = []
    link_dropped = 0
    known_lids = {it["logical_id"] for it in manifest}
    file_lid: dict[str, int] = {}
    for e in plan.entries:
        if e.logical_id is not None:
            file_lid[e.record.file] = e.logical_id
    exports = [e for e in plan.entries if e.record.export is not None]
    plain = sorted((e for e in plan.entries if e.record.export is None), key=lambda e: e.record.key)
    todo = [e for e in plain + _export_order(exports) if e.action != "unchanged"]
    second: list[Entry] = []

    async def send(
        key: str, sha: str, item: dict[str, Any], expected: int | None, action: str
    ) -> dict[str, Any] | ToolCallError:
        args = {
            "project": project,
            "request_id": request_id(project, key, sha, expected, action),
            "client": CLIENT,
            "items": [item],
            "token_budget": WRITE_BUDGET,
        }
        try:
            return await call("memory.write", args)
        except ToolCallError as exc:
            return exc

    async def write_entry(e: Entry, links: list[dict[str, Any]] | None, action: str = "write") -> dict | None:
        res = await send(e.record.key, e.record.sha256, _item(e, links), e.expected, action)
        if isinstance(res, ToolCallError) and res.code == "E_VERSION_CONFLICT" and e.remapped_from is None:
            # another writer moved the item: re-read the manifest once and re-classify (Sol 40 #3)
            fresh, _ = await fetch_items(call, project)
            again = classify(project, plan.system, ParseResult(records=[e.record]), fresh, meter).entries
            if not again or again[0].action == "unchanged":
                return {"versions": [], "replayed": True, "unchanged": True}
            e.action, e.logical_id, e.expected = again[0].action, again[0].logical_id, again[0].expected
            res = await send(e.record.key, e.record.sha256, _item(e, links), e.expected, action)
        if isinstance(res, ToolCallError):
            failures.append({"key": e.record.key, "code": res.code, "message": res.message[:300]})
            return None
        return res

    for n, e in enumerate(todo, 1):
        links, complete, dropped = (None, True, 0)
        if e.record.export is not None:
            links, complete, dropped = _resolve_links(project, e, file_lid, known_lids)
            link_dropped += dropped
        ack = await write_entry(e, links)
        if ack is None or ack.get("unchanged"):
            continue
        stats["replayed" if ack.get("replayed") else "written"] += 1
        if e.action == "changed":
            stats["revisions"] += 1
        v = ack["versions"][0]
        file_lid[e.record.file] = v["logical_id"]
        e.logical_id, e.expected = v["logical_id"], v["version_id"]
        known_lids.add(v["logical_id"])
        if not complete:
            second.append(e)
        if progress and n % 25 == 0:
            sys.stderr.write(f"hlm import: {n}/{len(todo)} written\n")
    for e in second:  # links that closed a cycle: one revision with the full link set
        links, _complete, _dropped = _resolve_links(project, e, file_lid, known_lids)
        e.action = "changed"
        ack = await write_entry(e, links, "links")
        if ack is not None and not ack.get("unchanged"):
            stats["link_revisions"] += 1
    if close:
        for old in plan.closes:
            key = source_key(old.get("source")) or f"lid:{old['logical_id']}"
            res = await send(key, old["body_sha256"], _close_item(project, old), old["version_id"], "close")
            if isinstance(res, ToolCallError):
                failures.append({"key": key, "code": res.code, "message": res.message[:300]})
            else:
                stats["closed"] += 1
    stats["failed"] = failures
    stats["links_dropped"] = link_dropped
    return stats


# --------------------------------------------------------------------------- export
async def run_export(
    call: Call, project: str, out: Path, *, kinds: list[str] | None = None, as_of: str | None = None
) -> dict[str, Any]:
    items, as_of_used = await fetch_items(
        call, project, view="full", kinds=kinds, valid_at=as_of, known_at=as_of
    )
    names = exportfmt.file_names(items)
    wanted: dict[str, str] = {
        names[it["logical_id"]]: exportfmt.render_item(it, names, project) for it in items
    }
    wanted["INDEX.md"] = exportfmt.render_index(items, names)
    removed = await asyncio.to_thread(write_export_dir, out, wanted)
    return {
        "project": project,
        "out": str(out),
        "as_of": as_of_used,
        "items": len(items),
        "card": any(it["kind"] == "project_card" for it in items),
        "files": len(wanted),
        "removed": removed,
    }


def write_export_dir(out: Path, wanted: dict[str, str]) -> list[str]:
    """Write the files; remove only stale files an earlier export wrote. Returns the removed ones."""
    out.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for old in sorted(out.rglob("*.md")):
        rel = old.relative_to(out).as_posix()
        if rel in wanted:
            continue
        head = old.read_text(encoding="utf-8", errors="replace")[:4096]
        if head.startswith("---\nhlm_export: 1\n") or exportfmt.is_index(head):
            old.unlink()  # only files an earlier export wrote
            removed.append(rel)
    for rel, text in sorted(wanted.items()):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return removed


__all__ = ["Call", "fetch_full", "fetch_items", "resolve_missing", "run_export", "run_import"]
