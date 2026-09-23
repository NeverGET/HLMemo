"""Reserved rows created by migration ``0006_librarian`` (CC-3, W2a).

``ensure_reserved_rows`` re-creates them idempotently with the same values as the migration; the
test suite needs it because its per-test truncation removes every row except device 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import AsyncConnection

LIBRARIAN_DEVICE = "librarian"
LIBRARIAN_TOKEN_PLACEHOLDER = "reserved:librarian"
MEMORY_PROJECT = "hlm-librarian"
GLOBAL_PROJECT = "hlm-global"
RESERVED_PROJECTS = (MEMORY_PROJECT, GLOBAL_PROJECT)

_ENSURE = r"""
INSERT INTO devices (user_id, name, class, fingerprint, os, status, is_admin, is_system,
                     token_sha256, notes, approved_at, approved_by_device_id)
VALUES ('owner', 'librarian', 'server', 'reserved:librarian', NULL, 'trusted', false, true,
        'reserved:librarian', 'reserved by migration 0006: librarian system actor, no usable bearer (CC-3)',
        now(), 1)
ON CONFLICT DO NOTHING;
INSERT INTO projects (slug, name, policy) VALUES
  ('hlm-librarian', 'Librarian working memory (reserved)', '{"reserved": true, "librarian": "off"}'),
  ('hlm-global', 'Global experience (reserved)', '{"reserved": true, "librarian": "off"}')
ON CONFLICT (slug) DO NOTHING;
"""


@dataclass(frozen=True, slots=True)
class ReservedIds:
    librarian_device_id: int
    memory_project_id: int
    global_project_id: int


async def ensure_reserved_rows(conn: AsyncConnection) -> ReservedIds:
    """Idempotent; runs inside the caller's transaction (the caller commits)."""
    async with conn.cursor() as cur:
        await cur.execute(_ENSURE, prepare=False)
    return await reserved_ids(conn)


async def reserved_ids(conn: AsyncConnection) -> ReservedIds:
    cur = await conn.execute(
        "SELECT device_id FROM devices WHERE name = %s AND is_system AND token_sha256 = %s",
        (LIBRARIAN_DEVICE, LIBRARIAN_TOKEN_PLACEHOLDER),
    )
    dev = await cur.fetchone()
    cur = await conn.execute(
        "SELECT slug, project_id FROM projects WHERE slug = ANY(%s)", (list(RESERVED_PROJECTS),)
    )
    projects = dict(await cur.fetchall())
    if dev is None or set(projects) != set(RESERVED_PROJECTS):
        raise RuntimeError(
            "librarian reserved rows missing: run `alembic upgrade phase0@head` (0006_librarian)"
        )
    return ReservedIds(int(dev[0]), int(projects[MEMORY_PROJECT]), int(projects[GLOBAL_PROJECT]))


__all__ = [
    "GLOBAL_PROJECT",
    "LIBRARIAN_DEVICE",
    "MEMORY_PROJECT",
    "RESERVED_PROJECTS",
    "ReservedIds",
    "ensure_reserved_rows",
    "reserved_ids",
]
