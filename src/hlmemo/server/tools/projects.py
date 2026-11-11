"""``hlm.project_create``: a member of an account creates ``<account>-...`` projects (D-274); a member
of the owner account creates unprefixed ones (D-279).

A client-protocol tool (``CLIENT_TOOLS``): dispatched for the ``hlm`` CLI (``hlm project create``) but
never advertised on ``tools/list``, so the agent tool surface (CC-4, G-SURF) does not grow. The rules
live in ``core/account_service``.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext
from hlmemo.core import account_service

NAME = "hlm.project_create"
DESCRIPTION = (
    "hlm CLI only: create a project in your account (<account>-..., or any free slug for the owner"
    " account), granted to the account's devices."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{1,63}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": account_service.NAME_MAX},
    },
    "required": ["slug"],
    "additionalProperties": False,
}


async def hlm_project_create(conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
    return await account_service.create_project(conn, ctx, args.get("slug"), args.get("name"))
