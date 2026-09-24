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
from psycopg.types.json import Jsonb

from hlmemo import __version__
from hlmemo.auth.errors import HlmError
from hlmemo.auth.resolve import lock_device_access
from hlmemo.auth.tokens import generate_token, hash_token
from hlmemo.core.skeleton_card import operator_context, write_skeleton_card
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import auth_queries as q
from hlmemo.db import librarian_queries as lq
from hlmemo.db import write_queries as wq

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
    # D-015: the deterministic skeleton card, in the same transaction (a `write` event by device 1)
    card_vid = await write_skeleton_card(
        conn, slug=slug, name=name or slug, card_logical_id=int(row["card_logical_id"]), client=CLIENT
    )
    return {"project": _project_public(row), "created": True, "card_clue": f"v{card_vid}"}


def _project_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["project_id"]),
        "slug": row["slug"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "archived_at": row["archived_at"].isoformat() if row.get("archived_at") else None,
    }


#: the ``projects.policy`` keys ``project policy set`` may change, with their allowed values
POLICY_VALUES: dict[str, tuple[str, ...]] = {lq.CROSS_PROJECT_POLICY: lq.CROSS_PROJECT_VALUES}


async def _project_policy_row(conn: AsyncConnection, slug: str, *, lock: bool) -> tuple[int, dict[str, Any]]:
    cur = await conn.execute(
        "SELECT project_id, policy FROM projects WHERE slug = %s" + (" FOR UPDATE" if lock else ""), (slug,)
    )
    row = await cur.fetchone()
    if row is None:
        raise HlmError("E_NOT_FOUND", "unknown project", {"project": slug})
    return int(row[0]), dict(row[1] or {})


async def project_policy(conn: AsyncConnection, slug: str) -> dict[str, Any]:
    _pid, policy = await _project_policy_row(conn, slug, lock=False)
    return {"project": slug, "policy": policy}


async def project_policy_set(conn: AsyncConnection, slug: str, key: str, value: str) -> dict[str, Any]:
    """Set one allow-listed ``projects.policy`` key. ``librarian_cross_project exclude`` isolates a
    disposable/test project from cross-project librarian work in both directions (candidates,
    ``widen_scope``, risk_check; e2e 2026-09-24 #2). Reserved system projects are refused. The
    change is recorded as a ``librarian`` event (op ``set_project_policy``; replay rebuilds nothing
    from it: ``projects`` is not a projection)."""
    from hlmemo.librarian.events import insert_system_event

    allowed = POLICY_VALUES.get(key)
    if allowed is None:
        raise invalid(f"unknown policy key (settable: {', '.join(sorted(POLICY_VALUES))})", key=key)
    if value not in allowed:
        raise invalid(f"{key} must be one of: {', '.join(allowed)}", key=key, value=value)
    pid, policy = await _project_policy_row(conn, slug, lock=True)
    if str(policy.get("reserved", "")).lower() == "true":
        raise HlmError("E_FORBIDDEN", "reserved system project: its policy is fixed", {"project": slug})
    previous = policy.get(key)
    policy[key] = value
    await conn.execute("UPDATE projects SET policy = %s WHERE project_id = %s", (Jsonb(policy), pid))
    at = await wq.clock_now(conn)
    await insert_system_event(
        conn,
        kind="librarian",
        project_id=pid,
        device_id=OPERATOR_DEVICE_ID,
        client=CLIENT,
        request_id=uuid.uuid4(),
        request={
            "actor": CLIENT,
            "op": "set_project_policy",
            "key": key,
            "value": value,
            "previous": previous,
        },
        resolved={"recorded_at": fmt_ts(at)},
        at=at,
    )
    return {"project": slug, "policy": policy, "previous": previous, "changed": previous != value}


