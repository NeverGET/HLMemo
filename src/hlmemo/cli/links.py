"""``hlm links``: deterministic link passes over stored items, run against the database directly.

    hlm links explicit --project P [--dry-run] [--json] [--dsn DSN]   # D-184 (A)
    hlm links explicit --project P --revert [--dry-run]                # supersede those links again

``explicit`` finds EXPLICIT supersession declarations in the project's current items ("MERGED …
from <path>", "supersedes D-036", "superseded by <path>", ...; ``core/explicit_supersession``)
and writes them as ``supersedes`` links through the librarian's evented link path
(``ops/explicit_links``): replayable, reversible with ``--revert``, idempotent (a pair a live link
already joins is skipped). ``--dry-run`` prints the proposals and writes nothing. No LLM call.

It is an operator command: it connects with ``--dsn`` (default ``HLM_DB_DSN`` / hlm.toml) and
records its events as the reserved admin device. Exit codes: 0 ok, 64 usage/unknown project,
69 database unavailable.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import typer

EX_USAGE = 64
EX_UNAVAILABLE = 69

links_app = typer.Typer(
    help="Deterministic link passes over stored items (operator, direct DB).",
    no_args_is_help=True,
    rich_markup_mode=None,
)


async def _run(dsn: str, project: str, *, dry_run: bool, revert: bool) -> dict[str, Any]:
    from psycopg import AsyncConnection

    from hlmemo.config import get_settings
    from hlmemo.ops import explicit_links as xl

    settings = get_settings()
    async with await AsyncConnection.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        await conn.execute("SET TIME ZONE 'UTC'")
        for name, value in (
            ("lock_timeout", settings.db_lock_timeout_ms),
            ("statement_timeout", settings.db_statement_timeout_ms),
        ):
            await conn.execute("SELECT set_config(%s, %s, true)", (name, f"{value}ms"))
        try:
            fn = xl.revert if revert else xl.apply
            out = await fn(conn, project, dry_run=dry_run)
        except BaseException:
            await conn.rollback()
            raise
        if dry_run:
            await conn.rollback()
        else:
            await conn.commit()
        return out


def _short(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _human(out: dict[str, Any], *, revert: bool) -> list[str]:
    if revert:
        verb = "would revert" if out["dry_run"] else "reverted"
        lines = [f"explicit links  project={out['project']}  {verb}={len(out['links'])}"]
        lines += [
            f"  link {ln['link_id']}: {ln['src_logical_id']} -> {ln['dst_logical_id']}"
            f"  {ln['props'].get('scope')}  {ln['props'].get('marker')}"
            for ln in out["links"]
        ]
        if out.get("event_id"):
            lines.append(f"event {out['event_id']}")
        return lines
    lines = [
        f"explicit supersession  project={out['project']}  parser={out['parser']}  items={out['items']}"
        f"  proposals={len(out['proposals'])}  already_linked={out['already_linked']}"
        + (
            "  (dry run: nothing written)"
            if out["dry_run"]
            else f"  applied={out['applied']}  stale={out['stale']}"
        )
    ]
    for p in out["proposals"]:
        lines.append(
            f"  {p['source_path'] or 'v' + str(p['source_version_id'])} [{p['source_ref']}]"
            f" -> {p['target_path'] or 'v' + str(p['target_version_id'])} [{p['target_ref']}]"
            f"  {p['scope']}  {p['marker']}"
        )
        lines.append(f"      quote: {_short(p['quote'])}")
    if out.get("event_id"):
        lines.append(f"event {out['event_id']}")
    return lines


@links_app.command("explicit")
def explicit(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option("--project", help="Project slug (default: the root --project / HLM_PROJECT)")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the proposals; write nothing")] = False,
    revert: Annotated[
        bool, typer.Option("--revert", help="Supersede every live explicit link of the project (evented)")
    ] = False,
    dsn: Annotated[
        str | None,
        typer.Option("--dsn", envvar="HLM_DB_DSN", help="Database DSN (default HLM_DB_DSN / hlm.toml)"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Write the EXPLICIT supersession declarations of a project as `supersedes` links (D-184)."""
    from psycopg import Error as DatabaseError

    from hlmemo.auth.errors import HlmError

    root = ctx.find_root().obj
    project = project or getattr(root, "project", None)
    if not project:
        typer.echo("error: --project is required", err=True)
        raise typer.Exit(EX_USAGE)
    if dsn is None:
        from hlmemo.config import get_settings

        dsn = get_settings().db_dsn
    as_json = json_out or bool(getattr(root, "as_json", False))
    try:
        out = asyncio.run(_run(dsn, project, dry_run=dry_run, revert=revert))
    except HlmError as exc:
        typer.echo(f"error {exc.code}: {exc.message}", err=True)
        raise typer.Exit(EX_USAGE) from None
    except (DatabaseError, OSError) as exc:
        typer.echo(f"error E_UNAVAILABLE: database unavailable ({type(exc).__name__}: {exc})", err=True)
        raise typer.Exit(EX_UNAVAILABLE) from None
    if as_json:
        typer.echo(json.dumps(out, sort_keys=True, ensure_ascii=False, default=str))
    else:
        for line in _human(out, revert=revert):
            typer.echo(line)


__all__ = ["links_app"]
