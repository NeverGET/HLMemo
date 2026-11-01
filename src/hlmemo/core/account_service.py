"""Account self-service project creation (D-274; threat model: docs/consults/127-d274-threat-model.md).

An **account** is a root project ``<account>-global`` whose ``projects.policy`` carries ``account``,
``account_root: true`` and ``project_quota``. Only the operator makes a root (``ops account set``, over
SSH). A device is a **member** while its request context holds a live ``write``/``admin`` grant on the
root: the context is resolved from the bearer inside the request transaction (revoked, expired and
pending devices never get one), never from the payload.

A member creates ``<account>-<rest>`` projects with the unlisted client tool ``hlm.project_create``.
The account is derived from the slug, the root row is locked ``FOR UPDATE`` (the quota check and the
insert are serialized per account), and the new project is tagged ``policy.account``. It is granted
``write`` to every live member and ``admin`` to device 1, like ``ops project create``. An existing
slug of the same account is an idempotent no-op; any other existing slug is refused. No schema change:
the keys live in ``projects.policy``, which replay keeps (only ``REPLAYED_POLICY_KEYS`` are rebuilt).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.core.skeleton_card import write_skeleton_card
from hlmemo.db import auth_queries as q

ACCOUNT_RE = re.compile(r"^[a-z0-9]{2,32}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
RESERVED_ACCOUNTS = frozenset({"hlm", "owner", "admin", "librarian", "mig", "system", "root"})
ROOT_SUFFIX = "-global"
DEFAULT_QUOTA = 20
MAX_QUOTA = 200
OPERATOR_DEVICE_ID = 1
NAME_MAX = 200

#: the one refusal for "no account here / not a member": the same message whatever the cause, so a
#: caller learns nothing about accounts it does not belong to (threat model T6)
_NOT_MEMBER = "project creation needs membership of the account its slug names (<account>-...)"


def account_of(slug: str) -> str | None:
    """``maher-api`` -> ``maher``; None when the slug cannot belong to an account."""
    head, sep, rest = slug.partition("-")
    if not sep or not rest or not ACCOUNT_RE.fullmatch(head) or head in RESERVED_ACCOUNTS:
        return None
    return head


def root_slug(account: str) -> str:
    return f"{account}{ROOT_SUFFIX}"


async def _project_row(conn: AsyncConnection, slug: str, *, lock: bool = False) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT project_id, slug, name, card_logical_id, created_at, archived_at, policy"
            " FROM projects WHERE slug = %s" + (" FOR UPDATE" if lock else ""),
            (slug,),
        )
        return await cur.fetchone()


def is_root(row: dict[str, Any] | None, account: str) -> bool:
    policy = (row or {}).get("policy") or {}
    return (
        row is not None
        and row["slug"] == root_slug(account)
        and row.get("archived_at") is None
        and policy.get("account") == account
        and policy.get("account_root") is True
    )


async def live_members(conn: AsyncConnection, root_id: int) -> list[int]:
    """Devices with a live write/admin grant on the root: trusted, not expired, not device 1 or a
    system actor."""
    cur = await conn.execute(
        """
        SELECT g.device_id
          FROM device_project_grants g JOIN devices d USING (device_id)
         WHERE g.project_id = %s AND g.revoked_at IS NULL AND g.role IN ('write', 'admin')
           AND d.status = 'trusted' AND NOT d.is_admin AND NOT d.is_system
           AND (d.expires_at IS NULL OR d.expires_at > now())
         ORDER BY g.device_id
           FOR SHARE OF g, d
        """,
        (root_id,),
    )
    return [int(r[0]) for r in await cur.fetchall()]


async def count_projects(conn: AsyncConnection, account: str) -> int:
    """The account's projects, its root excluded (what the quota limits)."""
    cur = await conn.execute(
        "SELECT count(*) FROM projects WHERE policy->>'account' = %s AND slug <> %s",
        (account, root_slug(account)),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["project_id"]),
        "slug": row["slug"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def create_project(
    conn: AsyncConnection, ctx: AuthContext, slug: str, name: str | None
) -> dict[str, Any]:
    """``hlm.project_create``: runs in the caller's (request) transaction."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise HlmError("E_INVALID_ARG", "slug must match [a-z0-9][a-z0-9-]{1,63}", {"slug": slug})
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX):
        raise HlmError("E_INVALID_ARG", f"name must be 1..{NAME_MAX} characters", {})
    account = account_of(slug)
    if account is None or ctx.is_admin or slug == root_slug(account):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    # membership first, without a lock: a non-member never locks (or times) another account's root
    root = await _project_row(conn, root_slug(account))
    if not is_root(root, account):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    assert root is not None
    root_id = int(root["project_id"])
    # an explicit grant: device 1's admin bypass never counts
    if ctx.grants.get(root_id) not in (Role.WRITE, Role.ADMIN):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    root = await _project_row(conn, root_slug(account), lock=True)  # serializes the account's creates
    if not is_root(root, account):  # changed while unlocked
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    assert root is not None

    existing = await _project_row(conn, slug, lock=True)
    if existing is not None:
        if (existing.get("policy") or {}).get("account") == account:
            # no grant change and nothing beyond the slug: a member who joined later learns no more
            return {"project": {"slug": slug}, "created": False, "account": account}
        raise HlmError(
            "E_FORBIDDEN",
            "this slug exists and does not belong to your account; ask the operator",
            {"slug": slug},
        )

    quota = int((root.get("policy") or {}).get("project_quota") or DEFAULT_QUOTA)
    used = await count_projects(conn, account)
    if used >= quota:
        raise HlmError(
            "E_FORBIDDEN",
            "the account's project quota is used up; ask the operator",
            {"account": account, "quota": quota, "used": used},
        )

    title = (name or slug).strip()
    row = await q.insert_project(conn, slug=slug, name=title)
    pid = int(row["project_id"])
    await conn.execute(
        "UPDATE projects SET policy = policy || %s WHERE project_id = %s", (Jsonb({"account": account}), pid)
    )
    members = await live_members(conn, root_id)
    await q.insert_event(
        conn,
        kind="project_created",
        project_id=pid,
        device_id=ctx.device_id,
        client=ctx.client,
        request={"slug": slug, "name": title, "via": "account"},
        resolved={"project_id": pid, "card_logical_id": int(row["card_logical_id"]), "account": account},
    )
    grants = [(OPERATOR_DEVICE_ID, "admin"), *((d, "write") for d in members)]
    for device_id, role in grants:
        await q.upsert_grant(conn, device_id=device_id, project_id=pid, role=role, granted_by=ctx.device_id)
        await q.insert_event(
            conn,
            kind="grant_added",
            project_id=pid,
            device_id=ctx.device_id,
            client=ctx.client,
            request_id=uuid.uuid4(),
            request={"device": device_id, "project": slug, "role": role},
            resolved={"device_id": device_id, "project_id": pid, "via": "account_project_create"},
        )
    # D-015: the skeleton card, written by the operator context as `ops project create` does
    card_vid = await write_skeleton_card(
        conn, slug=slug, name=title, card_logical_id=int(row["card_logical_id"]), client=ctx.client
    )
    return {
        "project": _public(row),
        "created": True,
        "account": account,
        "granted": len(members),
        "card_clue": f"v{card_vid}",
        "quota": {"limit": quota, "used": used + 1},
    }


__all__ = [
    "ACCOUNT_RE",
    "DEFAULT_QUOTA",
    "MAX_QUOTA",
    "RESERVED_ACCOUNTS",
    "account_of",
    "count_projects",
    "create_project",
    "is_root",
    "live_members",
    "root_slug",
]
