"""Shared test infrastructure (PHASE0-SPEC §7): session-scoped Postgres + migrated schema.

Database selection:
  1. `HLM_TEST_DSN` set  -> use it as-is (CI / `compose --profile test`).
  2. else                -> `docker compose up -d --wait db` and use the host-mapped port.
In both cases `alembic upgrade phase0@head` is applied once per session (idempotent).
Between tests every table is truncated except `devices` row 1 (reserved admin, §2).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DB_USER = "hlm"
COMPOSE_DB_PASSWORD = "hlm"
COMPOSE_DB_NAME = "hlm"

ConnectFactory = Callable[[], Awaitable[psycopg.AsyncConnection]]


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def _compose_db_dsn() -> tuple[str, bool]:
    """Ensure the compose `db` service is healthy; return (dsn, started_by_us)."""
    already = _compose("ps", "-q", "--status", "running", "db", check=False).stdout.strip()
    _compose("up", "-d", "--wait", "db")
    port_line = _compose("port", "db", "5432").stdout.strip()  # e.g. 0.0.0.0:5432
    host, _, port = port_line.rpartition(":")
    host = "127.0.0.1" if host in ("0.0.0.0", "", "[::]") else host
    dsn = f"postgresql://{COMPOSE_DB_USER}:{COMPOSE_DB_PASSWORD}@{host}:{port}/{COMPOSE_DB_NAME}"
    return dsn, not already


def _wait_for_postgres(dsn: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.Error as exc:  # noqa: PERF203
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"Postgres not reachable at {dsn!r}: {last}")


def _migrate(dsn: str) -> str:
    env = {**os.environ, "HLM_DB_DSN": dsn}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "phase0@head"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stderr + proc.stdout


@pytest.fixture(scope="session")
def db_dsn() -> str:
    """Migrated Postgres DSN for the whole session."""
    dsn = os.environ.get("HLM_TEST_DSN")
    started_by_us = False
    if not dsn:
        dsn, started_by_us = _compose_db_dsn()
    _wait_for_postgres(dsn)
    _migrate(dsn)
    yield dsn
    if started_by_us and os.environ.get("HLM_TEST_KEEP_DB") != "1":
        _compose("stop", "db", check=False)


@pytest.fixture(scope="session")
def connect(db_dsn: str) -> ConnectFactory:
    """Async connection factory: `async with await connect() as conn: ...`."""

    async def _connect() -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(db_dsn, autocommit=False)
        await conn.execute("SET TIME ZONE 'UTC'")
        return conn

    return _connect


# Projection/event tables in FK-safe truncate order; `devices` handled separately (row 1 stays).
TRUNCATE_SQL = """
TRUNCATE TABLE jobs, links, embeddings, chunks, memory_versions, events,
               device_project_grants, projects
    RESTART IDENTITY CASCADE;
DELETE FROM devices WHERE device_id <> 1;
SELECT setval(pg_get_serial_sequence('devices', 'device_id'), 1);
SELECT setval('logical_id_seq', 1, false);
"""


@pytest.fixture(autouse=True)
async def _clean_tables(connect: ConnectFactory) -> None:
    """Truncate all tables between tests, keeping the reserved admin device (device_id = 1)."""
    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
    yield
