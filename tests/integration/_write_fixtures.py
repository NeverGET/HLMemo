"""Shared helpers for the write-service integration tests (G5 integrity, G6 durability).

Devices, projects and grants are inserted with plain SQL; ``AuthContext`` objects are built
directly (no HTTP, no token hashing) exactly as ``server/auth.py`` would derive them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from hlmemo.auth.context import AuthContext, Role

MAIN = "g6-main"
OTHER = "g6-other"


@dataclass(slots=True)
class World:
    main_id: int
    other_id: int
    main_card_lid: int
    dev_a: int  # write on main + other
    dev_b: int  # write on other only
    ctx_a: AuthContext
    ctx_b: AuthContext
    ctx_admin: AuthContext


async def seed_world(conn: psycopg.AsyncConnection) -> World:
    cur = await conn.execute(
        "INSERT INTO projects (slug, name) VALUES (%s, 'Main'), (%s, 'Other') RETURNING project_id, slug",
        (MAIN, OTHER),
    )
    ids = {slug: pid for pid, slug in await cur.fetchall()}
    cur = await conn.execute("SELECT card_logical_id FROM projects WHERE project_id = %s", (ids[MAIN],))
    (card_lid,) = await cur.fetchone()
    cur = await conn.execute(
        """
        INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,
                             approved_by_device_id)
        VALUES ('dev-a', 'personal', 'fp-a', 'h-a', 'trusted', now(), 1),
               ('dev-b', 'work', 'fp-b', 'h-b', 'trusted', now(), 1)
        RETURNING device_id, name
        """
    )
    devs = {name: did for did, name in await cur.fetchall()}
    await conn.execute(
        """
        INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)
        VALUES (%s, %s, 'write', 1), (%s, %s, 'write', 1), (%s, %s, 'write', 1)
        """,
        (devs["dev-a"], ids[MAIN], devs["dev-a"], ids[OTHER], devs["dev-b"], ids[OTHER]),
    )
    await conn.commit()
    ctx_a = AuthContext(
        device_id=devs["dev-a"],
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants={ids[MAIN]: Role.WRITE, ids[OTHER]: Role.WRITE},
        client="pytest/0",
    )
    ctx_b = AuthContext(
        device_id=devs["dev-b"],
        device_class="work",
        is_admin=False,
        token_generation=1,
        grants={ids[OTHER]: Role.WRITE},
        client="pytest/0",
    )
    ctx_admin = AuthContext(device_id=1, device_class="server", is_admin=True, token_generation=1)
    return World(ids[MAIN], ids[OTHER], card_lid, devs["dev-a"], devs["dev-b"], ctx_a, ctx_b, ctx_admin)


def item(title: str, body: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "fact", "title": title, "body": body, **kw}


def write_req(project: str, items: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"project": project, "request_id": str(uuid.uuid4()), "client": "pytest/0", "items": items, **kw}


async def count(conn: psycopg.AsyncConnection, table: str, where: str = "true", params: tuple = ()) -> int:
    cur = await conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", params)
    return (await cur.fetchone())[0]


async def event_payload(conn: psycopg.AsyncConnection, request_id: str) -> dict[str, Any]:
    cur = await conn.execute("SELECT payload FROM events WHERE request_id = %s", (request_id,))
    return (await cur.fetchone())[0]


async def head_at(
    conn: psycopg.AsyncConnection, logical_id: int, valid_at: datetime, known_at: datetime
) -> tuple[int, str] | None:
    """The version of ``logical_id`` live at ``(valid_at, known_at)`` — direct SQL, both axes."""
    cur = await conn.execute(
        """
        SELECT version_id, body FROM memory_versions
        WHERE logical_id = %s AND valid_from <= %s AND %s < valid_to
          AND recorded_at <= %s AND %s < superseded_at
        ORDER BY version_id DESC
        """,
        (logical_id, valid_at, valid_at, known_at, known_at),
    )
    rows = await cur.fetchall()
    assert len(rows) <= 1, f"bi-temporal point must be unique, got {rows}"
    return None if not rows else (rows[0][0], rows[0][1])


async def dump_projections(conn: psycopg.AsyncConnection) -> dict[str, list[str]]:
    """Primary-key-ordered text dumps of the four projection tables (pg_dump-free).

    D-086 §2: the ``run_after`` of a librarian job still QUEUED after a SYSTEMIC hand-back
    (``last_error`` in ``worker.SYSTEMIC_HANDBACK_CODES``) is a non-authoritative scheduling hint
    (moved without an event), so it is not compared; every other job and column is (review 60)."""
    from hlmemo.librarian.worker import SYSTEMIC_HANDBACK_CODES

    out: dict[str, list[str]] = {}
    for table, pk in (("memory_versions", "version_id"), ("chunks", "chunk_id"), ("links", "link_id")):
        cur = await conn.execute(f"SELECT t::text FROM {table} t ORDER BY {pk}")
        out[table] = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT (kind, dedupe_key, payload::text, source_event_id, status,"
        " CASE WHEN kind = 'librarian_write' AND status = 'queued'"
        " AND (last_error IS NULL OR last_error = ANY(%(systemic)s)) THEN NULL ELSE run_after END)::text"
        " FROM jobs ORDER BY dedupe_key",
        {"systemic": sorted(SYSTEMIC_HANDBACK_CODES)},
    )
    out["jobs"] = [r[0] for r in await cur.fetchall()]
    cur = await conn.execute("SELECT t::text FROM code_refs t ORDER BY version_id, path")  # W1.5
    out["code_refs"] = [r[0] for r in await cur.fetchall()]
    return out
