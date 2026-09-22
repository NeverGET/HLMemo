"""G1 boot smoke for Task 1: migration 0001 applies, device 1 is reserved, key constraints hold.

`test_health_within_60s` / `test_restart_preserves_acked_events` (VALIDATION-GATES G1) land with
the real API in a later task.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

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
        # placeholder (fresh migration) or a sha256 hex bound by an API start (§2); device 1 survives
        # table truncation, so the generation only ever grows across sessions.
        assert token_sha256 == "reserved:admin" or (len(token_sha256) == 64 and int(token_sha256, 16) >= 0)
        assert gen >= 1 and fp == "reserved:admin" and approved_by == 1 and approved

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


async def test_api_boots_and_health_ok(db_dsn) -> None:
    """G1 precursor: the app factory's lifespan (pool + admin binding) runs and /health answers 200."""
    import httpx

    from hlmemo.config import get_settings
    from hlmemo.server.app import create_app

    app = create_app(get_settings(db_dsn=db_dsn, admin_token=None, registration_secret=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/health")
            assert r.status_code == 200 and r.json() == {"status": "ok"}
            r = await c.get("/mcp")
            assert r.status_code == 401  # gated: no bearer


@pytest.mark.parametrize("cancel_first", [False, True], ids=["concurrent", "cancelled-leader"])
async def test_concurrent_cold_readiness_loads_models_once(
    db_dsn, tmp_path, monkeypatch, cancel_first
) -> None:
    """Overlapping cold probes share one load, even if the first HTTP caller disconnects."""
    import httpx

    from hlmemo.config import get_settings
    from hlmemo.server import app as server

    for rel in server.MODEL_FILES:
        asset = tmp_path / rel
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("readiness test asset")
    expected_hashes = server.model_hashes(tmp_path)
    lock = tmp_path / "models.lock"
    lock.write_text("\n".join(f"{rel}: sha256:{digest}" for rel, digest in expected_hashes.items()))
    project_file = server._project_file
    monkeypatch.setattr(
        server, "_project_file", lambda name: lock if name == "models.lock" else project_file(name)
    )

    loop = asyncio.get_running_loop()
    hash_started = asyncio.Event()
    all_probes_started = asyncio.Event()
    release_hash = threading.Event()
    probe_count = 8
    probes_started = 0

    original_readiness = server.readiness

    async def observed_readiness(app):
        nonlocal probes_started
        probes_started += 1
        if probes_started == probe_count:
            all_probes_started.set()
        return await original_readiness(app)

    def gated_hashes(path):
        assert path == tmp_path
        loop.call_soon_threadsafe(hash_started.set)
        assert release_hash.wait(timeout=10), "test did not release model verification"
        return expected_hashes

    hashes = Mock(side_effect=gated_hashes)
    embedder = Mock()
    meter = Mock()
    # Count callers at the public boundary: a single-flight dependency check now
    # resolves the model directory only once, regardless of the number of waiters.
    monkeypatch.setattr(server, "readiness", observed_readiness)
    monkeypatch.setattr(server, "default_model_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "model_hashes", hashes)
    monkeypatch.setattr(server, "Embedder", embedder)
    monkeypatch.setattr(server, "Meter", meter)

    app = server.create_app(get_settings(db_dsn=db_dsn, admin_token=None, registration_secret=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            first = asyncio.create_task(c.get("/ready"))
            tasks = [first]
            try:
                await asyncio.wait_for(hash_started.wait(), timeout=5)
                if cancel_first:
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                tasks.extend(asyncio.create_task(c.get("/ready")) for _ in range(probe_count - 1))
                await asyncio.wait_for(all_probes_started.wait(), timeout=5)
                assert not any(task.done() for task in tasks[1:])
            finally:
                release_hash.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

            for response in results[1:] if cancel_first else results:
                assert isinstance(response, httpx.Response), response
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "ready"
                assert response.json()["checks"]["models"]["inference"] is True
            assert (await c.get("/ready")).status_code == 200  # warm cache stays reusable

    hashes.assert_called_once_with(tmp_path)
    embedder.assert_called_once_with(tmp_path, threads=2)
    embedder.return_value.embed_query.assert_called_once_with("readiness")
    meter.assert_called_once_with()
    meter.return_value.count_text.assert_called_once_with("readiness")
