"""Migration ``0007_import`` on a fresh database (``<test db>_b``, created and dropped here).

Upgrade from 0006 with a pre-existing user project: the W1.5 columns, constraints (validated),
index and ``code_refs`` exist; the user project gets its D-015 skeleton card, the reserved system
projects do not; an upgrade interrupted before the backfill completes when re-run; a downgrade
round-trips while no row carries a source and refuses once one does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SHA = "e" * 64
GOOD = '{"system":"a","path":"b","sha256":"' + SHA + '"}'


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


def _ops(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HLM_DB_DSN": dsn}
    return subprocess.run(
        [sys.executable, "-m", "hlmemo.ops", *args], cwd=ROOT, env=env, text=True, capture_output=True
    )


def _owner_index(dsn: str) -> list[tuple[bool, bool]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT i.indisvalid, i.indisunique FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
            " WHERE c.relname = 'mv_source_owner'"
        ).fetchall()


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
                " AND conname IN ('mv_source_key_derived', 'mv_source_shape')"
            ).fetchall()
        )
        assert cons == {"mv_source_key_derived": True, "mv_source_shape": True}
        assert conn.execute(
            "SELECT i.indisvalid, i.indisunique FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
            " WHERE c.relname = 'mv_source_owner'"
        ).fetchall() == [(True, True)]
        ev = conn.execute("SELECT kind, device_id, client FROM events WHERE kind = 'write'").fetchall()
        assert ev == [("write", 1, "hlm-migrate/0007")]
        # the CHECK pins source_key to the source (a hand-written mismatch is refused)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE memory_versions SET source = %s::jsonb, source_key = 'a:other'", (GOOD,))
    # Sol 42 #2: a source without system/path/sha256 (or with a JSON null) is refused, never UNKNOWN,
    # so a sourced row can never carry a NULL key that dodges the ownership index
    sha = f'"sha256":"{SHA}"'
    for bad in (
        '{"system":"a",' + sha + "}",
        '{"path":"b",' + sha + "}",
        '{"system":"a","path":null,' + sha + "}",
        '{"system":"a","path":"b"}',
        '{"system":"a","path":"",' + sha + "}",
        '"just a string"',
    ):
        with psycopg.connect(fresh_dsn) as conn, pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE memory_versions SET source = %s::jsonb,"
                " source_key = CASE WHEN %s::jsonb IS NULL THEN NULL"
                " ELSE (%s::jsonb ->> 'system') || ':' || (%s::jsonb ->> 'path') END",
                (bad, bad, bad, bad),
            )
    # round trip while no source exists: the backfilled card stays (plain Phase-0 data), no second card
    down = _alembic(fresh_dsn, "downgrade", "0006_librarian")
    assert down.returncode == 0, down.stderr[-2000:]
    assert _alembic(fresh_dsn, "upgrade", "main@head").returncode == 0
    assert _cards(fresh_dsn) == [("hlm-global", 0), ("hlm-librarian", 0), ("legacy", 1)]
    # a version carrying a source makes the downgrade refuse (projection data only replay restores)
    with psycopg.connect(fresh_dsn) as conn:
        conn.execute("UPDATE memory_versions SET source = %s::jsonb, source_key = 'a:b'", (GOOD,))
    refused = _alembic(fresh_dsn, "downgrade", "0006_librarian")
    assert refused.returncode != 0 and "downgrade refused" in refused.stderr
    assert _version(fresh_dsn) == [("0007_import",)]


def test_migration_0007_never_blocks_writers_for_a_second(fresh_dsn: str) -> None:
    """Sol 42 #1: the upgrade is online. A writer committing small transactions throughout the
    upgrade — while a long reader holds the table, so every catalog step has to wait and retry —
    is never blocked for more than 1 s, and the upgrade still completes."""
    import threading
    import time

    assert _alembic(fresh_dsn, "upgrade", "0006_librarian").returncode == 0
    with psycopg.connect(fresh_dsn) as conn:
        pid = conn.execute(
            "INSERT INTO projects (slug, name) VALUES ('busy', 'Busy') RETURNING project_id"
        ).fetchone()[0]
        eid = conn.execute(
            "INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,"
            " occurred_at) VALUES (%s, 1, 'pytest/0', gen_random_uuid(), 'write',"
            " '{\"request\":{},\"resolved\":{}}', 'x', now()) RETURNING event_id",
            (pid,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,"
            " token_count, valid_from, recorded_at, source_event_id)"
            " SELECT nextval('logical_id_seq'), %s, ARRAY[%s]::bigint[], 'fact', 't' || g,"
            " repeat('b', 200), 50, now(), now(), %s FROM generate_series(1, 40000) g",
            (pid, pid, eid),
        )
    stop = threading.Event()
    latencies: list[float] = []
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            with psycopg.connect(fresh_dsn) as conn:
                while not stop.is_set():
                    t0 = time.monotonic()
                    conn.execute(
                        "INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,"
                        " token_count, valid_from, recorded_at, source_event_id)"
                        " VALUES (nextval('logical_id_seq'), %s, ARRAY[%s]::bigint[], 'fact', 'w', 'w', 1,"
                        " now(), now(), %s)",
                        (pid, pid, eid),
                    )
                    conn.commit()
                    latencies.append(time.monotonic() - t0)
                    time.sleep(0.02)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    def long_reader() -> None:
        with psycopg.connect(fresh_dsn) as conn:
            conn.execute("SELECT count(*) FROM memory_versions").fetchone()
            time.sleep(2.5)  # holds ACCESS SHARE: every ALTER must time out and retry meanwhile
            conn.rollback()

    threads = [threading.Thread(target=writer), threading.Thread(target=long_reader)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    t_up = time.monotonic()
    up = _alembic(fresh_dsn, "upgrade", "main@head")
    up_s = time.monotonic() - t_up
    time.sleep(0.2)
    stop.set()
    for t in threads:
        t.join()
    assert up.returncode == 0, up.stderr[-2000:]
    assert not errors, errors
    assert up_s > 2.0  # it really waited behind the long reader (and retried)
    assert len(latencies) > 30 and max(latencies) < 1.0, (len(latencies), max(latencies))
    assert _version(fresh_dsn) == [("0007_import",)]


def test_migration_0007_duplicate_open_sources_fail_clearly_and_reconcile(fresh_dsn: str) -> None:
    """Sol 43 #2: pre-existing duplicate open (project, source_key) rows fail the upgrade BEFORE
    the concurrent build, naming the groups and the reconciliation command; any failed build
    leaves no INVALID index; ``ops sources close-duplicates --yes`` reconciles; the re-run is clean."""
    assert _alembic(fresh_dsn, "upgrade", "0006_librarian").returncode == 0
    # the DDL ran but the index did not: duplicates can exist (as rows written before 0007 finished)
    early = _alembic(fresh_dsn, "upgrade", "main@head", fault="0007:index")
    assert early.returncode != 0 and "injected fault at index" in early.stderr
    with psycopg.connect(fresh_dsn) as conn:
        pid = conn.execute(
            "INSERT INTO projects (slug, name) VALUES ('dup', 'Dup') RETURNING project_id"
        ).fetchone()[0]
        eid = conn.execute(
            "INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,"
            " occurred_at) VALUES (%s, 1, 'pytest/0', gen_random_uuid(), 'write',"
            " '{\"request\":{},\"resolved\":{}}', 'x', now()) RETURNING event_id",
            (pid,),
        ).fetchone()[0]
        for n in range(2):  # two logical items claiming one source, the second one newer
            conn.execute(
                "INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,"
                " token_count, valid_from, recorded_at, source_event_id, source, source_key)"
                " VALUES (nextval('logical_id_seq'), %s, ARRAY[%s]::bigint[], 'fact', 'd', %s, 1,"
                " now() - interval '1 day', now() - interval '1 hour' + %s * interval '1 minute', %s,"
                " %s::jsonb, 'a:b')",
                (pid, pid, f"dup {n}", n, eid, GOOD),
            )
    # (a) the pre-check names the group and the ops command; nothing half-built is left behind
    failed = _alembic(fresh_dsn, "upgrade", "main@head")
    assert failed.returncode != 0
    assert "hold more than one open current item" in failed.stderr
    assert f"({pid}, 'a:b') x2" in failed.stderr and "sources close-duplicates --yes" in failed.stderr
    assert _owner_index(fresh_dsn) == [] and _version(fresh_dsn) == [("0006_librarian",)]
    # (b) without the pre-check the concurrent build dies on 23505; the INVALID index it leaves is
    # dropped on the spot (a re-run never trips over it)
    raw = _alembic(fresh_dsn, "upgrade", "main@head", fault="0007:skip-duplicate-check")
    assert raw.returncode != 0 and "could not create unique index" in raw.stderr
    assert _owner_index(fresh_dsn) == []
    # (c) ops lists the group, refuses to close without --yes, then closes all but the newest
    listed = _ops(fresh_dsn, "sources", "duplicates", "--json")
    assert listed.returncode == 0, listed.stderr
    (group,) = json.loads(listed.stdout)["duplicates"]
    assert (group["project"], group["source_key"], len(group["items"])) == ("dup", "a:b", 2)
    older, newer = (it["logical_id"] for it in group["items"])
    refused = _ops(fresh_dsn, "sources", "close-duplicates")
    assert refused.returncode == 2 and "--yes" in refused.stderr
    closed = _ops(fresh_dsn, "sources", "close-duplicates", "--yes")
    assert closed.returncode == 0, closed.stderr
    assert [c["logical_id"] for c in json.loads(closed.stdout)["closed"]] == [older]
    assert json.loads(_ops(fresh_dsn, "sources", "duplicates", "--json").stdout) == {"duplicates": []}
    with psycopg.connect(fresh_dsn) as conn:
        heads = conn.execute(
            "SELECT logical_id, valid_to = 'infinity' FROM memory_versions WHERE source_key = 'a:b'"
            " AND superseded_at = 'infinity' ORDER BY logical_id"
        ).fetchall()
    assert heads == [(older, False), (newer, True)]  # validity ended, nothing deleted
    # (d) the re-run completes with a valid unique index
    ok = _alembic(fresh_dsn, "upgrade", "main@head")
    assert ok.returncode == 0, ok.stderr[-2000:]
    assert _owner_index(fresh_dsn) == [(True, True)] and _version(fresh_dsn) == [("0007_import",)]