async def list_projects(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await q.list_projects(conn, device_id=OPERATOR_DEVICE_ID, is_admin=True)
    return [_project_public(r) for r in rows]


def loopback_readiness() -> dict[str, Any]:
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
        "librarian": await librarian_status(conn),
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


#: A fresh heartbeat file is authoritative for the in-process breaker; older ones are ignored.
HEARTBEAT_MAX_AGE_S = 120.0
#: Without a readable heartbeat, the breaker is inferred from the ledger's recent outcomes.
LEDGER_WINDOW = "15 minutes"


async def librarian_status(conn: AsyncConnection, settings: Any = None) -> dict[str, Any]:
    """The W2a heartbeat fields for the operator (carried item, D-069): ``ready``, ``in_flight``,
    ``oldest_ready_age_s``, ``failed_24h``, spend (``spend_today_usd``, ``spend_hour_usd``,
    ``reserved_usd``), the effective ``role`` and ``breaker_state``.

    Everything but the breaker comes from the database (the same ``heartbeat_fields`` the
    librarian logs). The breaker lives in the librarian process, and ``ops`` runs in the api
    container: a visible, fresh heartbeat file is used when there is one (``breaker_source =
    "heartbeat"``), else the breaker is inferred from the ledger (``"ledger"``): the latest
    ``llm_calls`` outcome of the last 15 minutes — ``breaker_open`` → ``open``,
    ``budget_deferred`` → ``budget``, any other → ``closed``. No call at all is ``closed`` too,
    as the librarian's own heartbeat reports an untripped breaker (e2e 2026-09-24 #10: ops said
    ``idle`` while the heartbeat said ``closed``); ``llm_calls_15m`` tells the two apart.
    """
    import time as _time

    from hlmemo.config import get_settings
    from hlmemo.librarian.worker import heartbeat_fields

    settings = settings or get_settings()
    out: dict[str, Any] = dict(await heartbeat_fields(conn, settings.librarian_role))
    hb = _read_heartbeat(settings.librarian_heartbeat_file)
    age = _time.time() - float(hb.get("ts", 0)) if hb is not None else None
    if hb is not None and age is not None and age <= HEARTBEAT_MAX_AGE_S:
        out["breaker_state"] = str(hb.get("breaker_state", "unknown"))
        out["breaker_source"] = "heartbeat"
        out["heartbeat_age_s"] = round(age, 1)
        out["enabled"] = bool(hb.get("enabled", True))
        return out
    cur = await conn.execute(
        "SELECT outcome, count(*) OVER () FROM llm_calls"
        f" WHERE created_at > now() - interval '{LEDGER_WINDOW}' ORDER BY created_at DESC, call_id LIMIT 1"
    )
    row = await cur.fetchone()
    state = {"breaker_open": "open", "budget_deferred": "budget"}.get(row[0], "closed") if row else "closed"
    out["breaker_state"] = state
    out["breaker_source"] = "ledger"
    out["llm_calls_15m"] = int(row[1]) if row else 0
    return out


#: open current rows = the rows the UNIQUE index mv_source_owner (0007) covers
_OWNER_ROWS = "source_key IS NOT NULL AND superseded_at = 'infinity' AND valid_to = 'infinity'"
NS_RECONCILE = uuid.UUID("7b1c2e54-9a36-4f0e-8d25-3c6a1f9e0b47")


async def source_duplicates(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Groups of open current items sharing a (project, source_key) — what blocks 0007's UNIQUE
    ``mv_source_owner`` build. Items oldest first (the last one is the one close-duplicates keeps)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            SELECT p.slug, mv.project_id, mv.source_key, mv.logical_id, mv.version_id, mv.recorded_at
              FROM memory_versions mv JOIN projects p USING (project_id)
             WHERE {_OWNER_ROWS}
               AND (mv.project_id, mv.source_key) IN (
                   SELECT project_id, source_key FROM memory_versions WHERE {_OWNER_ROWS}
                    GROUP BY project_id, source_key HAVING count(*) > 1)
             ORDER BY mv.project_id, mv.source_key, mv.recorded_at, mv.logical_id
            """
        )
        rows = await cur.fetchall()
    groups: dict[tuple[int, str], dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(
            (r["project_id"], r["source_key"]),
            {"project": r["slug"], "source_key": r["source_key"], "items": []},
        )
        g["items"].append(
            {
                "logical_id": int(r["logical_id"]),
                "version_id": int(r["version_id"]),
                "recorded_at": r["recorded_at"].isoformat(),
            }
        )
    return list(groups.values())


async def close_source_duplicates(conn: AsyncConnection) -> dict[str, Any]:
    """Keep the newest item of each duplicate group and close the others with an ordinary ``close``
    write by device 1 (validity ends now; nothing is deleted; replayable)."""
    from hlmemo.core.write_service import write

    closed: list[dict[str, Any]] = []
    for g in await source_duplicates(conn):
        for it in g["items"][:-1]:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT mv.kind, mv.title, mv.body, mv.tags, mv.pinned, mv.stability, mv.importance,"
                    " mv.device_scope, mv.valid_from, mv.source, mv.project_id,"
                    " (SELECT array_agg(p.slug ORDER BY p.slug) FROM projects p"
                    "   WHERE p.project_id = ANY(mv.project_ids) AND p.project_id <> mv.project_id) AS also,"
                    " (SELECT array_agg(c.path ORDER BY c.path) FROM code_refs c"
                    "   WHERE c.version_id = mv.version_id) AS describes"
                    " FROM memory_versions mv WHERE mv.version_id = %s",
                    (it["version_id"],),
                )
                row = await cur.fetchone()
            assert row is not None
            item: dict[str, Any] = {
                "kind": row["kind"],
                "title": row["title"],
                "body": row["body"],
                "tags": list(row["tags"]),
                "pinned": bool(row["pinned"]),
                "stability": row["stability"],
                "device_scope": row["device_scope"],
                "source": row["source"],
                "logical_id": it["logical_id"],
                "expected_version_id": it["version_id"],
                "valid_from": row["valid_from"].isoformat(),
                "close": True,
            }
            if row["importance"] is not None:
                item["importance"] = row["importance"]
            if row["also"]:
                item["project_ids"] = [g["project"], *row["also"]]
            if row["describes"]:
                item["describes"] = list(row["describes"])
            request = {
                "project": g["project"],
                "request_id": str(uuid.uuid5(NS_RECONCILE, f"close-duplicate:{it['version_id']}")),
                "client": CLIENT,
                "items": [item],
            }
            ack = await write(conn, operator_context(CLIENT), request)
            closed.append(
                {
                    "project": g["project"],
                    "source_key": g["source_key"],
                    "logical_id": it["logical_id"],
                    "version_id": ack.versions[0].version_id,
                }
            )
    return {"closed": closed}


def _read_heartbeat(path: Any) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
