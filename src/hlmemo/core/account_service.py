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

The **owner account** (D-279; threat model: docs/consults/130-d279-threat-model.md) lets the owner's own
devices create projects without a prefix. Its root is the project ``owner-global`` with
``policy.owner_root: true`` (``ops account set owner``); members are the devices with a live
``write``/``admin`` grant on it. A slug that is not in an account's namespace (no ``<account>-global`` row
exists for its prefix) takes the owner path: a member creates it, tagged ``policy.owner_created: true``
for the quota and with **no** ``policy.account``, so it is an ordinary default-group project like one
made by ``ops project create`` (the isolation code never learns about it). Everyone else gets the same
single refusal as on the account path.
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
#: D-279: the owner's own account. Reserved as an account name, so no slug prefix maps to it.
OWNER_ACCOUNT = "owner"
OWNER_ROOT = OWNER_ACCOUNT + ROOT_SUFFIX
OWNER_ROOT_KEY = "owner_root"  # on the root's policy; only `ops account set owner` sets it
OWNER_CREATED_KEY = "owner_created"  # on a project made through the owner path; the quota counts it
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


def namespace_of(slug: str) -> str | None:
    """The account whose namespace ``slug`` would lie in: its first segment, when the slug has a
    hyphen and that segment is a valid, unreserved account name. Wider than ``account_of``: ``maher-``
    (an empty rest) is in ``maher``'s namespace too, as ``ops account set`` reads it (``starts_with``)."""
    head, sep, _rest = slug.partition("-")
    if not sep or not ACCOUNT_RE.fullmatch(head) or head in RESERVED_ACCOUNTS:
        return None
    return head


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


def is_owner_root(row: dict[str, Any] | None) -> bool:
    """The D-279 owner root: the project ``owner-global``, live, flagged by the operator."""
    policy = (row or {}).get("policy") or {}
    return (
        row is not None
        and row["slug"] == OWNER_ROOT
        and row.get("archived_at") is None
        and policy.get(OWNER_ROOT_KEY) is True
    )


def owner_slug_refusal(slug: str) -> str | None:
    """Why an owner member may not create ``slug`` (None = allowed): the system namespace and roots
    stay the operator's."""
    head = slug.partition("-")[0]
    if head in RESERVED_ACCOUNTS:  # hlm-global, hlm-librarian and every other system slug start with one
        return "this slug is in the reserved system namespace"
    if slug.endswith("-"):
        return "a slug must not end with a hyphen"
    if slug.endswith(ROOT_SUFFIX):
        return "a slug ending in -global names an account root; the operator makes those"
    return None


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


