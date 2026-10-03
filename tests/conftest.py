"""Shared test infrastructure (PHASE0-SPEC §7): session-scoped Postgres + migrated schema.

Database selection: `HLM_TEST_DSN` names the database, always (CI / `compose --profile test` /
`make test`). Without it the session stops at the first test that needs a database, with a usage
error (exit code 4): there is NO fallback to any compose database (D-236 lesson "never fall back to
the dev DB": a fallback once ran the suite against a shared stack). Tests truncate every table, so a
DSN naming a protected database (the dev stack's `hlm`) is refused too.
`alembic upgrade main@head` is applied once per session (idempotent).
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


MISSING_DSN = (
    "HLM_TEST_DSN is not set: the test suite needs an explicit, dedicated database "
    "(e.g. postgresql://hlm:hlm@127.0.0.1:<port>/hlm_test on your own compose project). "
    "There is no fallback to any compose database."
)


def _require_test_dsn() -> str:
    """``HLM_TEST_DSN`` or a session stop (``pytest.exit``, exit code 4) before any database work."""
    dsn = os.environ.get("HLM_TEST_DSN", "").strip()
    if not dsn:
        pytest.exit(MISSING_DSN, returncode=pytest.ExitCode.USAGE_ERROR)
    return dsn


@pytest.fixture(scope="session")
def db_dsn() -> str:
    """Migrated Postgres DSN for the whole session."""
    dsn = _require_test_dsn()
    _refuse_protected(dsn)
    _wait_for_postgres(dsn)
    _migrate(dsn)
    return dsn


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
TRUNCATE TABLE librarian_questions, librarian_batches, version_signals, llm_lineage_calls, jobs, links,
               embeddings, chunks, code_refs, memory_map_summaries,
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
