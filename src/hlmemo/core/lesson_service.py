"""``memory.register_lesson`` (PHASE2-4-ROADMAP W2d; report D.3 #6).

``register_lesson(conn, ctx, args)`` turns ``{project, request_id, mistake, fix, context?, tags?,
device_scope?}`` into ONE ``lesson`` item written through ``write_service.write`` (same
transaction, authorization, idempotency, event, chunks and embed job as ``memory.write``; the
event kind stays ``write``, so no migration and no new replay path). The body is::

    ## Mistake
    <mistake>

    ## Fix
    <fix>

    ## Context            (only when given)
    <context>

The title is the first line of the mistake (≤ ``TITLE_MAX`` characters, cut at a word). The
idempotency key is the sha256 of the tool arguments AS RECEIVED (``raw=``): a retry with the same
arguments replays the stored ack; the same ``request_id`` with other arguments (or already used by
``memory.write``) is ``E_REQUEST_ID_CONFLICT``. ``payload.request`` keeps the verbatim
register_lesson arguments; ``payload.resolved.write.items`` holds the derived lesson item, which is
what replay and ``memory.raw`` read.

Cross-project check hook (W2b wiring point)
-------------------------------------------
The roadmap asks for the librarian's cross-project check of a new lesson at priority 2. The W2b
enqueue does not exist on this branch, so this module exposes ONE isolated hook, awaited inside
the write transaction right after a NEW (non-replayed) lesson was written::

    CROSS_PROJECT_CHECK_HOOK: async (conn, ctx, LessonRegistered) -> bool   # True = enqueued

Wire it at merge (one line, wherever W2b's enqueue lives)::

    lesson_service.CROSS_PROJECT_CHECK_HOOK = enqueue_lesson_check   # LessonRegistered.priority == 2

If W2b's generic trigger already enqueues ``librarian_write:<event_id>`` for every write event
(priority 3), the hook only has to lower that job's priority number to 2 (same dedupe key). The
result's ``cross_project_check`` is ``"queued"`` when the hook returned True, ``"skipped"`` when it
returned False, ``"not_wired"`` while no hook is set and ``"replayed"`` on an idempotent replay.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from pydantic import Field, field_validator

from hlmemo.auth.context import AuthContext
from hlmemo.core import write_service
from hlmemo.core.budget import DEFAULT_WRITE_BUDGET
from hlmemo.core.clues import encode_clue
from hlmemo.core.errors import ToolError
from hlmemo.core.write_models import (
    DEVICE_SCOPE_RE,
    SLUG_RE,
    _Strict,
    canonical_device_scope,
    parse_request,
)
from hlmemo.core.write_service import WriteDeps
from hlmemo.db import write_queries as wq

TOOL = "memory.register_lesson"
TITLE_MAX = 120
PART_MAX = 16000
PRIORITY = 2
CLIENT_FALLBACK = "unknown/0"


class LessonRequest(_Strict):
    project: str = Field(pattern=SLUG_RE)
    request_id: str
    mistake: str = Field(min_length=1, max_length=PART_MAX)
    fix: str = Field(min_length=1, max_length=PART_MAX)
    context: str | None = Field(default=None, min_length=1, max_length=PART_MAX)
    tags: list[str] = Field(default_factory=list, max_length=32)
    device_scope: str = Field(default="all", pattern=DEVICE_SCOPE_RE)

    _device_scope = field_validator("device_scope")(canonical_device_scope)

    @field_validator("request_id")
    @classmethod
    def _uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))

    @field_validator("mistake", "fix", "context")
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("must not be blank")
        return v


@dataclass(frozen=True, slots=True)
class LessonRegistered:
    event_id: int
    project_id: int
    logical_id: int
    version_id: int
    priority: int = PRIORITY


LessonHook = Callable[[AsyncConnection, AuthContext, LessonRegistered], Awaitable[bool]]
#: W2b wiring point (see the module docstring). None until the orchestrator wires it at merge.
CROSS_PROJECT_CHECK_HOOK: LessonHook | None = None


def lesson_title(mistake: str) -> str:
    first = next((ln.strip() for ln in mistake.strip().splitlines() if ln.strip()), "")
    first = " ".join(first.split())
    if len(first) <= TITLE_MAX:
        return first
    cut = first[: TITLE_MAX - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space >= TITLE_MAX // 2 else cut).rstrip(" ,;:.") + "…"


def lesson_body(mistake: str, fix: str, context: str | None) -> str:
    parts = [f"## Mistake\n{mistake.strip()}", f"## Fix\n{fix.strip()}"]
    if context is not None and context.strip():
        parts.append(f"## Context\n{context.strip()}")
    return "\n\n".join(parts)


def lesson_item(request: LessonRequest) -> dict[str, Any]:
    return {
        "kind": "lesson",
        "title": lesson_title(request.mistake),
        "body": lesson_body(request.mistake, request.fix, request.context),
        "tags": list(dict.fromkeys(request.tags)),
        "device_scope": request.device_scope,
        "stability": "stable",
    }


async def register_lesson(
    conn: AsyncConnection,
    ctx: AuthContext,
    args: dict[str, Any],
    *,
    deps: WriteDeps | None = None,
) -> dict[str, Any]:
    request = parse_request(LessonRequest, args)
    deps = deps or write_service.default_deps()
    write_args = {
        "project": request.project,
        "request_id": request.request_id,
        "client": (ctx.client or CLIENT_FALLBACK)[:200],
        "items": [lesson_item(request)],
        "token_budget": DEFAULT_WRITE_BUDGET,
    }
    async with conn.transaction():
        # raw = the verbatim register_lesson arguments: they are the idempotency key and the
        # event's payload.request (write_service authorizes, locks and dedupes exactly as for writes)
        ack = await write_service.write(conn, ctx, write_args, deps=deps, raw=dict(args))
        v = ack.versions[0]
        hook = CROSS_PROJECT_CHECK_HOOK
        if ack.replayed:
            check = "replayed"
        elif hook is None:
            check = "not_wired"
        else:
            ref = (await wq.resolve_projects(conn, [request.project]))[request.project]
            ev = await wq.find_event(conn, ref.project_id, ctx.device_id, request.request_id)
            if ev is None:  # cannot happen inside the write transaction; belt and braces
                raise ToolError("E_UNAVAILABLE", "lesson event not found after write")
            queued = await hook(
                conn, ctx, LessonRegistered(ev.event_id, ref.project_id, v.logical_id, v.version_id)
            )
            check = "queued" if queued else "skipped"
    result: dict[str, Any] = {
        "request_id": ack.request_id,
        "replayed": ack.replayed,
        "clue": encode_clue(v.version_id),
        "logical_id": v.logical_id,
        "version_id": v.version_id,
        "embedding_status": v.embedding_status,
        "cross_project_check": check,
    }
    deps.meter.settle(result, DEFAULT_WRITE_BUDGET)
    return result


__all__ = [
    "CROSS_PROJECT_CHECK_HOOK",
    "PRIORITY",
    "TOOL",
    "LessonHook",
    "LessonRegistered",
    "LessonRequest",
    "lesson_body",
    "lesson_item",
    "lesson_title",
    "register_lesson",
]
