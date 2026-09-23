"""Helpers shared by the /devices and /admin route modules."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ValidationError
from starlette.requests import Request

from hlmemo.auth.context import AuthContext
from hlmemo.auth.errors import HlmError
from hlmemo.server.errors import from_validation_error, invalid_arg

SLUG_RE = r"^[a-z0-9][a-z0-9-]{1,63}$"
DEVICE_CLASSES = ("personal", "work", "server", "ci", "other")
ROLES = ("read", "write", "admin")


async def parse_body[M: BaseModel](request: Request, model: type[M]) -> M:
    raw = await request.body()
    if not raw:
        data: Any = {}
    else:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise invalid_arg("request body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise invalid_arg("request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise from_validation_error(exc) from exc


def auth_of(request: Request) -> AuthContext:
    ctx = request.state.auth
    if ctx is None:  # the middleware guarantees this for every gated route; defensive only
        raise HlmError("E_AUTH", "missing bearer token")
    return ctx


def conn_of(request: Request) -> AsyncConnection:
    conn = request.state.conn
    assert conn is not None, "AuthMiddleware must run before any route"
    return conn


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def device_view(row: dict[str, Any]) -> dict[str, Any]:
    """Public projection of a devices row (never the token hash)."""
    return {
        "id": int(row["device_id"]),
        "name": row["name"],
        "class": row["class"],
        "status": row["status"],
        "is_admin": bool(row["is_admin"]),
        "reserved": int(row["device_id"]) == 1,
        "os": row.get("os"),
        "token_generation": int(row["token_generation"]),
        "registered_at": iso(row.get("registered_at")),
        "approved_at": iso(row.get("approved_at")),
        "approved_by_device_id": row.get("approved_by_device_id"),
        "revoked_at": iso(row.get("revoked_at")),
        "last_seen_at": iso(row.get("last_seen_at")),
        "expires_at": iso(row.get("expires_at")),
        "expired": bool(row.get("expired")),
        "notes": row.get("notes"),
    }


def project_view(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": int(row["project_id"]),
        "slug": row["slug"],
        "name": row["name"],
        "card_logical_id": int(row["card_logical_id"]),
        "created_at": iso(row.get("created_at")),
        "archived_at": iso(row.get("archived_at")),
    }
    if "role" in row:
        out["role"] = row["role"]
    return out


def path_int(request: Request, name: str) -> int:
    raw = request.path_params.get(name)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise invalid_arg(f"{name} must be an integer", value=raw) from exc
