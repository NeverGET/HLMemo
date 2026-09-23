"""Shared test infrastructure (PHASE0-SPEC §7): session-scoped Postgres + migrated schema.

Database selection:
  1. `HLM_TEST_DSN` set  -> use it as-is (CI / `compose --profile test`).
  2. else                -> `docker compose up -d --wait db` and use a dedicated `hlm_test`
                            database on it (created if missing), never the dev database `hlm`.
Tests truncate every table, so a DSN naming a protected database (the dev stack's `hlm`) is refused.
In both cases `alembic upgrade main@head` is applied once per session (idempotent).
Between tests every table is truncated except `devices` row 1 (reserved admin, §2).
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import psycopg
import pytest

# Before any test module can import onnxruntime (some import it directly): the native 1DS
# telemetry uploader raced interpreter exit (recursive_mutex abort, rc=134).
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
# W0a (D-061): registration and admin HTTP default to closed/disabled (fail-closed). The Phase-0
# suites exercise the dev contract, so the test fixtures opt in explicitly, like compose.yaml does.
# Tests of the production contract pass `registration_mode="closed", admin_http="disabled"`.
os.environ.setdefault("HLM_REGISTRATION_MODE", "open")
os.environ.setdefault("HLM_ADMIN_HTTP", "enabled")

ROOT = Path(__file__).resolve().parents[1]
# CC-5: the suite never reaches a real provider. Strict cassette replay unless a test says otherwise
# (the G-L gates use scripted in-process stubs; recording is a manual, keyed step).
os.environ.setdefault("HLM_LLM_MODE", "replay")
os.environ.setdefault("HLM_LLM_CASSETTE_DIR", str(ROOT / "tests" / "cassettes" / "w2a"))
COMPOSE_DB_USER = "hlm"
COMPOSE_DB_PASSWORD = "hlm"
COMPOSE_DB_NAME = "hlm_test"
# Databases the suite must never truncate (the dev stack's live data). D-056.
PROTECTED_DB_NAMES = frozenset({"hlm"})

ConnectFactory = Callable[[], Awaitable[psycopg.AsyncConnection]]


@pytest.fixture(scope="session", autouse=True)
def _release_standalone_native_dependencies():
    """Drop service caches while Python and ONNX Runtime are still fully operational."""
    yield
    from hlmemo.core.read_service import _deps_for

    _deps_for.cache_clear()
    gc.collect()


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, text=True, capture_output=True, check=check)


def _compose_db_dsn() -> tuple[str, bool]:
    """Ensure the compose `db` service is healthy; return (dsn, started_by_us)."""
    already = _compose("ps", "-q", "--status", "running", "db", check=False).stdout.strip()
    _compose("up", "-d", "--wait", "db")
    port_line = _compose("port", "db", "5432").stdout.strip()  # e.g. 0.0.0.0:5432
    host, _, port = port_line.rpartition(":")
    host = "127.0.0.1" if host in ("0.0.0.0", "", "[::]") else host
    admin = f"postgresql://{COMPOSE_DB_USER}:{COMPOSE_DB_PASSWORD}@{host}:{port}/postgres"
    _wait_for_postgres(admin)
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (COMPOSE_DB_NAME,)).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE "{COMPOSE_DB_NAME}"')
    dsn = f"postgresql://{COMPOSE_DB_USER}:{COMPOSE_DB_PASSWORD}@{host}:{port}/{COMPOSE_DB_NAME}"
    return dsn, not already


def _refuse_protected(dsn: str) -> None:
    """Refuse a DSN whose database is protected: the autouse fixture truncates every table."""
    name = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname")
    if name in PROTECTED_DB_NAMES:
        raise pytest.UsageError(
            f"refusing to run tests against protected database {name!r}: the suite truncates every "
            "table. Point HLM_TEST_DSN at a dedicated database (e.g. hlm_verify)."
        )


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
        [sys.executable, "-m", "alembic", "upgrade", "main@head"],
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
    _refuse_protected(dsn)
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
TRUNCATE TABLE librarian_questions, llm_lineage_calls, jobs, links, embeddings, chunks, code_refs,
               memory_versions, events, device_project_grants, projects, llm_calls, llm_budget,
               llm_reservations
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
