"""`python -m hlmemo.ops` (W0a, D-052/D-061): the operator path for every admin action.

Run inside the api container (it has the app DSN), normally through `deploy/scripts/hlm_ops.sh`:

    python -m hlmemo.ops device mint --name N --class C [--grant slug:role ...] [--expires 2h] [--notes T]
    python -m hlmemo.ops device list|revoke REF|rotate REF [--expires D|--no-expiry]
    python -m hlmemo.ops device grant REF SLUG ROLE | ungrant REF SLUG
    python -m hlmemo.ops project create SLUG [--name N] [--exists-ok] | project list
    python -m hlmemo.ops status [--json]
    python -m hlmemo.ops librarian audit|questions list|approve-batch|role set|expire (ops/librarian.py)

`device mint` and `device rotate` print ONLY the token on stdout (so it can be piped into
`hlm device login --token-stdin`); their metadata goes to stderr as one JSON line. Every command
runs in ONE transaction; nothing is committed on error. Exit codes: 0 ok, 1 refused (error envelope
on stderr), 2 usage, 69 database unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from psycopg import AsyncConnection
from psycopg import Error as DatabaseError

from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.ops import librarian, service

EX_REFUSED = 1
EX_USAGE = 2
EX_UNAVAILABLE = 69


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hlmemo.ops", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="group", required=True)

    dev = sub.add_parser("device", help="mint, list, revoke, rotate, grant, ungrant devices")
    dsub = dev.add_subparsers(dest="action", required=True)
    m = dsub.add_parser("mint", help="create a trusted device; prints ONLY its token on stdout")
    m.add_argument("--name", required=True)
    m.add_argument("--class", dest="device_class", required=True, choices=service.DEVICE_CLASSES)
    m.add_argument("--grant", action="append", default=[], metavar="SLUG:ROLE")
    m.add_argument("--expires", metavar="DURATION", help="e.g. 30m, 2h, 7d (default: never)")
    m.add_argument("--notes")
    dsub.add_parser("list", help="all devices with status, expiry and grants").add_argument(
        "--json", action="store_true"
    )
    r = dsub.add_parser("revoke", help="global revocation (lost device)")
    r.add_argument("ref", help="device id or name")
    ro = dsub.add_parser("rotate", help="new token for a trusted device; prints ONLY the token")
    ro.add_argument("ref", help="device id or name")
    ro.add_argument("--expires", metavar="DURATION")
    ro.add_argument("--no-expiry", action="store_true")
    g = dsub.add_parser("grant", help="grant a role on a project")
    g.add_argument("ref")
    g.add_argument("slug")
    g.add_argument("role", choices=service.ROLES)
    u = dsub.add_parser("ungrant", help="remove a project grant")
    u.add_argument("ref")
    u.add_argument("slug")

    proj = sub.add_parser("project", help="create or list projects")
    psub = proj.add_subparsers(dest="action", required=True)
    pc = psub.add_parser("create")
    pc.add_argument("slug")
    pc.add_argument("--name")
    pc.add_argument("--exists-ok", action="store_true", help="succeed if the project already exists")
    psub.add_parser("list").add_argument("--json", action="store_true")

    st = sub.add_parser("status", help="jobs ledger, worker progress, devices, migration")
    st.add_argument("--json", action="store_true")
    librarian.add_parser(sub)  # W2b/W2c: librarian audit|questions|approve-batch|role|expire
    return ap


def _meta(obj: dict[str, Any]) -> None:
    sys.stderr.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def _device_line(d: dict[str, Any]) -> str:
    grants = ",".join(f"{g['project']}:{g['role']}" for g in d.get("grants", [])) or "-"
    name = "admin (reserved)" if d["reserved"] else d["name"]
    return (
        f"{d['id']:>4}  {name:<28} {d['class']:<9} {d['status']:<8} "
        f"expires={d['expires_at'] or '-'} last_seen={d['last_seen_at'] or '-'} grants={grants}"
    )


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.group == "status":
        return await _status(args, settings)
    async with await AsyncConnection.connect(settings.db_dsn, autocommit=False, connect_timeout=10) as conn:
        await conn.execute("SET TIME ZONE 'UTC'")
        for name, value in (
            ("lock_timeout", settings.db_lock_timeout_ms),
            ("statement_timeout", settings.db_statement_timeout_ms),
        ):
            await conn.execute("SELECT set_config(%s, %s, true)", (name, f"{value}ms"))
        try:
            rc = await _dispatch(conn, args, settings)
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()
        return rc


async def _dispatch(conn: AsyncConnection, args: argparse.Namespace, settings: Any) -> int:
    group, action = args.group, getattr(args, "action", None)
    if group == "device" and action == "mint":
        minted = await service.mint(
            conn,
            name=args.name,
            device_class=args.device_class,
            grants=args.grant,
            expires=args.expires,
            notes=args.notes,
        )
        # Commit BEFORE the token leaves the process: a printed token always works.
        await conn.commit()
        _meta({"minted": minted.device, "grants": minted.grants})
        sys.stdout.write(minted.token + "\n")
        sys.stdout.flush()
        return 0
    if group == "device" and action == "rotate":
        minted = await service.rotate(
            conn, args.ref, settings=settings, expires=args.expires, no_expiry=args.no_expiry
        )
        await conn.commit()
        _meta({"rotated": minted.device, "grants": minted.grants})
        sys.stdout.write(minted.token + "\n")
        sys.stdout.flush()
        return 0
    if group == "device" and action == "list":
        rows = await service.list_devices(conn)
        if args.json:
            _print({"devices": rows})
        else:
            for d in rows:
                sys.stdout.write(_device_line(d) + "\n")
        return 0
    if group == "device" and action == "revoke":
        _print(await service.revoke(conn, args.ref, settings=settings))
        return 0
    if group == "device" and action == "grant":
        _print(await service.grant(conn, args.ref, args.slug, args.role))
        return 0
    if group == "device" and action == "ungrant":
        _print(await service.ungrant(conn, args.ref, args.slug))
        return 0
    if group == "project" and action == "create":
        _print(await service.project_create(conn, args.slug, args.name, exists_ok=args.exists_ok))
        return 0
    if group == "project" and action == "list":
        rows = await service.list_projects(conn)
        if args.json:
            _print({"projects": rows})
        else:
            for p in rows:
                sys.stdout.write(f"{p['id']:>4}  {p['slug']:<28} {p['name']}\n")
        return 0
    if group == "librarian":
        return await librarian.dispatch(conn, args)
    raise HlmError("E_INVALID_ARG", f"unknown command {group} {action}")


async def _status(args: argparse.Namespace, settings: Any) -> int:
    """Sol 36 M2: the loopback /ready diagnostics come FIRST and are printed even when the
    database is down; the DB ledger is best-effort (exit 69 when it is unavailable)."""
    st: dict[str, Any] = {"ready": service.loopback_readiness()}
    rc = 0
    try:
        async with await AsyncConnection.connect(settings.db_dsn, autocommit=True, connect_timeout=5) as conn:
            await conn.execute("SELECT set_config('statement_timeout', '5000ms', false)")
            st.update(await service.status(conn))
        st["db"] = {"ok": True}
    except (DatabaseError, OSError) as exc:
        st["db"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc).strip()[:200]}"}
        rc = EX_UNAVAILABLE
    if args.json:
        _print(st)
        return rc
    rd = st["ready"]
    failing = sorted(k for k, v in (rd.get("checks") or {}).items() if not v.get("ok"))
    sys.stdout.write(f"ready       {rd.get('status')} failing={','.join(failing) or '-'}\n")
    for name, check in sorted((rd.get("checks") or {}).items()):
        if not check.get("ok"):
            detail = check.get("error") or check.get("reasons") or check
            sys.stdout.write(f"  check     {name}: {detail}\n")
    if not st["db"]["ok"]:
        sys.stdout.write(f"db          UNAVAILABLE {st['db']['error']}\n")
        return rc
    w = st["worker"]
    sys.stdout.write(f"migration   {','.join(st['migration'])}\n")
    sys.stdout.write(f"devices     {st['devices']}\n")
    sys.stdout.write(
        f"worker      ready={w['ready_jobs']} oldest_ready_age_s={w['oldest_ready_age_s']} "
        f"last_done_at={w['last_done_at']} expired_leases={w['expired_leases']}\n"
    )
    for j in st["jobs"]:
        sys.stdout.write(f"jobs        {j['kind']:<14} {j['status']:<8} {j['count']}\n")
    return rc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except HlmError as exc:
        sys.stderr.write(f"error {exc.code}: {exc.message}\n")
        if exc.details:
            _meta({"code": exc.code, "details": exc.details})
        return EX_USAGE if exc.code == "E_INVALID_ARG" else EX_REFUSED
    except (DatabaseError, OSError) as exc:
        sys.stderr.write(f"error E_UNAVAILABLE: database unavailable ({type(exc).__name__})\n")
        return EX_UNAVAILABLE
