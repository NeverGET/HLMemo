"""Migration ``0006_librarian`` on a fresh database (``<test db>_b``, created and dropped here).

The reserved librarian device exists, is a system device, trusted, not admin, and has no usable
bearer; the reserved projects ``hlm-librarian`` and ``hlm-global`` exist with NO grants; the new
job and event kinds are accepted; downgrade to 0005 (W0a, which keeps device_minted) and upgrade
again round-trip.
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


def _main_head() -> str:
    """The ``main@head`` revision (0006 is followed by later revisions on the chain)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    (rev,) = script.get_revisions("main@head")
    return rev.revision


GIN_SQL = (
    "SELECT c.relname, coalesce(array_to_string(c.reloptions, ','), '') FROM pg_class c"
    " WHERE c.relname IN ('chunks_trgm_gin', 'chunks_tsv_gin', 'mv_title_tsv') ORDER BY 1"
)


def test_migration_0006_reserved_rows_and_round_trip(fresh_dsn: str) -> None:
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        # D-063: no GIN pending list on the chunk/title indexes
        assert conn.execute(GIN_SQL).fetchall() == [
            ("chunks_trgm_gin", "fastupdate=off"),
            ("chunks_tsv_gin", "fastupdate=off"),
            ("mv_title_tsv", "fastupdate=off"),
        ]
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(_main_head(),)]
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
    _alembic(fresh_dsn, "downgrade", "0005_w0_access")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM devices WHERE name = 'librarian'").fetchone() == (0,)
        assert conn.execute("SELECT to_regclass('llm_calls')").fetchone() == (None,)
        assert [opt for _, opt in conn.execute(GIN_SQL).fetchall()] == ["", "", ""]  # symmetric
        # 0005_w0_access state survives the 0006 downgrade: device_minted is still a valid kind.
        conn.execute(
            "INSERT INTO events (device_id, client, request_id, kind, payload, payload_sha256,"
            " occurred_at) VALUES (1, 't', gen_random_uuid(), 'device_minted', '{}', 'x', now())"
        )
        conn.rollback()
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM devices WHERE is_system").fetchone() == (1,)


def test_migration_0006_fails_fast_behind_an_index_reader_then_retries(fresh_dsn: str) -> None:
    """Neutral verifier (W2a): a reader holding AccessShare on chunks_trgm_gin must not make the
    fastupdate ALTER queue while table locks are held. The migration fails within lock_timeout
    with NOTHING of the table part applied, and a plain re-run completes (idempotent)."""
    import time

    _alembic(fresh_dsn, "upgrade", "0005_w0_access")
    holder = psycopg.connect(fresh_dsn)
    try:
        holder.execute("SET enable_seqscan = off")
        holder.execute("SELECT count(*) FROM chunks WHERE text_norm % 'svc-qx7'").fetchone()
        held = holder.execute(
            "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation"
            " WHERE c.relname = 'chunks_trgm_gin' AND l.pid = pg_backend_pid()"
        ).fetchone()
        assert held == (1,)  # the reader's open transaction keeps AccessShare on the index
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "main@head"],
            cwd=ROOT,
            env={**os.environ, "HLM_DB_DSN": fresh_dsn},
            text=True,
            capture_output=True,
        )
        elapsed = time.monotonic() - t0
        assert proc.returncode != 0 and "lock_timeout" in proc.stderr and "Retry" in proc.stderr
        assert elapsed < 15, elapsed  # 3 s lock_timeout + interpreter start, never an open-ended queue
        with psycopg.connect(fresh_dsn) as conn:  # no partial state: the table DDL never ran
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [("0005_w0_access",)]
            row = conn.execute(
                "SELECT to_regclass('llm_calls'), to_regclass('librarian_questions')"
            ).fetchone()
            assert row == (None, None)
    finally:
        holder.rollback()
        holder.close()
    t0 = time.monotonic()
    _alembic(fresh_dsn, "upgrade", "main@head")  # retry
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(_main_head(),)]
        assert [opt for _, opt in conn.execute(GIN_SQL).fetchall()] == ["fastupdate=off"] * 3
    print(f"\n0006 upgrade after the reader released: {time.monotonic() - t0:.2f} s (incl. interpreter)")


def _alembic_fault(dsn: str, fault: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "HLM_DB_DSN": dsn, "HLM_MIGRATION_FAULT": fault, "HLM_TESTING": "1"},
        text=True,
        capture_output=True,
    )


def _state(dsn: str) -> tuple:
    with psycopg.connect(dsn) as conn:
        return (
            conn.execute("SELECT version_num FROM alembic_version").fetchall(),
            conn.execute(
                "SELECT to_regclass('llm_calls') IS NOT NULL,"
                " to_regclass('events_librarian_role') IS NOT NULL"
            ).fetchone(),
            [opt for _, opt in conn.execute(GIN_SQL).fetchall()],
            conn.execute("SELECT count(*) FROM devices WHERE token_sha256 = 'reserved:librarian'").fetchone(),
        )


def test_migration_0006_upgrade_recovers_after_the_flush_failed(fresh_dsn: str) -> None:
    """Sol 38 #4a: the pending-list flush fails AFTER the table DDL committed; version stays 0005;
    a plain re-run completes (every DDL step is idempotent) and reaches head."""
    _alembic(fresh_dsn, "upgrade", "0005_w0_access")
    proc = _alembic_fault(fresh_dsn, "0006:flush", "upgrade", "main@head")
    assert proc.returncode != 0 and "injected fault at flush" in proc.stderr
    version, (tables, _role_index), gin, _dev = _state(fresh_dsn)
    assert version == [("0005_w0_access",)] and tables  # the half-applied state
    _alembic(fresh_dsn, "upgrade", "main@head")  # recovery
    assert _state(fresh_dsn) == ([(_main_head(),)], (True, True), ["fastupdate=off"] * 3, (1,))


def test_migration_0006_failed_downgrade_leaves_head_and_reruns(fresh_dsn: str) -> None:
    """Sol 38 #4b: a downgrade failing midway rolls back as one transaction: the database is
    exactly at 0006, re-running upgrade (no-op) or downgrade succeeds, and upgrade again works."""
    _alembic(fresh_dsn, "upgrade", "0006_librarian")  # pinned: later revisions downgrade first
    head = _state(fresh_dsn)
    proc = _alembic_fault(fresh_dsn, "0006:downgrade", "downgrade", "0005_w0_access")
    assert proc.returncode != 0 and "injected fault at downgrade" in proc.stderr
    assert _state(fresh_dsn) == head  # nothing half-applied (fastupdate still off, index present)
    _alembic(fresh_dsn, "upgrade", "0006_librarian")  # pinned: later revisions downgrade first
    assert _state(fresh_dsn) == head
    _alembic(fresh_dsn, "downgrade", "0005_w0_access")
    assert _state(fresh_dsn) == ([("0005_w0_access",)], (False, False), [""] * 3, (0,))
    _alembic(fresh_dsn, "upgrade", "0006_librarian")  # pinned: later revisions downgrade first
    assert _state(fresh_dsn) == head
