"""The D-015 skeleton project card (carried item; W1.5 / D-069).

D-015: the L3 project card exists from day one — a deterministic skeleton written at project
creation, later replaced by the client LLM through ``memory.write(kind=project_card)`` or
``memory.call_the_day(card_update)``. The skeleton is an ordinary ``write`` event by the operator
device (device 1) through ``core/write_service``, so replay (G6), ``memory.raw`` provenance and the
card's optimistic concurrency (``expected_version_id`` = the skeleton's version) work unchanged.

Three callers, one code path: ``python -m hlmemo.ops project create`` and ``POST /admin/projects``
(same transaction as ``project_created``), and migration ``0007_import``'s backfill for projects
created before it (one short transaction per project, after the DDL committed).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext

log = logging.getLogger("hlmemo.skeleton_card")

#: Namespace of the deterministic skeleton request ids (never change: re-sends must replay).
NS_SKELETON = uuid.UUID("5d3b1f7e-0c4a-4c55-9a07-2d1e6f0b7a31")
OPERATOR_DEVICE_ID = 1
CARD_TITLE = "Project card"
SKELETON_TAG = "skeleton-card"
NAME_MAX = 120  # the card cap is 512 o200k tokens; a long project name must never exceed it
BACKFILL_CLIENT = "hlm-migrate/0007"


def skeleton_body(slug: str, name: str) -> str:
    """Deterministic in (slug, name) only — no clock, no counts."""
    shown = name if len(name) <= NAME_MAX else name[: NAME_MAX - 1] + "…"
    return (
        f"# {shown}\n\n"
        f"Skeleton card (D-015) of project `{slug}`: no summary yet. Replace it with "
        "memory.write(kind=project_card) or memory.call_the_day(card_update) once the project's "
        "purpose, architecture and conventions are known.\n"
    )


def skeleton_request_id(slug: str, card_logical_id: int) -> str:
    return str(uuid.uuid5(NS_SKELETON, f"skeleton-card:{slug}:{card_logical_id}"))


def skeleton_request(slug: str, name: str, card_logical_id: int, client: str) -> dict[str, Any]:
    """The verbatim ``memory.write`` arguments of the skeleton card (hashed as-is, §3)."""
    return {
        "project": slug,
        "request_id": skeleton_request_id(slug, card_logical_id),
        "client": (client or "unknown/0")[:200],
        "items": [
            {
                "kind": "project_card",
                "title": CARD_TITLE,
                "body": skeleton_body(slug, name),
                "tags": [SKELETON_TAG],
                "pinned": True,
                "stability": "stable",
            }
        ],
    }


def operator_context(client: str) -> AuthContext:
    """Device 1 acting (the ops path and the migration); ``is_admin`` bypasses grants only."""
    return AuthContext(
        device_id=OPERATOR_DEVICE_ID, device_class="server", is_admin=True, token_generation=0, client=client
    )


async def write_skeleton_card(
    conn: AsyncConnection,
    *,
    slug: str,
    name: str,
    card_logical_id: int,
    client: str,
    ctx: AuthContext | None = None,
    deps: Any = None,
) -> int:
    """Write the skeleton card inside the caller's transaction; returns its version id."""
    from hlmemo.core.write_service import write

    ack = await write(
        conn,
        ctx or operator_context(client),
        skeleton_request(slug, name, card_logical_id, client),
        deps=deps,
    )
    return int(ack.versions[0].version_id)


_MISSING = """
SELECT p.project_id, p.slug, p.name, p.card_logical_id
  FROM projects p
 WHERE p.archived_at IS NULL
   AND COALESCE(p.policy ->> 'reserved', 'false') <> 'true'
   AND NOT EXISTS (SELECT 1 FROM memory_versions mv WHERE mv.logical_id = p.card_logical_id)
 ORDER BY p.project_id
"""


async def backfill_skeleton_cards(dsn: str, *, deps: Any = None) -> int:
    """Migration 0007: a skeleton card for every live project without any card version.

    One transaction per project; the project row is locked and the condition re-checked inside it,
    so a re-run (or a concurrent run) never writes a second card. Skipped: archived projects (the
    write path refuses them) and the reserved system projects of 0006 (``policy.reserved``:
    ``hlm-librarian``/``hlm-global`` are not user projects, and skipping them keeps a fresh
    database's migration free of the tokenizer). Returns the number written.
    """
    written = 0
    async with await AsyncConnection.connect(dsn, autocommit=False) as conn:
        await conn.execute("SET TIME ZONE 'UTC'")
        cur = await conn.execute(_MISSING)
        todo = await cur.fetchall()
        await conn.commit()
        if not todo:
            return 0
        if deps is None:
            from hlmemo.core.write_service import default_deps

            deps = default_deps()  # e5 tokenizer + o200k meter: only needed when a card is missing
        for project_id, slug, name, card_lid in todo:
            cur = await conn.execute(
                "SELECT 1 FROM projects p WHERE p.project_id = %s AND p.archived_at IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM memory_versions mv WHERE mv.logical_id = p.card_logical_id)"
                " FOR UPDATE OF p",
                (project_id,),
            )
            if await cur.fetchone() is None:
                await conn.rollback()
                continue
            await write_skeleton_card(
                conn, slug=slug, name=name, card_logical_id=card_lid, client=BACKFILL_CLIENT, deps=deps
            )
            await conn.commit()
            written += 1
    if written:
        log.warning("0007_import: wrote %s skeleton project card(s)", written)
    return written


__all__ = [
    "BACKFILL_CLIENT",
    "CARD_TITLE",
    "NS_SKELETON",
    "SKELETON_TAG",
    "backfill_skeleton_cards",
    "operator_context",
    "skeleton_body",
    "skeleton_request",
    "skeleton_request_id",
    "write_skeleton_card",
]
