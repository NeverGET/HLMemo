"""All SQL for devices / grants / projects / device-level events (PHASE0-SPEC §2).

Every function takes an open psycopg `AsyncConnection` inside the caller's transaction and never
commits. Locking discipline (§2 "per-request, in-transaction authorization"):
  * request authorization  -> `select_device_by_hash_for_share`  (FOR SHARE)
  * revoke / approve / grant / token rebinding -> `select_device_for_update` (FOR UPDATE)
Token hashes are written, never read back into application objects that leave this module.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from itertools import count
from typing import Any

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

DEVICE_COLUMNS = """
    device_id, user_id, name, class, fingerprint, os, status, is_admin, token_generation, notes,
    registered_at, approved_at, approved_by_device_id, revoked_at, last_seen_at, expires_at,
    (expires_at IS NOT NULL AND expires_at <= now()) AS expired
"""

ADMIN_PLACEHOLDER_HASH = "reserved:admin"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- devices


async def select_device_by_hash_for_share(conn: AsyncConnection, token_hash: str) -> dict[str, Any] | None:
    """Resolve the calling device under a share lock (serialises against revoke/rebind FOR UPDATE)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DEVICE_COLUMNS} FROM devices WHERE token_sha256 = %s FOR SHARE",
            (token_hash,),
        )
        return await cur.fetchone()


async def select_device_for_update(conn: AsyncConnection, device_id: int) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DEVICE_COLUMNS} FROM devices WHERE device_id = %s FOR UPDATE", (device_id,)
        )
        return await cur.fetchone()


async def select_device(conn: AsyncConnection, device_id: int) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"SELECT {DEVICE_COLUMNS} FROM devices WHERE device_id = %s", (device_id,))
        return await cur.fetchone()


async def list_devices(conn: AsyncConnection, user_id: str) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DEVICE_COLUMNS} FROM devices WHERE user_id = %s ORDER BY device_id", (user_id,)
        )
        return await cur.fetchall()


async def insert_device(
    conn: AsyncConnection,
    *,
    name: str,
    device_class: str,
    fingerprint: str,
    os: str | None,
    token_hash: str,
    user_id: str = "owner",
) -> dict[str, Any]:
    """Create a `pending` device. Unique violations (name / fingerprint) propagate to the caller."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            INSERT INTO devices (user_id, name, class, fingerprint, os, status, token_sha256)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s)
            RETURNING {DEVICE_COLUMNS}
            """,
            (user_id, name, device_class, fingerprint, os, token_hash),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def release_revoked_device_name(conn: AsyncConnection, name: str, *, user_id: str = "owner") -> None:
    """Free a revoked name in the registration transaction; preserve its identity and token.

    The row lock serializes competing registrations. Active names remain protected by
    UNIQUE (user_id, name). Archive names obey the existing 64-character slug CHECK;
    a preoccupied archive name is skipped without changing its owner's row.
    """
    cur = await conn.execute(
        "SELECT device_id FROM devices WHERE user_id = %s AND name = %s AND status = 'revoked' FOR UPDATE",
        (user_id, name),
    )
    row = await cur.fetchone()
    if row is None:
        return
    device_id = int(row[0])
    for attempt in count():
        suffix = f"-revoked-{device_id}" + (f"-{attempt}" if attempt else "")
        archived_name = name[: 64 - len(suffix)] + suffix
        if archived_name == name:
            continue
        try:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE devices SET name = %s WHERE device_id = %s",
                    (archived_name, device_id),
                )
            return
        except UniqueViolation as exc:
            if exc.diag.constraint_name != "devices_user_id_name_key":
                raise


