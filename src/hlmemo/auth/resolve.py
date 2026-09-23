"""bearer -> AuthContext, inside the request transaction (PHASE0-SPEC §2).

`resolve()` is the ONLY producer of `AuthContext`. It runs `SELECT ... FOR SHARE` on the device row
so that it is serialised against revoke / grant removal / token rebinding (`FOR UPDATE`), loads the
live grants in the same transaction and never caches anything across requests.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.auth.tokens import hash_token
from hlmemo.db import auth_queries as q


async def lock_device_access(conn: AsyncConnection, device_id: int, *, exclusive: bool = False) -> None:
    """Queue revoke ahead of new readers; row-level SHARE alone allows reader barging.

    Namespace 3 is distinct from request/session advisory locks (1/2), and two-key
    advisory locks are distinct from logical-id bigint locks. Hash collisions only
    serialize unrelated devices, never grant access.
    """
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await conn.execute(f"SELECT {function}(3, hashtext(%s))", (str(device_id),))


def context_from_row(row: dict[str, Any], grants: list[tuple[int, str]], client: str) -> AuthContext:
    return AuthContext(
        device_id=int(row["device_id"]),
        device_class=str(row["class"]),
        is_admin=bool(row["is_admin"]),
        token_generation=int(row["token_generation"]),
        grants={pid: Role(role) for pid, role in grants},
        client=client,
    )


async def resolve(
    conn: AsyncConnection,
    bearer: str | None,
    *,
    client: str = "unknown/0",
    allow_pending: bool = False,
    allow_revoked: bool = False,
    exclusive: bool = False,
) -> tuple[AuthContext, dict[str, Any]]:
    """Resolve the calling device under FOR SHARE and load its grants.

    Raises `E_AUTH` for a missing/unknown token and (unless `allow_revoked`) for a revoked device,
    `E_DEVICE_PENDING` for a pending device unless `allow_pending` (only `GET /health` sets it).
    Returns `(AuthContext, device_row)`; the row never contains the token hash. `last_seen_at` is
    NOT touched here: an UPDATE while other requests hold FOR SHARE on the same row would deadlock,
    so the server refreshes it after the request transaction commits.
    """
    if not bearer:
        raise HlmError("E_AUTH", "missing bearer token")
    token_hash = hash_token(bearer)
    cur = await conn.execute("SELECT device_id FROM devices WHERE token_sha256 = %s", (token_hash,))
    identity = await cur.fetchone()
    if identity is None:
        raise HlmError("E_AUTH", "unknown token")
    await lock_device_access(conn, int(identity[0]), exclusive=exclusive)
    # The first lookup identifies only the lock key. Recheck the bearer and status
    # under the row lock after waiting: a concurrent revoke/rebind may have committed.
    row = await q.select_device_by_hash_for_share(conn, token_hash)
    if row is None or int(row["device_id"]) != int(identity[0]):
        raise HlmError("E_AUTH", "unknown token")
    if row.get("expired") and row["status"] != "revoked":
        # W0a (D-061): expired is treated exactly like revoked, here and in the pre-body gate.
        row = {**row, "status": "revoked"}
    status = row["status"]
    if status == "revoked" and row.get("expired") and not allow_revoked:
        raise HlmError("E_AUTH", "device expired")
    if status == "revoked" and not allow_revoked:
        raise HlmError("E_AUTH", "device revoked")
    if status == "pending" and not allow_pending:
        raise HlmError("E_DEVICE_PENDING", "device awaiting approval", {"device_id": int(row["device_id"])})
    grants = await q.select_active_grants(conn, int(row["device_id"])) if status == "trusted" else []
    return context_from_row(row, grants, client), row
