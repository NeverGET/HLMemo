"""Migration ``0007_import`` on a fresh database (``<test db>_b``, created and dropped here).

Upgrade from 0006 with a pre-existing user project: the W1.5 columns, constraints (validated),
index and ``code_refs`` exist; the user project gets its D-015 skeleton card, the reserved system
projects do not; an upgrade interrupted before the backfill completes when re-run; a downgrade
round-trips while no row carries a source and refuses once one does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _alembic(dsn: str, *args: str, fault: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HLM_DB_DSN": dsn}
    if fault:
        env.update(HLM_TESTING="1", HLM_MIGRATION_FAULT=fault)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, text=True, capture_output=True
    )


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


def _version(dsn: str) -> list[tuple[str]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchall()


def _cards(dsn: str) -> list[tuple[str, int]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT p.slug, count(mv.version_id) FROM projects p LEFT JOIN memory_versions mv"
            " ON mv.logical_id = p.card_logical_id GROUP BY p.slug ORDER BY p.slug"
        ).fetchall()


def test_migration_0007_upgrade_backfill_recovery_and_downgrade(fresh_dsn: str) -> None:
    assert _alembic(fresh_dsn, "upgrade", "0006_librarian").returncode == 0
    with psycopg.connect(fresh_dsn) as conn:
        conn.execute("INSERT INTO projects (slug, name) VALUES ('legacy', 'Legacy project')")
    # an upgrade that dies after the DDL (before the backfill) leaves a state a re-run completes
    failed = _alembic(fresh_dsn, "upgrade", "main@head", fault="0007:backfill")
    assert failed.returncode != 0 and "injected fault at backfill" in failed.stderr
    assert _version(fresh_dsn) == [("0006_librarian",)]
    ok = _alembic(fresh_dsn, "upgrade", "main@head")
    assert ok.returncode == 0, ok.stderr[-2000:]
    assert _version(fresh_dsn) == [("0007_import",)]
    assert _cards(fresh_dsn) == [("hlm-global", 0), ("hlm-librarian", 0), ("legacy", 1)]
    with psycopg.connect(fresh_dsn) as conn:
        cons = dict(
            conn.execute(
                "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = 'memory_versions'::regclass"
                " AND conname IN ('mv_source_key_derived', 'mv_source_shape', 'mv_one_source_owner')"
            ).fetchall()
        )
        assert cons == {"mv_source_key_derived": True, "mv_source_shape": True, "mv_one_source_owner": True}
        assert conn.execute(
            "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
            " WHERE c.relname = 'mv_source_key'"
        ).fetchall() == [(True,)]
        ev = conn.execute("SELECT kind, device_id, client FROM events WHERE kind = 'write'").fetchall()
        assert ev == [("write", 1, "hlm-migrate/0007")]
        # the CHECK pins source_key to the source (a hand-written mismatch is refused)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                'UPDATE memory_versions SET source = \'{"system":"a","path":"b","sha256":"c"}\','
                " source_key = 'a:other'"
            )
    # round trip while no source exists: the backfilled card stays (plain Phase-0 data), no second card
    down = _alembic(fresh_dsn, "downgrade", "0006_librarian")
    assert down.returncode == 0, down.stderr[-2000:]
    assert _alembic(fresh_dsn, "upgrade", "main@head").returncode == 0
    assert _cards(fresh_dsn) == [("hlm-global", 0), ("hlm-librarian", 0), ("legacy", 1)]
    # a version carrying a source makes the downgrade refuse (projection data only replay restores)
    with psycopg.connect(fresh_dsn) as conn:
        conn.execute(
            'UPDATE memory_versions SET source = \'{"system":"a","path":"b","sha256":"c"}\','
            " source_key = 'a:b'"
        )
    refused = _alembic(fresh_dsn, "downgrade", "0006_librarian")
    assert refused.returncode != 0 and "downgrade refused" in refused.stderr
    assert _version(fresh_dsn) == [("0007_import",)]
