"""``hlm import`` / ``hlm export`` command bodies (kept out of ``cli/hlm.py``; it only wires them)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hlmemo.importers import automemory, context, markdown, serena
from hlmemo.importers.common import ParseResult
from hlmemo.importers.plan import classify, report
from hlmemo.importers.runner import Call, fetch_items, run_export, run_import

SOURCES = ("markdown", "automemory", "serena", "context")


def resolve_tz(name: str | None) -> tzinfo:
    """``--tz``: an IANA zone, ``UTC``, or ``local`` (default) — the zone date-only evidence is read in."""
    if name is None or name == "local":
        local = datetime.now().astimezone().tzinfo
        return local if local is not None else UTC
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - zoneinfo raises several types
        raise ValueError(f"unknown time zone {name!r}") from exc


def parse_source(
    source: str,
    paths: list[Path],
    *,
    base: Path | None = None,
    repo: Path | None = None,
    now: datetime | None = None,
    tz: tzinfo | None = None,
) -> ParseResult:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if not paths:
        raise ValueError(f"`hlm import {source}` needs at least one path")
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise ValueError(f"path(s) not found: {', '.join(missing)}")
    if source == "markdown":
        return markdown.parse(paths, base=base, repo=repo, now=now, tz=tz)
    if source == "context":
        return context.parse(paths, base=base, repo=repo, now=now, tz=tz)
    if len(paths) != 1:
        raise ValueError(f"`hlm import {source}` takes exactly one directory")
    if source == "automemory":
        return automemory.parse(paths[0], repo=repo, now=now, tz=tz)
    return serena.parse(paths[0], repo=repo, now=now, tz=tz)


async def import_async(
    call: Call | None,
    *,
    source: str,
    parsed: ParseResult,
    project: str,
    dry_run: bool,
    meter: Any = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Classify against the server manifest (empty when ``call`` is None: ``--offline``) and write."""
    if meter is None:
        from hlmemo.core.budget import Meter

        meter = Meter()
    manifest: list[dict[str, Any]] = []
    if call is not None:
        manifest, _as_of = await fetch_items(call, project)
    plan = classify(project, source, parsed, manifest, meter)
    out = report(plan, dry_run=dry_run or call is None)
    if call is None or dry_run:
        return out
    out["writes"] = await run_import(call, plan, manifest=manifest, meter=meter, progress=progress)
    return out


def human_summary(rep: dict[str, Any]) -> str:
    c = rep["counts"]
    mode = " (dry-run)" if rep["dry_run"] else ""
    lines = [
        f"hlm import {rep['source']} -> project {rep['project']}{mode}",
        f"  new {c['new']}  changed {c['changed']}  unchanged {c['unchanged']}  skipped {c['skipped']}  "
        f"rejected {c['rejected']}  missing {c['missing']}",
        f"  token estimate {rep['token_estimate']['tokens']} (o200k) for "
        f"{rep['token_estimate']['items']} item(s)",
    ]
    for r in rep["rejected"]:
        lines.append(f"  rejected {r['key']}: {r['reason']} {r['date'] or ''}".rstrip())
    for g in rep["duplicate_groups"]:
        lines.append(f"  duplicates ({g['reason']}): {', '.join(g['paths'])}")
    for m in rep["missing"]:
        lines.append(f"  missing (no longer produced; not closed): {m}")
    w = rep.get("writes")
    if w:
        lines.append(
            f"  wrote {w['written']} (replayed {w['replayed']}, revisions {w['revisions']}, "
            f"link revisions {w['link_revisions']}, links dropped {w['links_dropped']}, "
            f"failed {len(w['failed'])})"
        )
        for f in w["failed"]:
            lines.append(f"  FAILED {f['key']}: {f['code']} {f['message']}")
    return "\n".join(lines)


def run_import_command(
    *,
    source: str,
    paths: list[Path],
    project: str,
    dry_run: bool,
    offline: bool,
    base: Path | None,
    repo: Path | None,
    memory: Any,
    progress: bool,
    tz: str | None = None,
) -> dict[str, Any]:
    parsed = parse_source(source, paths, base=base, repo=repo, now=datetime.now(UTC), tz=resolve_tz(tz))

    async def go() -> dict[str, Any]:
        if offline:
            return await import_async(None, source=source, parsed=parsed, project=project, dry_run=True)
        async with memory.session() as call:
            return await import_async(
                call, source=source, parsed=parsed, project=project, dry_run=dry_run, progress=progress
            )

    return asyncio.run(go())


def run_export_command(
    *, project: str, out: Path, kinds: list[str] | None, as_of: str | None, memory: Any
) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with memory.session() as call:
            return await run_export(call, project, out, kinds=kinds, as_of=as_of)

    return asyncio.run(go())


__all__ = [
    "SOURCES",
    "human_summary",
    "import_async",
    "parse_source",
    "run_export_command",
    "run_import_command",
]