async def count_owner_projects(conn: AsyncConnection) -> int:
    """The projects made through the owner path (what the owner account's quota limits)."""
    cur = await conn.execute("SELECT count(*) FROM projects WHERE policy->>%s = 'true'", (OWNER_CREATED_KEY,))
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
    """``hlm.project_create``: runs in the caller's (request) transaction.

    A slug in an account's namespace (``<account>-<rest>`` with a project row ``<account>-global``)
    takes the D-274 account path; any other slug takes the D-279 owner path. Both refuse a non-member
    with the same error, before any lock."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise HlmError("E_INVALID_ARG", "slug must match [a-z0-9][a-z0-9-]{1,63}", {"slug": slug})
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX):
        raise HlmError("E_INVALID_ARG", f"name must be 1..{NAME_MAX} characters", {})
    if ctx.is_admin:
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    # Both roots are read before any decision and without a lock, for every caller alike: a non-member
    # never locks a root, and the number of reads depends on the slug's shape only, never on whether
    # an account of that name exists.
    space = namespace_of(slug)
    root = None
    if space is not None and slug != root_slug(space):
        root = await _project_row(conn, root_slug(space))
    owner_root = await _project_row(conn, OWNER_ROOT)
    if root is not None:  # the prefix is an account's namespace, whatever the state of its root
        assert space is not None
        if account_of(slug) != space:  # `<account>-` with nothing after it: no account creates that
            raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
        return await _create_in_account(conn, ctx, slug, name, space, root)
    return await _create_as_owner(conn, ctx, slug, name, owner_root)


async def _create_in_account(
    conn: AsyncConnection,
    ctx: AuthContext,
    slug: str,
    name: str | None,
    account: str,
    root: dict[str, Any],
) -> dict[str, Any]:
    """D-274: a member of ``account`` creates ``<account>-<rest>``, tagged ``policy.account``."""
    if not is_root(root, account):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    root_id = int(root["project_id"])
    # an explicit grant: device 1's admin bypass never counts
    if ctx.grants.get(root_id) not in (Role.WRITE, Role.ADMIN):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    locked = await _project_row(conn, root_slug(account), lock=True)  # serializes the account's creates
    if not is_root(locked, account):  # changed while unlocked
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    assert locked is not None

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

    quota = int((locked.get("policy") or {}).get("project_quota") or DEFAULT_QUOTA)
    used = await count_projects(conn, account)
    return await _insert_and_grant(
        conn,
        ctx,
        slug,
        name,
        root_id=root_id,
        account=account,
        tag={"account": account},
        quota=quota,
        used=used,
    )


async def _create_as_owner(
    conn: AsyncConnection, ctx: AuthContext, slug: str, name: str | None, root: dict[str, Any] | None
) -> dict[str, Any]:
    """D-279: a member of the owner account creates a slug outside every account's namespace. The
    project carries ``owner_created`` and no ``account`` tag: an ordinary default-group project."""
    if not is_owner_root(root):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    assert root is not None
    root_id = int(root["project_id"])
    # an explicit grant: device 1's admin bypass never counts
    if ctx.grants.get(root_id) not in (Role.WRITE, Role.ADMIN):
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    # a member from here on: the slug rules may say why
    refusal = owner_slug_refusal(slug)
    if refusal is not None:
        raise HlmError("E_FORBIDDEN", f"{refusal}; ask the operator", {"slug": slug})
    locked = await _project_row(conn, OWNER_ROOT, lock=True)  # serializes the owner account's creates
    if not is_owner_root(locked):  # changed while unlocked
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})
    assert locked is not None
    space = namespace_of(slug)
    if space is not None and await _project_row(conn, root_slug(space)) is not None:
        # an account root appeared for this prefix while unlocked: the slug is in its namespace now
        raise HlmError("E_FORBIDDEN", _NOT_MEMBER, {"slug": slug})

    existing = await _project_row(conn, slug, lock=True)
    if existing is not None:
        if (existing.get("policy") or {}).get(OWNER_CREATED_KEY) is True:
            return {"project": {"slug": slug}, "created": False, "account": OWNER_ACCOUNT}
        raise HlmError(
            "E_FORBIDDEN",
            "this slug exists and was not created through the owner account; ask the operator",
            {"slug": slug},
        )
    # A root the operator commits between the re-read above and this transaction's commit is not seen
    # by either side (review 130 C2): the project then lies under a live account, untagged, and a later
    # `ops account set` for that account stops on it until the operator clears `owner_created` by hand.
    quota = int((locked.get("policy") or {}).get("project_quota") or DEFAULT_QUOTA)
    used = await count_owner_projects(conn)
    return await _insert_and_grant(
        conn,
        ctx,
        slug,
        name,
        root_id=root_id,
        account=OWNER_ACCOUNT,
        tag={OWNER_CREATED_KEY: True},
        quota=quota,
        used=used,
    )


async def _insert_and_grant(
    conn: AsyncConnection,
    ctx: AuthContext,
    slug: str,
    name: str | None,
    *,
    root_id: int,
    account: str,
    tag: dict[str, Any],
    quota: int,
    used: int,
) -> dict[str, Any]:
    """The shared tail, under the root lock: the quota, the row with its tag, the events, the grants
    (device 1 ``admin``, every live member ``write``) and the skeleton card."""
    if used >= quota:
        raise HlmError(
            "E_FORBIDDEN",
            "the account's project quota is used up; ask the operator",
            {"account": account, "quota": quota, "used": used},
        )

    title = (name or slug).strip()
    row = await q.insert_project(conn, slug=slug, name=title)
    pid = int(row["project_id"])
    await conn.execute("UPDATE projects SET policy = policy || %s WHERE project_id = %s", (Jsonb(tag), pid))
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
    "OWNER_ACCOUNT",
    "OWNER_CREATED_KEY",
    "OWNER_ROOT",
    "OWNER_ROOT_KEY",
    "RESERVED_ACCOUNTS",
    "account_of",
    "count_owner_projects",
    "count_projects",
    "create_project",
    "is_owner_root",
    "is_root",
    "live_members",
    "namespace_of",
    "owner_slug_refusal",
    "root_slug",
]
