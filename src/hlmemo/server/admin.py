"""Project administration (PHASE0-SPEC §2 step 4): POST/GET /admin/projects."""

from __future__ import annotations

from psycopg import errors as pgerrors
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from hlmemo.auth.errors import HlmError
from hlmemo.db import auth_queries as q
from hlmemo.server.common import SLUG_RE, auth_of, conn_of, parse_body, project_view
from hlmemo.server.errors import from_db_error


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=SLUG_RE)
    name: str = Field(min_length=1, max_length=200)


async def projects_create(request: Request) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    if not ctx.is_admin:
        raise HlmError("E_FORBIDDEN", "only device 1 may create projects")
    body = await parse_body(request, ProjectBody)
    try:
        async with conn.transaction():
            row = await q.insert_project(conn, slug=body.slug, name=body.name)
    except pgerrors.Error as exc:
        mapped = from_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    pid = int(row["project_id"])
    await q.upsert_grant(
        conn, device_id=ctx.device_id, project_id=pid, role="admin", granted_by=ctx.device_id
    )
    await q.insert_event(
        conn,
        kind="project_created",
        project_id=pid,
        device_id=ctx.device_id,
        client=ctx.client,
        request=body.model_dump(),
        resolved={"project_id": pid, "card_logical_id": int(row["card_logical_id"])},
    )
    await q.insert_event(
        conn,
        kind="grant_added",
        project_id=pid,
        device_id=ctx.device_id,
        client=ctx.client,
        request={"device": ctx.device_id, "project": body.slug, "role": "admin"},
        resolved={"device_id": ctx.device_id, "project_id": pid, "via": "project_create"},
    )
    return JSONResponse({"project": project_view(row)}, status_code=201)


async def projects_list(request: Request) -> JSONResponse:
    ctx = auth_of(request)
    conn = conn_of(request)
    rows = await q.list_projects(conn, device_id=ctx.device_id, is_admin=ctx.is_admin)
    return JSONResponse({"projects": [project_view(r) for r in rows]})
