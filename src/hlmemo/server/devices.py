"""Device onboarding + grants (PHASE0-SPEC §2 "Device onboarding flow").

Routes (both spellings share one implementation):
  POST /devices/register                      {name, class?, fingerprint, os?, client}
  POST /devices/approve  | POST /admin/devices/{id}/approve   {id?, class, notes?, grants?:[{project,role}]}
  POST /devices/revoke   | POST /admin/devices/{id}/revoke    {id?}
  POST /devices/grant    | POST /admin/projects/{slug}/grants {device, project?, role}
  DELETE /devices/grant  | DELETE /admin/projects/{slug}/grants {device, project?}
  GET  /devices/list, GET /devices/whoami
"""

from __future__ import annotations

from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg import errors as pgerrors
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.errors import HlmError
from hlmemo.auth.tokens import generate_token, hash_token
from hlmemo.db import auth_queries as q
from hlmemo.server.common import (
    DEVICE_CLASSES,
    ROLES,
    SLUG_RE,
    auth_of,
    conn_of,
    device_view,
    parse_body,
)
from hlmemo.server.errors import from_db_error, invalid_arg

DeviceClass = Literal["personal", "work", "server", "ci", "other"]
RoleName = Literal["read", "write", "admin"]


class RegisterBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str = Field(pattern=SLUG_RE)
    device_class: DeviceClass = Field(default="other", alias="class")
    fingerprint: str = Field(min_length=1, max_length=512)
    os: str | None = Field(default=None, max_length=256)
    client: str = Field(min_length=1, max_length=128)


class GrantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(pattern=SLUG_RE)
    role: RoleName


class ApproveBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    id: int | None = None
    device_class: DeviceClass = Field(alias="class")
    notes: str | None = Field(default=None, max_length=2000)
    grants: list[GrantSpec] = Field(default_factory=list, max_length=64)


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int | None = None


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: int
    project: str | None = Field(default=None, pattern=SLUG_RE)
    role: RoleName


class UngrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: int
    project: str | None = Field(default=None, pattern=SLUG_RE)


assert set(DeviceClass.__args__) == set(DEVICE_CLASSES) and set(RoleName.__args__) == set(ROLES)


# --------------------------------------------------------------------------- register


async def register(request: Request) -> JSONResponse:
    app_state = request.app.state
    limiter = getattr(app_state, "register_limiter", None)
    if limiter is not None:
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HlmError("E_RATE_LIMITED", "too many registrations from this address; retry in a minute")
    secret = app_state.settings.registration_secret
    if secret is not None:
        presented = request.headers.get("x-hlm-registration-secret", "")
        from hlmemo.auth.tokens import constant_time_equal

        if not presented or not constant_time_equal(presented, secret.get_secret_value()):
            raise HlmError("E_AUTH", "registration secret missing or wrong")
    body = await parse_body(request, RegisterBody)
    if body.name == "admin" or body.fingerprint == q.ADMIN_PLACEHOLDER_HASH:
        raise invalid_arg("reserved device name / fingerprint", field="name")
    conn = conn_of(request)
    token = generate_token()
    try:
        async with conn.transaction():  # savepoint: a unique violation must not poison the request tx
            row = await q.insert_device(
                conn,
                name=body.name,
                device_class=body.device_class,
                fingerprint=body.fingerprint,
                os=body.os,
                token_hash=hash_token(token),
            )
    except pgerrors.Error as exc:
        mapped = from_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    request_dump = body.model_dump(by_alias=True)
    await q.insert_event(
        conn,
        kind="device_registered",
        device_id=int(row["device_id"]),
        client=body.client,
        request=request_dump,
        resolved={"device_id": int(row["device_id"]), "status": "pending"},
    )
    return JSONResponse({"device": device_view(row), "token": token}, status_code=201)


# --------------------------------------------------------------------------- approve


