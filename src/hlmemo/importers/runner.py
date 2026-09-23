"""The network half of ``hlm import`` / ``hlm export``: manifest paging, writes, export files.

Everything goes through one injected ``call(tool, arguments) -> dict`` (an MCP session in the CLI,
an in-process caller in tests), which raises ``ToolCallError`` for tool errors. Writes are one
``memory.write`` per record with ``request_id = uuid5(project ‖ source_key ‖ sha256)``, so an
interrupted run simply resumes (re-sent content replays). Export-format records with links are
written targets-first; links that close a cycle are added by a second revision.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.importers import exportfmt
from hlmemo.importers.plan import CLIENT, Entry, Plan, classify, request_id

Call = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
EXPORT_TOOL = "hlm.export"
PAGE_BUDGET = 32000
WRITE_BUDGET = 2000


async def fetch_items(
    call: Call,
    project: str,
    *,
    view: str = "manifest",
    kinds: list[str] | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
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
    src = rec.source() if (ex is None or ex.get("source") is not None) else None
    if src is not None:
        item["source"] = src
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
    if e.action == "changed":
        if rec.kind_guess != "project_card":
            item["logical_id"] = e.logical_id
        item["expected_version_id"] = e.head_version_id
    elif rec.kind_guess == "project_card":
        item["expected_version_id"] = e.head_version_id
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
    e: Entry, file_lid: dict[str, int], known_lids: set[int]
) -> tuple[list[dict[str, Any]], bool, int]:
    """(links, complete, dropped): targets written in this run or already present by id."""
    out: list[dict[str, Any]] = []
    complete, dropped = True, 0
    for ln in (e.record.export or {}).get("links") or []:
        if "target" in ln:
            lid = file_lid.get(ln["target"])
            if lid is None:
                complete = False
                continue
        else:
            lid = ln.get("target_logical_id")
            if lid not in known_lids:
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
) -> dict[str, Any]:
    """Write every new/changed record; returns write statistics (merged into the report)."""
    project = plan.project
    stats: dict[str, Any] = {"written": 0, "replayed": 0, "revisions": 0, "link_revisions": 0}
    failures: list[dict[str, Any]] = []
    link_dropped = 0
    known_lids = {it["logical_id"] for it in manifest}
    file_lid: dict[str, int] = {}
    heads: dict[str, int] = {}
    for e in plan.entries:
        if e.logical_id is not None:
            file_lid[e.record.file] = e.logical_id
            heads[e.record.file] = e.head_version_id or 0
    exports = [e for e in plan.entries if e.record.export is not None]
    plain = sorted((e for e in plan.entries if e.record.export is None), key=lambda e: e.record.key)
    todo = [e for e in plain + _export_order(exports) if e.action != "unchanged"]
    second: list[Entry] = []

    async def send(e: Entry, links: list[dict[str, Any]] | None, salt: str = "") -> dict[str, Any] | None:
        args = {
            "project": project,
            "request_id": request_id(project, e.record.key, e.record.sha256, salt),
            "client": CLIENT,
            "items": [_item(e, links)],
            "token_budget": WRITE_BUDGET,
        }
        try:
            return await call("memory.write", args)
        except ToolCallError as exc:
            if exc.code in ("E_VERSION_CONFLICT", "E_REQUEST_ID_CONFLICT") and not salt.endswith("#retry"):
                # another writer moved the item: re-read the manifest once and re-classify (Sol 40 #3)
                fresh, _ = await fetch_items(call, project)
                again = classify(project, plan.system, _single(plan, e), fresh, meter).entries[0]
                if again.action == "unchanged":
                    return {"versions": [], "replayed": True, "unchanged": True}
                e.action, e.logical_id, e.head_version_id = (
                    again.action,
                    again.logical_id,
                    again.head_version_id,
                )
                return await send(e, links, salt + "#retry")
            failures.append({"key": e.record.key, "code": exc.code, "message": exc.message[:300]})
            return None

    for n, e in enumerate(todo, 1):
        links, complete, dropped = (None, True, 0)
        if e.record.export is not None:
            links, complete, dropped = _resolve_links(e, file_lid, known_lids)
            link_dropped += dropped
        ack = await send(e, links)
        if ack is None or ack.get("unchanged"):
            continue
        stats["replayed" if ack.get("replayed") else "written"] += 1
        if e.action == "changed":
            stats["revisions"] += 1
        v = ack["versions"][0]
        file_lid[e.record.file] = v["logical_id"]
        e.logical_id, e.head_version_id = v["logical_id"], v["version_id"]
        known_lids.add(v["logical_id"])
        if not complete:
            second.append(e)
        if progress and n % 25 == 0:
            sys.stderr.write(f"hlm import: {n}/{len(todo)} written\n")
    for e in second:  # links that closed a cycle: one revision with the full link set
        links, _complete, _dropped = _resolve_links(e, file_lid, known_lids)
        e.action = "changed"
        ack = await send(e, links, "#links")
        if ack is not None and not ack.get("unchanged"):
            stats["link_revisions"] += 1
            e.head_version_id = ack["versions"][0]["version_id"]
    stats["failed"] = failures
    stats["links_dropped"] = link_dropped
    return stats


def _single(plan: Plan, e: Entry) -> Any:
    from hlmemo.importers.common import ParseResult

    return ParseResult(records=[e.record], scopes=[])


# --------------------------------------------------------------------------- export
async def run_export(
    call: Call, project: str, out: Path, *, kinds: list[str] | None = None, as_of: str | None = None
) -> dict[str, Any]:
    items, as_of_used = await fetch_items(
        call, project, view="full", kinds=kinds, valid_at=as_of, known_at=as_of
    )
    names = exportfmt.file_names(items)
    wanted: dict[str, str] = {names[it["logical_id"]]: exportfmt.render_item(it, names) for it in items}
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


__all__ = ["Call", "fetch_items", "run_export", "run_import"]
