"""Migration ``0008_librarian_tasks`` (W2b/W2c) on a fresh database (``<test db>_b``).

``version_signals`` and ``librarian_batches`` exist with their CHECKs, one open batch per project,
``librarian_questions`` accepts the W2c statuses and has ``answer``/``expires_at``; downgrade to
``0006_librarian`` removes exactly that and upgrade again round-trips.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.integration.test_migration_0006 import _alembic, fresh_dsn  # noqa: F401 - fixture by import

pytestmark = pytest.mark.integration

STATUSES = ("open", "approved", "rejected", "superseded", "answered", "applied", "expired", "authority_lost")
SIGNAL_COLS = {
    "version_id",
    "importance",
    "importance_src",
    "stability_suggested",
    "topic_hint",
    "topic_logical_id",
    "tags_add",
    "usage_count",
    "last_scored_at",
    "score",
}
INSERT_BATCH = (
    "INSERT INTO librarian_batches (batch_id, project_id, status, created_at, source_event_id)"
    " VALUES (%s, %s, 'open', now(), %s)"
)
INSERT_QUESTION = (
    "INSERT INTO librarian_questions (question_id, job_key, batch_id, project_id, project_ids, kind,"
    " subject_clues, subject_version_ids, proposal, status, created_at, source_event_id, answer, expires_at)"
    " VALUES (gen_random_uuid(), 'k', %s, %s, ARRAY[%s]::bigint[], 'link', '{}', '{}', '{}', %s, now(), %s,"
    " '{\"decision\":\"accept\"}', now() + interval '30 days')"
)


def test_migration_0008_tables_and_round_trip(fresh_dsn: str) -> None:  # noqa: F811
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        dev = conn.execute("SELECT device_id FROM devices WHERE name = 'librarian'").fetchone()[0]
        pid = conn.execute("SELECT project_id FROM projects WHERE slug = 'hlm-global'").fetchone()[0]
        ev = conn.execute(
            "INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,"
            " occurred_at) VALUES (%s, %s, 't', gen_random_uuid(), 'librarian', '{}', 'x', now())"
            " RETURNING event_id",
            (pid, dev),
        ).fetchone()[0]
        b1 = uuid.uuid4()
        conn.execute(INSERT_BATCH, (b1, pid, ev))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():  # one open batch per project
            conn.execute(INSERT_BATCH, (uuid.uuid4(), pid, ev))
        for status in STATUSES:
            conn.execute(INSERT_QUESTION, (b1, pid, pid, status, ev))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE librarian_questions SET status = 'bogus'")
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE librarian_batches SET status = 'bogus'")
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'version_signals'"
        )
        assert SIGNAL_COLS <= {r[0] for r in cur.fetchall()}
        conn.rollback()
    _alembic(fresh_dsn, "downgrade", "0006_librarian")
    with psycopg.connect(fresh_dsn) as conn:
        cur = conn.execute("SELECT to_regclass('version_signals'), to_regclass('librarian_batches')")
        assert cur.fetchone() == (None, None)
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'librarian_questions'"
            " AND column_name IN ('answer', 'expires_at')"
        )
        assert cur.fetchall() == []
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT to_regclass('version_signals') IS NOT NULL").fetchone() == (True,)