async def _resolve_grant_specs(
    conn: AsyncConnection, ctx: AuthContext, specs: list[GrantSpec]
) -> list[dict[str, Any]]:
    """Exactly the grant-endpoint rule (§2 step 2): caller must be project admin (or device 1) on each."""
    seen: set[str] = set()
    for spec in specs:
        if spec.project in seen:
            raise invalid_arg("duplicate project in grants[]", project=spec.project)
        seen.add(spec.project)
    resolved: list[dict[str, Any]] = []
    for spec in specs:
        project = await q.select_project_by_slug(conn, spec.project)
        if project is None or not ctx.has(int(project["project_id"]), Role.ADMIN):
            raise HlmError("E_FORBIDDEN_PROJECT", "no admin grant on project", {"project": spec.project})
        resolved.append(
            {"project": spec.project, "project_id": int(project["project_id"]), "role": spec.role}
        )
    return resolved


async def _approve(request: Request, target_id: int, body: ApproveBody) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    caller = request.state.device
    if target_id == 1:
        raise HlmError("E_FORBIDDEN", "device 1 is reserved")
    grants = await _resolve_grant_specs(conn, ctx, body.grants)
    target = await q.select_device_for_update(conn, target_id)
    if target is None:
        raise HlmError("E_NOT_FOUND", "device not found", {"id": target_id})
    if not ctx.is_admin and target["user_id"] != caller["user_id"]:
        raise HlmError("E_FORBIDDEN", "device belongs to another user")
    if target["status"] != "pending":
        raise invalid_arg(f"device is {target['status']}, only pending devices can be approved", id=target_id)
    row = await q.set_device_trusted(
        conn, target_id, device_class=body.device_class, notes=body.notes, approved_by=ctx.device_id
    )
    req = body.model_dump(by_alias=True, exclude_none=True)
    req["id"] = target_id
    await q.insert_event(
        conn,
        kind="device_approved",
        device_id=ctx.device_id,
        client=ctx.client,
        request=req,
        resolved={"device_id": target_id, "approved_by_device_id": ctx.device_id, "grants": grants},
    )
    for g in grants:
        await q.upsert_grant(
            conn, device_id=target_id, project_id=g["project_id"], role=g["role"], granted_by=ctx.device_id
        )
        await q.insert_event(
            conn,
            kind="grant_added",
            project_id=g["project_id"],
            device_id=ctx.device_id,
            client=ctx.client,
            request={"device": target_id, "project": g["project"], "role": g["role"]},
            resolved={"device_id": target_id, "project_id": g["project_id"], "via": "approve"},
        )
    return JSONResponse(
        {"device": device_view(row), "grants": [{"project": g["project"], "role": g["role"]} for g in grants]}
    )


async def approve(request: Request) -> JSONResponse:
    body = await parse_body(request, ApproveBody)
    if "id" in request.path_params:
        from hlmemo.server.common import path_int

        target_id = path_int(request, "id")
    elif body.id is not None:
        target_id = body.id
    else:
        raise invalid_arg("missing device id")
    return await _approve(request, target_id, body)


# --------------------------------------------------------------------------- revoke


async def _revoke(request: Request, target_id: int) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    if target_id == 1:
        raise HlmError("E_FORBIDDEN", "device 1 is reserved")
    if not (ctx.is_admin or target_id == ctx.device_id):
        raise HlmError("E_FORBIDDEN", "only device 1 or the device itself may revoke")
    target = await q.select_device_for_update(conn, target_id)
    if target is None:
        raise HlmError("E_NOT_FOUND", "device not found", {"id": target_id})
    if target["status"] == "revoked":
        raise invalid_arg("device already revoked", id=target_id)
    row = await q.set_device_revoked(conn, target_id)
    revoked_projects = await q.revoke_all_grants(conn, target_id)
    await q.insert_event(
        conn,
        kind="device_revoked",
        device_id=ctx.device_id,
        client=ctx.client,
        request={"id": target_id},
        resolved={
            "device_id": target_id,
            "token_generation": int(row["token_generation"]),
            "revoked_project_ids": revoked_projects,
        },
    )
    for pid in revoked_projects:
        await q.insert_event(
            conn,
            kind="grant_revoked",
            project_id=pid,
            device_id=ctx.device_id,
            client=ctx.client,
            request={"device": target_id, "project_id": pid},
            resolved={"device_id": target_id, "project_id": pid, "via": "revoke"},
        )
    return JSONResponse({"device": device_view(row), "revoked_grants": len(revoked_projects)})