async def set_device_trusted(
    conn: AsyncConnection, device_id: int, *, device_class: str, notes: str | None, approved_by: int
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            UPDATE devices
               SET status = 'trusted', class = %s, notes = COALESCE(%s, notes),
                   approved_at = now(), approved_by_device_id = %s
             WHERE device_id = %s
            RETURNING {DEVICE_COLUMNS}
            """,
            (device_class, notes, approved_by, device_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def set_device_revoked(conn: AsyncConnection, device_id: int) -> dict[str, Any]:
    """status -> revoked, revoked_at, token_generation + 1 (cursors and sessions die with it)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            UPDATE devices
               SET status = 'revoked', revoked_at = now(), token_generation = token_generation + 1
             WHERE device_id = %s
            RETURNING {DEVICE_COLUMNS}
            """,
            (device_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def insert_minted_device(
    conn: AsyncConnection,
    *,
    name: str,
    device_class: str,
    token_hash: str,
    fingerprint: str,
    notes: str | None,
    expires_at: datetime | None,
    approved_by: int = 1,
    user_id: str = "owner",
) -> dict[str, Any]:
    """W0a server-side minting (D-052/D-061): a device born `trusted`, approved by device 1."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            INSERT INTO devices (user_id, name, class, fingerprint, status, token_sha256, notes,
                                 approved_at, approved_by_device_id, expires_at)
            VALUES (%s, %s, %s, %s, 'trusted', %s, %s, now(), %s, %s)
            RETURNING {DEVICE_COLUMNS}
            """,
            (user_id, name, device_class, fingerprint, token_hash, notes, approved_by, expires_at),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def rotate_device_token(
    conn: AsyncConnection, device_id: int, *, token_hash: str, expires_at: datetime | None, keep_expiry: bool
) -> dict[str, Any]:
    """New bearer for an existing device: generation + 1 (old cursors/sessions die), last_seen reset."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            UPDATE devices
               SET token_sha256 = %s, token_generation = token_generation + 1, last_seen_at = NULL,
                   expires_at = CASE WHEN %s THEN expires_at ELSE %s END
             WHERE device_id = %s
            RETURNING {DEVICE_COLUMNS}
            """,
            (token_hash, keep_expiry, expires_at, device_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def select_device_by_name(
    conn: AsyncConnection, name: str, *, user_id: str = "owner"
) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DEVICE_COLUMNS} FROM devices WHERE user_id = %s AND name = %s", (user_id, name)
        )
        return await cur.fetchone()


async def list_all_devices(conn: AsyncConnection) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"SELECT {DEVICE_COLUMNS} FROM devices ORDER BY device_id")
        return await cur.fetchall()


async def bind_admin_token(conn: AsyncConnection, token_hash: str | None) -> int:
    """§2 start-up binding for device 1; returns the new token_generation.

    token_hash set   -> bind it, generation + 1, last_seen_at reset.
    token_hash None  -> placeholder `reserved:admin`, generation + 1 (an earlier hash is never left active).
    """
    await conn.execute("SELECT 1 FROM devices WHERE device_id = 1 FOR UPDATE")
    if token_hash:
        cur = await conn.execute(
            """
            UPDATE devices
               SET token_sha256 = %s, token_generation = token_generation + 1, last_seen_at = NULL
             WHERE device_id = 1
            RETURNING token_generation
            """,
            (token_hash,),
        )
    else:
        cur = await conn.execute(
            """
            UPDATE devices
               SET token_sha256 = %s, token_generation = token_generation + 1
             WHERE device_id = 1
            RETURNING token_generation
            """,
            (ADMIN_PLACEHOLDER_HASH,),
        )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("device 1 is missing: migration 0001 must reserve it before the API starts")
    return int(row[0])


async def admin_state(conn: AsyncConnection) -> dict[str, Any]:
    """For `hlm doctor` / tests: whether device 1 is bound to a real hash, and its generation."""
    cur = await conn.execute(
        "SELECT token_sha256 = %s, token_generation FROM devices WHERE device_id = 1",
        (ADMIN_PLACEHOLDER_HASH,),
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("device 1 is missing")
    return {"disabled": bool(row[0]), "token_generation": int(row[1])}


async def touch_last_seen(conn: AsyncConnection, device_id: int, *, min_interval_s: int = 60) -> None:
    """Refresh `last_seen_at` at most once per `min_interval_s` (§2)."""
    await conn.execute(
        """
        UPDATE devices SET last_seen_at = now()
         WHERE device_id = %s
           AND (last_seen_at IS NULL OR last_seen_at < now() - make_interval(secs => %s))
        """,
        (device_id, min_interval_s),
    )


# --------------------------------------------------------------------------- grants


async def select_active_grants(conn: AsyncConnection, device_id: int) -> list[tuple[int, str]]:
    cur = await conn.execute(
        "SELECT project_id, role FROM device_project_grants WHERE device_id = %s AND revoked_at IS NULL",
        (device_id,),
    )
    return [(int(r[0]), str(r[1])) for r in await cur.fetchall()]


async def list_grants_with_slugs(conn: AsyncConnection, device_id: int) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT g.project_id, p.slug AS project, g.role, g.granted_at, g.granted_by_device_id
              FROM device_project_grants g JOIN projects p USING (project_id)
             WHERE g.device_id = %s AND g.revoked_at IS NULL
             ORDER BY p.slug
            """,
            (device_id,),
        )
        return await cur.fetchall()


