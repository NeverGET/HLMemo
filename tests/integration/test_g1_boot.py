"""G1 boot smoke for Task 1: migration 0001 applies, device 1 is reserved, key constraints hold.

`test_health_within_60s` / `test_restart_preserves_acked_events` (VALIDATION-GATES G1) land with
the real API in a later task.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 1, tzinfo=UTC)
D = timedelta(days=1)
INF = "infinity"


async def _seed_project_and_event(conn: psycopg.AsyncConnection, *, device_id: int = 1) -> tuple[int, int]:
    """Insert one project and one write event; return (project_id, event_id)."""
    cur = await conn.execute(
        "INSERT INTO projects (slug, name) VALUES ('fx-main', 'Fixture main') RETURNING project_id"
    )
    (project_id,) = await cur.fetchone()
    cur = await conn.execute(
        """
        INSERT INTO events (project_id, device_id, client, request_id, kind, payload,
                            payload_sha256, occurred_at)
        VALUES (%s, %s, 'pytest/0', %s, 'write', '{"request":{},"resolved":{}}', 'sha', %s)
        RETURNING event_id
        """,
        (project_id, device_id, uuid.uuid4(), T0),
    )
    (event_id,) = await cur.fetchone()
    return project_id, event_id


async def _insert_version(
    conn: psycopg.AsyncConnection,
    *,
    logical_id: int,
    project_id: int,
    event_id: int,
    valid_from: datetime,
    valid_to: datetime | str,
    recorded_at: datetime,
    kind: str = "fact",
) -> int:
    cur = await conn.execute(
        """
        INSERT INTO memory_versions
            (logical_id, project_id, project_ids, kind, title, body, token_count,
             valid_from, valid_to, recorded_at, source_event_id)
        VALUES (%s, %s, %s, %s, 'title', 'body', 3, %s, %s, %s, %s)
        RETURNING version_id
        """,
        (logical_id, project_id, [project_id], kind, valid_from, valid_to, recorded_at, event_id),
    )
    (version_id,) = await cur.fetchone()
    return version_id


async def test_migration_applies_and_device1_reserved(connect) -> None:
    async with await connect() as conn:
        cur = await conn.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        assert [r[0] for r in await cur.fetchall()] == ["0001_phase0"], "hnsw branch must NOT be applied"

        cur = await conn.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm','btree_gist') ORDER BY 1"
        )
        assert [r[0] for r in await cur.fetchall()] == ["btree_gist", "pg_trgm", "vector"]

        cur = await conn.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'
            ORDER BY 1
            """
        )
        assert [r[0] for r in await cur.fetchall()] == [
            "chunks",
            "device_project_grants",
            "devices",
            "embeddings",
            "events",
            "jobs",
            "links",
            "memory_versions",
            "projects",
        ]

        cur = await conn.execute(
            """
            SELECT device_id, user_id, name, class, status, is_admin, token_sha256, token_generation,
                   fingerprint, approved_by_device_id, approved_at IS NOT NULL
            FROM devices WHERE device_id = 1
            """
        )
        row = await cur.fetchone()
        assert row is not None, "device 1 must be reserved by migration 0001"
        device_id, user_id, name, cls, status, is_admin, token_sha256, gen, fp, approved_by, approved = row
        assert (device_id, user_id, name, cls, status) == (1, "owner", "admin", "server", "trusted")
        assert is_admin is True
        assert token_sha256 == "reserved:admin"  # placeholder: can never equal sha256(<token>)
        assert gen == 1 and fp == "reserved:admin" and approved_by == 1 and approved

        cur = await conn.execute("SELECT count(*) FROM devices")
        assert (await cur.fetchone())[0] == 1

        # identity sequence was advanced past the reserved id: next device gets id 2
        cur = await conn.execute(
            "INSERT INTO devices (name, fingerprint, token_sha256) VALUES ('dev-two', 'fp2', 'h2')"
            " RETURNING device_id"
        )
        assert (await cur.fetchone())[0] == 2

        # only the reserved device may be admin
        with pytest.raises(errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO devices (name, fingerprint, token_sha256, is_admin)"
                    " VALUES ('dev-3','fp3','h3', true)"
                )

        # events.projection_version exists with default 1
        cur = await conn.execute(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'events' AND column_name = 'projection_version'
            """
        )
        assert (await cur.fetchone())[0] == "1"
        await conn.rollback()


async def test_constraints_smoke(connect) -> None:
    async with await connect() as conn:
        project_id, event_id = await _seed_project_and_event(conn)
        cur = await conn.execute("SELECT nextval('logical_id_seq')")
        (logical_id,) = await cur.fetchone()

        # --- EXCLUDE: two non-overlapping CURRENT valid-time segments of one logical item are fine
        v1 = await _insert_version(
            conn,
            logical_id=logical_id,
            project_id=project_id,
            event_id=event_id,
            valid_from=T0,
            valid_to=T0 + 10 * D,
            recorded_at=T0,
        )
        v2 = await _insert_version(
            conn,
            logical_id=logical_id,
            project_id=project_id,
            event_id=event_id,
            valid_from=T0 + 10 * D,
            valid_to=INF,
            recorded_at=T0,
        )
        assert v2 > v1

        # --- overlapping valid interval while both system intervals are open -> rejected
        with pytest.raises(errors.ExclusionViolation):
            async with conn.transaction():
                await _insert_version(
                    conn,
                    logical_id=logical_id,
                    project_id=project_id,
                    event_id=event_id,
                    valid_from=T0 + 5 * D,
                    valid_to=T0 + 12 * D,
                    recorded_at=T0 + D,
                )

        # --- the same valid interval is allowed once the old row's system interval is closed
        await conn.execute(
            "UPDATE memory_versions SET superseded_at = %s WHERE version_id = %s", (T0 + D, v1)
        )
        await _insert_version(
            conn,
            logical_id=logical_id,
            project_id=project_id,
            event_id=event_id,
            valid_from=T0,
            valid_to=T0 + 10 * D,
            recorded_at=T0 + D,
        )

        # --- valid_from < valid_to CHECK
        with pytest.raises(errors.CheckViolation):
            async with conn.transaction():
                await _insert_version(
                    conn,
                    logical_id=logical_id + 1,
                    project_id=project_id,
                    event_id=event_id,
                    valid_from=T0,
                    valid_to=T0,
                    recorded_at=T0,
                )

        # --- one project card per project per (valid_at, known_at) point
        await _insert_version(
            conn,
            logical_id=logical_id + 2,
            project_id=project_id,
            event_id=event_id,
            valid_from=T0,
            valid_to=INF,
            recorded_at=T0,
            kind="project_card",
        )
        with pytest.raises(errors.ExclusionViolation):
            async with conn.transaction():
                await _insert_version(
                    conn,
                    logical_id=logical_id + 3,
                    project_id=project_id,
                    event_id=event_id,
                    valid_from=T0 + D,
                    valid_to=INF,
                    recorded_at=T0,
                    kind="project_card",
                )

        # --- events: request_id unique per (project, device); NULL project is a distinct-but-unique key
        rid = uuid.uuid4()
        ev_sql = """
            INSERT INTO events (project_id, device_id, client, request_id, kind, payload,
                                payload_sha256, occurred_at)
            VALUES (%s, %s, 'pytest/0', %s, 'write', '{}', 'sha', %s)
        """
        await conn.execute(ev_sql, (project_id, 1, rid, T0))
        with pytest.raises(errors.UniqueViolation):
            async with conn.transaction():
                await conn.execute(ev_sql, (project_id, 1, rid, T0))
        await conn.execute(
            "INSERT INTO devices (name, fingerprint, token_sha256) VALUES ('dev-two','fp2','h2')"
        )
        await conn.execute(ev_sql, (project_id, 2, rid, T0))  # same UUID from another device = new request
        await conn.execute(ev_sql, (None, 1, rid, T0))  # device-level event, same UUID, different key
        with pytest.raises(errors.UniqueViolation):  # NULLS NOT DISTINCT
            async with conn.transaction():
                await conn.execute(ev_sql, (None, 1, rid, T0))

        # --- links: derived_from requires a pinned dst_version_id
        link_sql = """
            INSERT INTO links (project_id, project_ids, src_logical_id, dst_logical_id, dst_version_id, rel,
                               valid_from, recorded_at, source_event_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with pytest.raises(errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    link_sql,
                    (
                        project_id,
                        [project_id],
                        logical_id + 2,
                        logical_id,
                        None,
                        "derived_from",
                        T0,
                        T0,
                        event_id,
                    ),
                )
        await conn.execute(
            link_sql,
            (project_id, [project_id], logical_id + 2, logical_id, v2, "derived_from", T0, T0, event_id),
        )
        await conn.execute(
            link_sql,
            (project_id, [project_id], logical_id + 2, logical_id, None, "relates_to", T0, T0, event_id),
        )
        # duplicate current edge (src, dst, rel) at the same point -> rejected
        with pytest.raises(errors.ExclusionViolation):
            async with conn.transaction():
                await conn.execute(
                    link_sql,
                    (
                        project_id,
                        [project_id],
                        logical_id + 2,
                        logical_id,
                        None,
                        "relates_to",
                        T0 + D,
                        T0,
                        event_id,
                    ),
                )

        # --- device_scope CHECK and home-project CHECK
        with pytest.raises(errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO memory_versions (logical_id, project_id, project_ids, device_scope, kind,
                        title, body,
                        token_count, valid_from, recorded_at, source_event_id)
                    VALUES (%s, %s, %s, 'class:laptop', 'fact', 't', 'b', 1, %s, %s, %s)
                    """,
                    (logical_id + 9, project_id, [project_id], T0, T0, event_id),
                )
        with pytest.raises(errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,
                        token_count, valid_from, recorded_at, source_event_id)
                    VALUES (%s, %s, %s, 'fact', 't', 'b', 1, %s, %s, %s)
                    """,
                    (logical_id + 9, project_id, [project_id + 100], T0, T0, event_id),
                )
        await conn.commit()
