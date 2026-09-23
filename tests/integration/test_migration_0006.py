"""Migration ``0006_librarian`` on a fresh database (``<test db>_b``, created and dropped here).

The reserved librarian device exists, is a system device, trusted, not admin, and has no usable
bearer; the reserved projects ``hlm-librarian`` and ``hlm-global`` exist with NO grants; the new
job and event kinds are accepted; downgrade to 0004 and upgrade again round-trip.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from hlmemo.auth.tokens import hash_token

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _alembic(dsn: str, *args: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "HLM_DB_DSN": dsn},
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.fixture
def fresh_dsn(db_dsn: str):  # noqa: ANN201
    parts = conninfo_to_dict(db_dsn)
    name = f"{parts['dbname']}_b"
    admin = make_conninfo(db_dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(db_dsn, dbname=name)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


GIN_SQL = (
    "SELECT c.relname, coalesce(array_to_string(c.reloptions, ','), '') FROM pg_class c"
    " WHERE c.relname IN ('chunks_trgm_gin', 'chunks_tsv_gin', 'mv_title_tsv') ORDER BY 1"
)


def test_migration_0006_reserved_rows_and_round_trip(fresh_dsn: str) -> None:
    _alembic(fresh_dsn, "upgrade", "phase0@head")
    with psycopg.connect(fresh_dsn) as conn:
        # D-063: no GIN pending list on the chunk/title indexes
        assert conn.execute(GIN_SQL).fetchall() == [
            ("chunks_trgm_gin", "fastupdate=off"),
            ("chunks_tsv_gin", "fastupdate=off"),
            ("mv_title_tsv", "fastupdate=off"),
        ]
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0006_librarian",)]
        dev = conn.execute(
            "SELECT device_id, class, status, is_admin, is_system, token_sha256 FROM devices"
            " WHERE name = 'librarian'"
        ).fetchone()
        assert dev is not None and dev[1:5] == ("server", "trusted", False, True)
        assert dev[5] == "reserved:librarian"
        assert dev[5] != hash_token("reserved:librarian")  # a hash is 64 hex chars: nothing matches
        projects = conn.execute("SELECT slug, policy->>'librarian' FROM projects ORDER BY slug").fetchall()
        assert projects == [("hlm-global", "off"), ("hlm-librarian", "off")]
        assert conn.execute("SELECT count(*) FROM device_project_grants").fetchone() == (0,)
        assert conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'jobs' AND column_name = 'priority'"
        ).fetchone() == ("5",)
        for kind in (
            "librarian",
            "question",
            "answer",
            "import",
            "ingest",
            "consolidation",
            "pack_import",
            "device_minted",
        ):
            conn.execute(
                "INSERT INTO events (device_id, client, request_id, kind, payload, payload_sha256,"
                " occurred_at)"
                " VALUES (%s, 't', gen_random_uuid(), %s, '{}', 'x', now())",
                (dev[0], kind),
            )
        conn.execute(
            "INSERT INTO jobs (kind, dedupe_key, payload) VALUES ('librarian_write', 'k', '{}'),"
            " ('experience_review', 'k2', '{}')"
        )
        conn.rollback()
    _alembic(fresh_dsn, "downgrade", "0004_title_norm_fold")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM devices WHERE name = 'librarian'").fetchone() == (0,)
        assert conn.execute("SELECT to_regclass('llm_calls')").fetchone() == (None,)
        assert [opt for _, opt in conn.execute(GIN_SQL).fetchall()] == ["", "", ""]  # symmetric
    _alembic(fresh_dsn, "upgrade", "phase0@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM devices WHERE is_system").fetchone() == (1,)