async def revoke(request: Request) -> JSONResponse:
    if "id" in request.path_params:
        from hlmemo.server.common import path_int

        target_id = path_int(request, "id")
    else:
        body = await parse_body(request, RevokeBody)
        if body.id is None:
            raise invalid_arg("missing device id")
        target_id = body.id
    return await _revoke(request, target_id)


# --------------------------------------------------------------------------- grants


async def _authorized_project(conn: AsyncConnection, ctx: AuthContext, slug: str | None) -> dict[str, Any]:
    if slug is None:
        raise invalid_arg("missing project")
    project = await q.select_project_by_slug(conn, slug)
    if project is None or not ctx.has(int(project["project_id"]), Role.ADMIN):
        raise HlmError("E_FORBIDDEN_PROJECT", "no admin grant on project", {"project": slug})
    return project


async def grant_add(request: Request) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    body = await parse_body(request, GrantBody)
    slug = request.path_params.get("slug", body.project)
    project = await _authorized_project(conn, ctx, slug)
    pid = int(project["project_id"])
    if body.device == 1:
        raise HlmError("E_FORBIDDEN", "device 1 is reserved")
    target = await q.select_device_for_update(conn, body.device)
    if target is None:
        raise HlmError("E_NOT_FOUND", "device not found", {"id": body.device})
    if target["status"] == "revoked":
        raise invalid_arg("device is revoked", id=body.device)
    await q.upsert_grant(
        conn, device_id=body.device, project_id=pid, role=body.role, granted_by=ctx.device_id
    )
    await q.insert_event(
        conn,
        kind="grant_added",
        project_id=pid,
        device_id=ctx.device_id,
        client=ctx.client,
        request={"device": body.device, "project": slug, "role": body.role},
        resolved={"device_id": body.device, "project_id": pid},
    )
    return JSONResponse({"grant": {"device": body.device, "project": slug, "role": body.role}})


async def grant_remove(request: Request) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    body = await parse_body(request, UngrantBody)
    slug = request.path_params.get("slug", body.project)
    project = await _authorized_project(conn, ctx, slug)
    pid = int(project["project_id"])
    if body.device == 1:
        raise HlmError("E_FORBIDDEN", "device 1 is reserved")
    target = await q.select_device_for_update(conn, body.device)
    if target is None:
        raise HlmError("E_NOT_FOUND", "device not found", {"id": body.device})
    if not await q.revoke_grant(conn, device_id=body.device, project_id=pid):
        raise HlmError("E_NOT_FOUND", "no active grant", {"device": body.device, "project": slug})
    await q.insert_event(
        conn,
        kind="grant_revoked",
        project_id=pid,
        device_id=ctx.device_id,
        client=ctx.client,
        request={"device": body.device, "project": slug},
        resolved={"device_id": body.device, "project_id": pid},
    )
    return JSONResponse({"revoked": {"device": body.device, "project": slug}})


# --------------------------------------------------------------------------- read-only


async def list_devices(request: Request) -> JSONResponse:
    auth_of(request)
    conn = conn_of(request)
    rows = await q.list_devices(conn, request.state.device["user_id"])
    return JSONResponse({"devices": [device_view(r) for r in rows]})


async def whoami(request: Request) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    grants = await q.list_grants_with_slugs(conn, ctx.device_id)
    return JSONResponse(
        {
            "device": device_view(request.state.device),
            "grants": [
                {"project": g["project"], "project_id": int(g["project_id"]), "role": g["role"]}
                for g in grants
            ],
            "scope": list(ctx.scope_values()),
            "client": ctx.client,
        }
    )
