"""System-actor events (CC-5, proposed D-062): ``events.schema_version = 2``.

For system-actor kinds (``librarian``, ``consolidation``, ``question``) and the librarian-era
owner decisions (``answer``, role decisions), ``payload.request`` is the actor's arguments — for
an LLM job the versioned audit record ``{"audit": "llm/1", …}`` — and ``payload.resolved`` holds
only what was applied (mutations with their allocated ids, enqueued jobs, the job marked done) plus
``recorded_at``. Replay reads ``resolved`` only and never calls an LLM.

Idempotency: ``request_id`` is deterministic (uuid5 over the job's dedupe key) and the insert is
``ON CONFLICT DO NOTHING`` on ``UNIQUE (project_id, device_id, request_id)``: a job applied twice
(lease takeover, crash after commit) records exactly one event.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from hlmemo import __version__ as _HLM_VERSION
from hlmemo.core.budget import canonical

SCHEMA_VERSION_SYSTEM = 2
NS_LIBRARIAN = uuid.UUID("5b0f1c2e-7d7a-5c55-9a61-6c6962726172")
CLIENT = f"hlm-librarian/{_HLM_VERSION}"


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(request).encode("utf-8")).hexdigest()


async def lock_event_refs(conn: AsyncConnection, project_id: int | None, device_id: int) -> None:
    """Take the row locks an ``events`` insert's foreign keys take (``FOR KEY SHARE`` on its
    project and device rows) NOW, so the insert itself cannot wait (D-095): an apply judges the
    question TTL once every lock is held, and nothing may wait between that check and the event.
    The same locks at the same place in the lock order (the insert took them last anyway)."""
    if project_id is not None:
        await conn.execute("SELECT 1 FROM projects WHERE project_id = %s FOR KEY SHARE", (project_id,))
    await conn.execute("SELECT 1 FROM devices WHERE device_id = %s FOR KEY SHARE", (device_id,))


async def insert_system_event(
    conn: AsyncConnection,
    *,
    kind: str,
    project_id: int | None,
    device_id: int,
    client: str,
    request_id: uuid.UUID,
    request: dict[str, Any],
    resolved: dict[str, Any],
    at: datetime,
    event_id: int | None = None,
) -> int | None:
    """Insert one schema-version-2 event; ``None`` if ``(project, device, request_id)`` exists."""
    payload = {"request": request, "resolved": resolved}
    if event_id is None:
        cur = await conn.execute(
            """
            INSERT INTO events (project_id, device_id, client, request_id, kind, schema_version, payload,
                                payload_sha256, occurred_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING event_id
            """,
            (
                project_id,
                device_id,
                client,
                request_id,
                kind,
                SCHEMA_VERSION_SYSTEM,
                Jsonb(payload),
                request_sha256(request),
                at,
            ),
        )
    else:
        cur = await conn.execute(
            """
            INSERT INTO events (event_id, project_id, device_id, client, request_id, kind, schema_version,
                                payload, payload_sha256, occurred_at)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING event_id
            """,
            (
                event_id,
                project_id,
                device_id,
                client,
                request_id,
                kind,
                SCHEMA_VERSION_SYSTEM,
                Jsonb(payload),
                request_sha256(request),
                at,
            ),
        )
    row = await cur.fetchone()
    return None if row is None else int(row[0])


__all__ = [
    "CLIENT",
    "NS_LIBRARIAN",
    "SCHEMA_VERSION_SYSTEM",
    "insert_system_event",
    "lock_event_refs",
    "request_sha256",
]