async def upsert_grant(
    conn: AsyncConnection, *, device_id: int, project_id: int, role: str, granted_by: int
) -> None:
    """Add (or re-activate / re-role) a grant; PK is (device_id, project_id)."""
    await conn.execute(
        """
        INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (device_id, project_id) DO UPDATE
           SET role = EXCLUDED.role, granted_at = now(),
               granted_by_device_id = EXCLUDED.granted_by_device_id, revoked_at = NULL
        """,
        (device_id, project_id, role, granted_by),
    )


async def revoke_grant(conn: AsyncConnection, *, device_id: int, project_id: int) -> bool:
    cur = await conn.execute(
        """
        UPDATE device_project_grants SET revoked_at = now()
         WHERE device_id = %s AND project_id = %s AND revoked_at IS NULL
        """,
        (device_id, project_id),
    )
    return cur.rowcount == 1


async def revoke_all_grants(conn: AsyncConnection, device_id: int) -> list[int]:
    cur = await conn.execute(
        """
        UPDATE device_project_grants SET revoked_at = now()
         WHERE device_id = %s AND revoked_at IS NULL
        RETURNING project_id
        """,
        (device_id,),
    )
    return [int(r[0]) for r in await cur.fetchall()]


# --------------------------------------------------------------------------- projects


async def select_project_by_slug(conn: AsyncConnection, slug: str) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT project_id, slug, name, card_logical_id, created_at, archived_at"
            " FROM projects WHERE slug = %s",
            (slug,),
        )
        return await cur.fetchone()


async def insert_project(conn: AsyncConnection, *, slug: str, name: str) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO projects (slug, name) VALUES (%s, %s)
            RETURNING project_id, slug, name, card_logical_id, created_at, archived_at
            """,
            (slug, name),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def list_projects(conn: AsyncConnection, *, device_id: int, is_admin: bool) -> list[dict[str, Any]]:
    """Projects visible to the caller: all for device 1, else only those it holds a live grant on."""
    async with conn.cursor(row_factory=dict_row) as cur:
        if is_admin:
            await cur.execute(
                "SELECT project_id, slug, name, card_logical_id, created_at, archived_at, 'admin' AS role"
                " FROM projects ORDER BY slug"
            )
        else:
            await cur.execute(
                """
                SELECT p.project_id, p.slug, p.name, p.card_logical_id, p.created_at, p.archived_at, g.role
                  FROM projects p JOIN device_project_grants g USING (project_id)
                 WHERE g.device_id = %s AND g.revoked_at IS NULL
                 ORDER BY p.slug
                """,
                (device_id,),
            )
        return await cur.fetchall()


# --------------------------------------------------------------------------- events


async def insert_event(
    conn: AsyncConnection,
    *,
    kind: str,
    device_id: int,
    client: str,
    request: dict[str, Any],
    resolved: dict[str, Any],
    project_id: int | None = None,
    request_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    result: dict[str, Any] | None = None,
) -> int:
    """Append one device-level (or project-scoped) event; payload = {"request","resolved"} (§1.1)."""
    request_id = request_id or uuid.uuid4()
    occurred_at = occurred_at or datetime.now(UTC)
    payload = {"request": request, "resolved": resolved}
    cur = await conn.execute(
        """
        INSERT INTO events (project_id, device_id, client, request_id, kind, payload,
                            payload_sha256, occurred_at, result)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
        RETURNING event_id
        """,
        (
            project_id,
            device_id,
            client,
            request_id,
            kind,
            canonical_json(payload),
            sha256_text(canonical_json(request)),
            occurred_at,
            canonical_json(result) if result is not None else None,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])
