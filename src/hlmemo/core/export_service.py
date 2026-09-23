"""``hlm.export``: the client-protocol read behind ``hlm import`` and ``hlm export`` (W1.5).

Not an agent tool: it is dispatched by ``tools/call`` but never advertised on ``tools/list``, so
the agent tool surface and G-SURF are unchanged (CC-4; Sol consult 40 #1, option B). It pages
every item of a project that is live at one frozen bi-temporal point.

* ``view="manifest"`` — ids, kind, title, tags, valid interval, ``source``, ``describes``,
  ``body_sha256``/``body_chars`` (no body): the importer's new/changed/unchanged classification.
* ``view="full"`` — additionally ``body`` and the outgoing ``links``: the Markdown export. A body
  too large for one page is continued by character offset (``body_range``); the client joins it.

Rules (the read path's, §4.4): the ``read`` grant on the project, then (a) project membership +
device scope on every version and link (and each link's far endpoint), and ``temporal_live`` at
the as-of frozen on the first page. Active items only unless ``include_archived``. Keyset order
``(logical_id, version_id)``; the HMAC cursor binds device + generation + the request hash + the
frozen as-of, and every page re-runs the authorization in its own transaction. Bulk export does
NOT record access events (it must not reset idleness, D-012).
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import Field, field_validator

from hlmemo.auth.context import AuthContext, Role
from hlmemo.auth.cursors import sign_cursor, verify_cursor
from hlmemo.auth.errors import HlmError
from hlmemo.core.budget import BudgetError, Meter, canonical, validate_budget
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, default_read_deps
from hlmemo.core.temporal import fmt_ts, parse_opt_ts
from hlmemo.core.write_models import SLUG_RE, Kind, _Strict, parse_request
from hlmemo.db import import_queries as iq
from hlmemo.db import read_queries as rq

TOOL_EXPORT = "hlm.export"
FETCH_MANIFEST = 400
FETCH_FULL = 40


class ExportRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    token_budget: Any
    view: Literal["manifest", "full"] = "manifest"
    kinds: list[Kind] | None = Field(default=None, min_length=1, max_length=7)
    valid_at: str | None = None
    known_at: str | None = None
    include_archived: bool = False
    cursor: str | None = None

    @field_validator("kinds")
    @classmethod
    def _unique(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(set(v)) != len(v):
            raise ValueError("kinds must be unique")
        return v


INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "project": {"type": "string", "pattern": SLUG_RE},
        "token_budget": {"type": "integer", "minimum": 256, "maximum": 32000},
        "view": {"enum": ["manifest", "full"], "default": "manifest"},
        "kinds": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 7},
        "valid_at": {"type": "string", "format": "date-time"},
        "known_at": {"type": "string", "format": "date-time"},
        "include_archived": {"type": "boolean", "default": False},
        "cursor": {"type": "string"},
    },
    "required": ["project", "token_budget"],
    "additionalProperties": False,
}


def _sha(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def _render(row: iq.ExportRow, slugs: dict[int, str], home: int, describes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "logical_id": row.logical_id,
        "version_id": row.version_id,
        "kind": row.kind,
        "status": row.status,
        "title": row.title,
        "tags": list(row.tags),
        "pinned": row.pinned,
        "stability": row.stability,
        "importance": row.importance,
        "device_scope": row.device_scope,
        "also_in": [slugs[p] for p in row.project_ids if p != home and p in slugs],
        "valid_from": fmt_ts(row.valid_from),
        "valid_to": fmt_ts(row.valid_to),
        "source": row.source,
        "describes": describes,
        "body_chars": row.body_chars,
    }
    return out


async def export(
    conn: AsyncConnection,
    ctx: AuthContext,
    req: ExportRequest | dict[str, Any],
    *,
    deps: ReadDeps | None = None,
) -> dict[str, Any]:
    request = parse_request(ExportRequest, req)
    deps = deps or default_read_deps()
    meter: Meter = deps.meter
    try:
        budget = validate_budget(request.token_budget)
    except BudgetError as exc:
        raise ToolError(exc.code, str(exc), **exc.details) from exc
    full = request.view == "full"
    req_hash = _sha(
        {
            "project": request.project,
            "view": request.view,
            "kinds": sorted(request.kinds) if request.kinds else None,
            "archived": request.include_archived,
            "valid_at": request.valid_at,
            "known_at": request.known_at,
        }
    )
    async with conn.transaction():
        project = await rq.resolve_project(conn, request.project)
        if project is None or not ctx.has(project.project_id, Role.READ):
            raise ToolError(
                "E_FORBIDDEN_PROJECT",
                f"no read grant on project {request.project!r}",
                project=request.project,
            )
        pid = project.project_id
        scopes = list(ctx.scope_values())
        pos: tuple[int, int, int] | None = None  # (logical_id, version_id, body offset) to resume at
        if request.cursor is not None:
            try:
                p = verify_cursor(deps.cursor_secret, request.cursor, ctx)
            except HlmError as exc:
                raise ToolError("E_INVALID_CURSOR", exc.message) from exc
            if p.get("tool") != TOOL_EXPORT or p.get("h") != req_hash:
                raise ToolError("E_INVALID_CURSOR", "cursor does not belong to this export")
            raw_pos = p.get("pos")
            if not (
                isinstance(raw_pos, list) and len(raw_pos) == 3 and all(isinstance(x, int) for x in raw_pos)
            ):
                raise ToolError("E_INVALID_CURSOR", "malformed cursor position")
            pos = (raw_pos[0], raw_pos[1], raw_pos[2])
            valid_at = parse_opt_ts(p.get("valid_at"), field="valid_at")
            known_at = parse_opt_ts(p.get("known_at"), field="known_at")
        else:
            now = await rq.clock_now(conn)
            valid_at = parse_opt_ts(request.valid_at, field="valid_at") or now
            known_at = parse_opt_ts(request.known_at, field="known_at") or now
        assert valid_at is not None and known_at is not None
        statuses = ["active", "archived"] if request.include_archived else ["active"]

        envelope: dict[str, Any] = {
            "project": request.project,
            "view": request.view,
            "as_of": {"valid_at": fmt_ts(valid_at), "known_at": fmt_ts(known_at)},
            "items": [],
            "next_cursor": None,
        }

        def cursor_for(at: tuple[int, int, int]) -> str:
            return sign_cursor(
                deps.cursor_secret,
                ctx,
                {
                    "tool": TOOL_EXPORT,
                    "h": req_hash,
                    "pos": list(at),
                    "valid_at": fmt_ts(valid_at),
                    "known_at": fmt_ts(known_at),
                },
            )

        reserve = meter.count(cursor_for((10**15, 10**15, 10**9))) + 8  # the signed cursor, worst case
        fixed = meter.settle(envelope, budget)
        if fixed + reserve > budget:
            raise ToolError(
                "E_BUDGET_TOO_SMALL", f"token_budget {budget} cannot hold a page", min=fixed + reserve
            )
        after = (pos[0], pos[1] - 1) if pos is not None else None  # keyset: resume AT (lid, vid)
        fetch = FETCH_FULL if full else FETCH_MANIFEST
        rows = await iq.export_rows(
            conn,
            pid=pid,
            scopes=scopes,
            valid_at=valid_at,
            known_at=known_at,
            statuses=statuses,
            kinds=list(request.kinds) if request.kinds else None,
            after=after,
            limit=fetch + 1,
            with_body=full,
        )
        more_rows = len(rows) > fetch
        rows = rows[:fetch]
        if pos is not None and rows and (rows[0].logical_id, rows[0].version_id) != (pos[0], pos[1]):
            pos = (rows[0].logical_id, rows[0].version_id, 0)  # the resume item left the snapshot: restart it
        slugs = await rq.project_slugs(conn, sorted({p for r in rows for p in r.project_ids}))
        refs = await iq.code_refs_of(conn, [r.version_id for r in rows])
        links: dict[int, list[dict[str, Any]]] = {}
        if full:
            for ln in await iq.export_links(
                conn,
                pid=pid,
                scopes=scopes,
                valid_at=valid_at,
                known_at=known_at,
                src_logical_ids=sorted({r.logical_id for r in rows}),
            ):
                links.setdefault(ln.src_logical_id, []).append(
                    {"rel": ln.rel, "dst_logical_id": ln.dst_logical_id, "dst_version_id": ln.dst_version_id}
                )

        def rendered(n: int, row: iq.ExportRow, upto: int | None = None) -> dict[str, Any]:
            item = _render(row, slugs, pid, [p for p, _c in refs.get(row.version_id, [])])
            item["body_sha256"] = row.body_sha256
            if full:
                body = row.body or ""
                start = pos[2] if (n == 0 and pos is not None) else 0
                end = len(body) if upto is None else upto
                item["links"] = links.get(row.logical_id, [])
                item["body_range"] = [start, end]
                item["body"] = body[start:end]
            return item

        used = fixed + reserve
        packed: list[dict[str, Any]] = []
        next_pos: tuple[int, int, int] | None = None
        for n, row in enumerate(rows):
            item = rendered(n, row)
            cost = meter.count(item) + 1
            if used + cost <= budget:
                packed.append(item)
                used += cost
                continue
            next_pos = (row.logical_id, row.version_id, 0)
            if full and not packed:  # one oversized body: the largest prefix that fits, continued later
                start = pos[2] if pos is not None else 0
                lo, hi, best = start + 1, len(row.body or ""), None
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if used + meter.count(rendered(n, row, mid)) + 1 <= budget:
                        best, lo = mid, mid + 1
                    else:
                        hi = mid - 1
                if best is not None:
                    packed.append(rendered(n, row, best))
                    next_pos = (row.logical_id, row.version_id, best)
            break
        if next_pos is None and more_rows:
            last = rows[-1]
            next_pos = (last.logical_id, last.version_id + 1, 0)
        if not packed and rows:
            raise ToolError(
                "E_BUDGET_TOO_SMALL",
                f"token_budget {budget} cannot hold one item",
                min=min(32000, budget * 2),
            )
        envelope["items"] = packed
        envelope["next_cursor"] = cursor_for(next_pos) if next_pos is not None else None
        # exact measure (the per-item estimate ignores separators): trim from the end if needed
        while meter.settle(envelope, budget) > budget:
            if len(packed) > 1:
                dropped = packed.pop()
                lid, vid = dropped["logical_id"], dropped["version_id"]
                off = dropped["body_range"][0] if full else 0
                envelope["next_cursor"] = cursor_for((lid, vid, off))
                continue
            only = packed[0]
            if not full or only["body_range"][1] - only["body_range"][0] < 2:
                raise ToolError(
                    "E_BUDGET_TOO_SMALL",
                    f"token_budget {budget} cannot hold one item",
                    min=min(32000, budget * 2),
                )
            a, b = only["body_range"]
            b = a + (b - a) * 9 // 10
            src = next(r for r in rows if r.version_id == only["version_id"])
            packed[0] = rendered(0, src, b)
            envelope["next_cursor"] = cursor_for((only["logical_id"], only["version_id"], b))
    return envelope


__all__ = ["INPUT_SCHEMA", "TOOL_EXPORT", "ExportRequest", "export"]
