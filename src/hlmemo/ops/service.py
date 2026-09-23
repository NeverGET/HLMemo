"""Operator actions (W0a, D-052/D-061): the only admin path when `admin_http=disabled`.

Every function runs inside the caller's single transaction on a plain connection to the app DSN,
uses the same query layer as the HTTP routes (`db/auth_queries.py`) and never commits. Events
carry `device_id = 1` (the reserved admin device acts; it needs no bound token) and
`client = hlm-ops/<version>`. A plaintext token is returned only by `mint` / `rotate` and is never
written to an event, a log line or the database.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg import errors as pgerrors
from psycopg.rows import dict_row

from hlmemo import __version__
from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import lock_device_access
from hlmemo.auth.tokens import generate_token, hash_token
from hlmemo.db import auth_queries as q

CLIENT = f"hlm-ops/{__version__}"
OPERATOR_DEVICE_ID = 1
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
DEVICE_CLASSES = ("personal", "work", "server", "ci", "other")
ROLES = ("read", "write", "admin")
_DURATION = re.compile(r"^([1-9][0-9]{0,6})([smhdw])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def invalid(message: str, **details: Any) -> HlmError:
    return HlmError("E_INVALID_ARG", message, details)


def parse_duration(text: str) -> timedelta:
    """`30m`, `2h`, `7d`, `2w`, `90s` -> timedelta (no fractional or compound values)."""
    m = _DURATION.match(text.strip())
    if not m:
        raise invalid("duration must look like 30m, 2h, 7d or 2w", value=text)
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])


def parse_grant(spec: str) -> tuple[str, str]:
    slug, _, role = spec.partition(":")
    if not SLUG.match(slug) or role not in ROLES:
        raise invalid(f"grant must be slug:role with role in {ROLES}", value=spec)
    return slug, role


def _check_name(name: str) -> None:
    if not SLUG.match(name) or name == "admin":
        raise invalid("device name must be a slug ([a-z0-9-], 2-64 chars) and not 'admin'", value=name)


def _check_class(device_class: str) -> None:
    if device_class not in DEVICE_CLASSES:
        raise invalid(f"class must be one of {DEVICE_CLASSES}", value=device_class)


def device_public(row: dict[str, Any]) -> dict[str, Any]:
    """Operator view of a device row (never the token hash)."""

    def iso(v: datetime | None) -> str | None:
        return v.isoformat() if v is not None else None

    status = "revoked" if row.get("expired") and row["status"] == "trusted" else row["status"]
    return {
        "id": int(row["device_id"]),
        "name": row["name"],
        "class": row["class"],
        "status": status,
        "expired": bool(row.get("expired")),
        "expires_at": iso(row.get("expires_at")),
        "reserved": int(row["device_id"]) == OPERATOR_DEVICE_ID,
        "token_generation": int(row["token_generation"]),
        "registered_at": iso(row.get("registered_at")),
        "last_seen_at": iso(row.get("last_seen_at")),
        "revoked_at": iso(row.get("revoked_at")),
        "notes": row.get("notes"),
    }


async def _event(
    conn: AsyncConnection, kind: str, request: dict[str, Any], resolved: dict[str, Any], **kw: Any
) -> None:
    await q.insert_event(
        conn, kind=kind, device_id=OPERATOR_DEVICE_ID, client=CLIENT, request=request, resolved=resolved, **kw
    )


async def _project(conn: AsyncConnection, slug: str) -> dict[str, Any]:
    project = await q.select_project_by_slug(conn, slug)
    if project is None:
        raise HlmError("E_NOT_FOUND", "unknown project", {"project": slug})
    return project


async def _device_id(conn: AsyncConnection, ref: str) -> int:
    """`<id>` or `<name>` -> device id (no lock); device 1 is never an ops target."""
    if ref.isdigit():
        device_id = int(ref)
    else:
        row = await q.select_device_by_name(conn, ref)
        if row is None:
            raise HlmError("E_NOT_FOUND", "no device with that name", {"device": ref})
        device_id = int(row["device_id"])
    if device_id == OPERATOR_DEVICE_ID:
        raise HlmError("E_FORBIDDEN", "device 1 is reserved")
    return device_id


async def _device_for_update(
    conn: AsyncConnection, ref: str, *, exclusive_settings: Any = None
) -> dict[str, Any]:
    """Row lock on the target. With `exclusive_settings`, first queue the exclusive advisory lock
    behind in-flight requests of that device (same order as HTTP revoke: advisory, then row)."""
    device_id = await _device_id(conn, ref)
    if exclusive_settings is not None:
        wait_ms = int(exclusive_settings.request_db_timeout_s * 1000) + exclusive_settings.db_lock_timeout_ms
        for name in ("lock_timeout", "statement_timeout"):
            await conn.execute("SELECT set_config(%s, %s, true)", (name, f"{wait_ms}ms"))
        await lock_device_access(conn, device_id, exclusive=True)
    row = await q.select_device_for_update(conn, device_id)
    if row is None:
        raise HlmError("E_NOT_FOUND", "device not found", {"id": device_id})
    return row


@dataclass
class Minted:
    token: str
    device: dict[str, Any]
    grants: list[dict[str, str]]


async def mint(
    conn: AsyncConnection,
    *,
    name: str,
    device_class: str,
    grants: list[str] = (),  # type: ignore[assignment]
    expires: str | None = None,
    notes: str | None = None,
) -> Minted:
    """Create a trusted device + grants in one transaction; returns the only copy of its token."""
    _check_name(name)
    _check_class(device_class)
    parsed = [parse_grant(g) for g in grants]
    slugs = [slug for slug, _ in parsed]
    if len(set(slugs)) != len(slugs):
        raise invalid("duplicate project in --grant")
    projects = {slug: await _project(conn, slug) for slug in slugs}
    expires_at = datetime.now(UTC) + parse_duration(expires) if expires else None
    token = generate_token()
    await q.release_revoked_device_name(conn, name)
    try:
        async with conn.transaction():
            row = await q.insert_minted_device(
                conn,
                name=name,
                device_class=device_class,
                token_hash=hash_token(token),
                fingerprint=f"minted:{uuid.uuid4()}",
                notes=notes,
                expires_at=expires_at,
            )
    except pgerrors.UniqueViolation as exc:
        raise invalid("device name already in use by an active device", name=name) from exc
    device_id = int(row["device_id"])
    request: dict[str, Any] = {"command": "device mint", "name": name, "class": device_class}
    request.update(grants=list(grants), expires=expires, notes=notes)
    await _event(
        conn,
        "device_minted",
        request,
        {
            "device_id": device_id,
            "status": "trusted",
            "class": device_class,
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "token_generation": int(row["token_generation"]),
            "via": "mint",
        },
    )
    out_grants = []
    for slug, role in parsed:
        pid = int(projects[slug]["project_id"])
        await q.upsert_grant(
            conn, device_id=device_id, project_id=pid, role=role, granted_by=OPERATOR_DEVICE_ID
        )
        await _event(
            conn,
            "grant_added",
            {"device": device_id, "project": slug, "role": role},
            {"device_id": device_id, "project_id": pid, "via": "ops_mint"},
            project_id=pid,
        )
        out_grants.append({"project": slug, "role": role})
    return Minted(token=token, device=device_public(row), grants=out_grants)


async def rotate(
    conn: AsyncConnection,
    ref: str,
    *,
    settings: Any,
    expires: str | None = None,
    no_expiry: bool = False,
) -> Minted:
    """New token for a trusted device (old token, cursors and sessions stop working at commit)."""
    if expires and no_expiry:
        raise invalid("--expires and --no-expiry are mutually exclusive")
    row = await _device_for_update(conn, ref, exclusive_settings=settings)
    if row["status"] != "trusted":
        raise invalid(
            f"device is {row['status']}; only trusted devices can be rotated", id=int(row["device_id"])
        )
    device_id = int(row["device_id"])
    expires_at = datetime.now(UTC) + parse_duration(expires) if expires else None
    keep = not expires and not no_expiry
    if keep and row.get("expired"):
        raise invalid("device has expired; pass --expires DURATION or --no-expiry", id=device_id)
    token = generate_token()
    row = await q.rotate_device_token(
        conn, device_id, token_hash=hash_token(token), expires_at=expires_at, keep_expiry=keep
    )
    await _event(
        conn,
        "device_minted",
        {"command": "device rotate", "device": ref, "expires": expires, "no_expiry": no_expiry},
        {
            "device_id": device_id,
            "status": "trusted",
            "class": row["class"],
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "token_generation": int(row["token_generation"]),
            "via": "rotate",
        },
    )
    grants = await q.list_grants_with_slugs(conn, device_id)
    return Minted(
        token=token,
        device=device_public(row),
        grants=[{"project": g["project"], "role": g["role"]} for g in grants],
    )


async def revoke(conn: AsyncConnection, ref: str, *, settings: Any) -> dict[str, Any]:
    """Global revocation of another device (the operator path for the lost-laptop case)."""
    row = await _device_for_update(conn, ref, exclusive_settings=settings)
    device_id = int(row["device_id"])
    if row["status"] == "revoked":
        raise invalid("device already revoked", id=device_id)
    row = await q.set_device_revoked(conn, device_id)
    revoked_projects = await q.revoke_all_grants(conn, device_id)
    await _event(
        conn,
        "device_revoked",
        {"id": device_id, "command": "device revoke"},
        {
            "device_id": device_id,
            "token_generation": int(row["token_generation"]),
            "revoked_project_ids": revoked_projects,
        },
    )
    for pid in revoked_projects:
        await _event(
            conn,
            "grant_revoked",
            {"device": device_id, "project_id": pid},
            {"device_id": device_id, "project_id": pid, "via": "revoke"},
            project_id=pid,
        )
    return {"device": device_public(row), "revoked_grants": len(revoked_projects)}


async def grant(conn: AsyncConnection, ref: str, slug: str, role: str) -> dict[str, Any]:
    if role not in ROLES:
        raise invalid(f"role must be one of {ROLES}", value=role)
    project = await _project(conn, slug)
    row = await _device_for_update(conn, ref)
    if row["status"] == "revoked":
        raise invalid("device is revoked", id=int(row["device_id"]))
    device_id, pid = int(row["device_id"]), int(project["project_id"])
    await q.upsert_grant(conn, device_id=device_id, project_id=pid, role=role, granted_by=OPERATOR_DEVICE_ID)
    await _event(
        conn,
        "grant_added",
        {"device": device_id, "project": slug, "role": role},
        {"device_id": device_id, "project_id": pid, "via": "ops"},
        project_id=pid,
    )
    return {"grant": {"device": device_id, "project": slug, "role": role}}


async def ungrant(conn: AsyncConnection, ref: str, slug: str) -> dict[str, Any]:
    project = await _project(conn, slug)
    row = await _device_for_update(conn, ref)
    device_id, pid = int(row["device_id"]), int(project["project_id"])
    if not await q.revoke_grant(conn, device_id=device_id, project_id=pid):
        raise HlmError("E_NOT_FOUND", "no active grant", {"device": device_id, "project": slug})
    await _event(
        conn,
        "grant_revoked",
        {"device": device_id, "project": slug},
        {"device_id": device_id, "project_id": pid, "via": "ops"},
        project_id=pid,
    )
    return {"revoked": {"device": device_id, "project": slug}}


async def list_devices(conn: AsyncConnection) -> list[dict[str, Any]]:
    out = []
    for row in await q.list_all_devices(conn):
        view = device_public(row)
        grants = await q.list_grants_with_slugs(conn, int(row["device_id"]))
        view["grants"] = [{"project": g["project"], "role": g["role"]} for g in grants]
        out.append(view)
    return out


async def project_create(
    conn: AsyncConnection, slug: str, name: str | None, *, exists_ok: bool = False
) -> dict[str, Any]:
    """Same effects as `POST /admin/projects` from device 1 (project_created + grant_added)."""
    if not SLUG.match(slug):
        raise invalid("project slug must match [a-z0-9][a-z0-9-]{1,63}", value=slug)
    existing = await q.select_project_by_slug(conn, slug)
    if existing is not None:
        if exists_ok:
            return {"project": _project_public(existing), "created": False}
        raise invalid("project already exists", project=slug)
    row = await q.insert_project(conn, slug=slug, name=name or slug)
    pid = int(row["project_id"])
    await q.upsert_grant(
        conn, device_id=OPERATOR_DEVICE_ID, project_id=pid, role="admin", granted_by=OPERATOR_DEVICE_ID
    )
    await _event(
        conn,
        "project_created",
        {"slug": slug, "name": name or slug},
        {"project_id": pid, "card_logical_id": int(row["card_logical_id"])},
        project_id=pid,
    )
    await _event(
        conn,
        "grant_added",
        {"device": OPERATOR_DEVICE_ID, "project": slug, "role": "admin"},
        {"device_id": OPERATOR_DEVICE_ID, "project_id": pid, "via": "project_create"},
        project_id=pid,
    )
    return {"project": _project_public(row), "created": True}


def _project_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["project_id"]),
        "slug": row["slug"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "archived_at": row["archived_at"].isoformat() if row.get("archived_at") else None,
    }


async def list_projects(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await q.list_projects(conn, device_id=OPERATOR_DEVICE_ID, is_admin=True)
    return [_project_public(r) for r in rows]


def _loopback_readiness() -> dict[str, Any]:
    """The API's detailed /ready, read on its loopback listener (only loopback peers get checks)."""
    import urllib.error
    import urllib.request

    from hlmemo.config import get_settings

    url = f"http://127.0.0.1:{get_settings().api_port}/ready"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except ValueError:
            return {"status": "not_ready", "error": f"HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"status": "unreachable", "error": f"{type(exc).__name__}: {exc}"[:200]}


async def status(conn: AsyncConnection) -> dict[str, Any]:
    """Jobs ledger, worker progress (last committed job), devices by status, migration."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT kind, status, count(*) AS n FROM jobs GROUP BY kind, status ORDER BY kind, status"
        )
        jobs = [
            {"kind": r["kind"], "status": r["status"], "count": int(r["n"])} for r in await cur.fetchall()
        ]
        await cur.execute(
            "SELECT count(*) FILTER (WHERE status = 'queued' AND run_after <= now()) AS ready,"
            " EXTRACT(EPOCH FROM now() - min(run_after) FILTER (WHERE status = 'queued'"
            " AND run_after <= now()))::float AS oldest_ready_age_s,"
            " max(done_at) AS last_done_at, count(*) FILTER (WHERE status = 'running'"
            " AND lease_until < now()) AS expired_leases FROM jobs"
        )
        ledger = await cur.fetchone() or {}
        await cur.execute(
            "SELECT CASE WHEN status = 'trusted' AND expires_at IS NOT NULL AND expires_at <= now()"
            " THEN 'expired' ELSE status END AS s, count(*) AS n FROM devices WHERE device_id <> 1"
            " GROUP BY 1 ORDER BY 1"
        )
        devices = {r["s"]: int(r["n"]) for r in await cur.fetchall()}
        await cur.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        migration = [r["version_num"] for r in await cur.fetchall()]
    admin = await q.admin_state(conn)
    return {
        "ready": _loopback_readiness(),
        "jobs": jobs,
        "worker": {
            "ready_jobs": int(ledger.get("ready") or 0),
            "oldest_ready_age_s": ledger.get("oldest_ready_age_s"),
            "last_done_at": ledger["last_done_at"].isoformat() if ledger.get("last_done_at") else None,
            "expired_leases": int(ledger.get("expired_leases") or 0),
        },
        "devices": devices,
        "admin_device": {"http_token_bound": not admin["disabled"]},
        "migration": migration,
    }
