"""``hlm links``: deterministic link passes over stored items, run against the database directly.

    hlm links explicit --project P [--dry-run] [--json] [--dsn DSN]   # D-184 (A)
    hlm links explicit --project P --revert [--dry-run]                # supersede those links again
    hlm links backfill --project P --apply --proposals F.jsonl [--min-confidence X]    # R4 (D-195)
    hlm links backfill --project P --dry-run --proposals F.jsonl                       # the same, rolled back
    hlm links backfill --project P --revert [--dry-run]                                # PROJECT-WIDE

``backfill`` (R4, ``ops/backfill_links``; LLM-free: the automatic proposer is not part of it) writes a
REVIEWED proposals file as ``supersedes`` links through the same evented link path: ONE event per
apply, all or nothing (R-1: a stale head or an endpoint outside ``--project`` rejects the whole apply,
exit 65, nothing written). ``--revert`` needs ``--project`` and supersedes EVERY live backfill link of
that project in ONE event: it is project-wide, not one apply's links. ``--dry-run`` reports and rolls
back.

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
from pathlib import Path
from typing import Annotated, Any

import typer

EX_USAGE = 64
EX_DATAERR = 65  # R4 (R-1): a backfill apply rejected (stale or foreign endpoint): nothing written
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


# ------------------------------------------------------------------ R4 backfill (D-195, apply/revert only)
async def _backfill(dsn: str, project: str, mode: str, opts: dict[str, Any]) -> dict[str, Any]:
    from psycopg import AsyncConnection

    from hlmemo.config import get_settings
    from hlmemo.ops import backfill_links as bf

    settings = get_settings()
    async with await AsyncConnection.connect(dsn, autocommit=False, connect_timeout=10) as conn:
        await conn.execute("SET TIME ZONE 'UTC'")
        for name, value in (
            ("lock_timeout", settings.db_lock_timeout_ms),
            ("statement_timeout", settings.db_statement_timeout_ms),
        ):
            await conn.execute("SELECT set_config(%s, %s, true)", (name, f"{value}ms"))
        try:
            if mode == "apply":
                out = await bf.apply(
                    conn,
                    project,
                    opts["records"],
                    min_confidence=opts["min_confidence"],
                    preview=opts["preview"],
                )
            else:
                out = await bf.revert(conn, project, preview=opts["preview"])
        except BaseException:
            await conn.rollback()  # a rejected apply included: nothing is written
            raise
        await (conn.rollback() if opts["preview"] else conn.commit())
        return out


def _backfill_human(out: dict[str, Any], mode: str) -> list[str]:
    if mode == "apply":
        lines = [
            f"backfill apply  project={out['project']}  selected={out['selected']}"
            f"  already_linked={out['already_linked']}  dropped={out['dropped']}"
            + ("  (dry run: nothing written)" if out["preview"] else f"  applied={out['applied']}")
        ]
        lines += [
            f"  v{ln['src_vid']} -> v{ln['dst_vid']}  {ln['scope']}  conf={ln['confidence']}"
            for ln in out["links"]
        ]
    else:
        verb = "would revert" if out["preview"] else "reverted"
        lines = [f"backfill revert  project={out['project']}  {verb}={len(out['links'])}  (project-wide)"]
    if out.get("event_id"):
        lines.append(f"event {out['event_id']}")
    return lines


@links_app.command("backfill")
def backfill(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option("--project", help="Project slug (REQUIRED)")] = None,
    apply_: Annotated[
        bool, typer.Option("--apply", help="Write the proposals of --proposals as links")
    ] = False,
    revert: Annotated[
        bool, typer.Option("--revert", help="Supersede EVERY live backfill link of --project (one event)")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what --apply/--revert would do, then roll back")
    ] = False,
    proposals: Annotated[
        Path | None, typer.Option("--proposals", help="The reviewed proposals JSONL")
    ] = None,
    min_confidence: Annotated[float, typer.Option("--min-confidence", min=0.0, max=1.0)] = 0.0,
    dsn: Annotated[
        str | None,
        typer.Option("--dsn", envvar="HLM_DB_DSN", help="Database DSN (default HLM_DB_DSN / hlm.toml)"),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Apply (or revert) reviewed supersession proposals as `supersedes` links (R4, LLM-free)."""
    from psycopg import Error as DatabaseError

    from hlmemo.auth.errors import HlmError
    from hlmemo.ops import backfill_links as bf

    root = ctx.find_root().obj
    if apply_ and revert:
        typer.echo("error: --apply and --revert exclude each other", err=True)
        raise typer.Exit(EX_USAGE)
    mode = "revert" if revert else ("apply" if apply_ or (dry_run and proposals is not None) else None)
    if not project or mode is None:  # R-7: --project is required (never a default project here)
        typer.echo(
            "error: --project and one of --apply / --dry-run --proposals / --revert are required", err=True
        )
        raise typer.Exit(EX_USAGE)
    if mode == "apply" and (proposals is None or not proposals.is_file()):
        typer.echo("error: --apply needs an existing --proposals file", err=True)
        raise typer.Exit(EX_USAGE)
    if dsn is None:
        from hlmemo.config import get_settings

        dsn = get_settings().db_dsn
    as_json = json_out or bool(getattr(root, "as_json", False))
    opts: dict[str, Any] = {"min_confidence": min_confidence, "preview": dry_run}
    try:
        if mode == "apply":
            assert proposals is not None
            opts["records"] = bf.read_proposals(proposals)
        result = asyncio.run(_backfill(dsn, project, mode, opts))
    except bf.BackfillRejected as exc:
        typer.echo(f"error E_REJECTED: {exc}", err=True)
        typer.echo(json.dumps({"rejected": True, **exc.details}, sort_keys=True, default=str))
        raise typer.Exit(EX_DATAERR) from None
    except HlmError as exc:
        typer.echo(f"error {exc.code}: {exc.message}", err=True)
        raise typer.Exit(EX_USAGE) from None
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        typer.echo(f"error E_INVALID_ARG: proposals file: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(EX_USAGE) from None
    except (DatabaseError, OSError) as exc:
        typer.echo(f"error E_UNAVAILABLE: database unavailable ({type(exc).__name__}: {exc})", err=True)
        raise typer.Exit(EX_UNAVAILABLE) from None
    if as_json:
        typer.echo(json.dumps(result, sort_keys=True, ensure_ascii=False, default=str))
    else:
        for line in _backfill_human(result, mode):
            typer.echo(line)


__all__ = ["links_app"]
