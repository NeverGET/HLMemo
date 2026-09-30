"""Migration ``0010_billing_outcome`` (D-212) on a fresh database (``<test db>_b``).

It only widens the ``llm_calls.outcome`` CHECK by ``billing_or_quota``: before it the value is refused,
after it accepted, every older outcome is accepted on both sides (so code from before 0010, which never
writes the new value, runs unchanged on the migrated schema), an unknown outcome is still refused, and
downgrade turns billing rows back into ``http_error`` and restores the old CHECK.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from hlmemo.librarian.ledger import OUTCOMES, LedgerRow
from tests._heads import main_head
from tests.integration.test_migration_0006 import _alembic, fresh_dsn  # noqa: F401 - fixture by import

pytestmark = pytest.mark.integration

OLD_OUTCOMES = (
    "ok",
    "schema_retry_ok",
    "schema_fail",
    "http_error",
    "timeout",
    "budget_deferred",
    "breaker_open",
)


def _insert(conn: psycopg.Connection, outcome: str) -> None:
    conn.execute(
        "INSERT INTO llm_calls (call_id, task, profile, model_id, prompt_version, schema_version,"
        " mode, outcome) VALUES (%s, 't', 'p', 'm', 'v1', 's1', 'live', %s)",
        (uuid.uuid4(), outcome),
    )


def test_migration_0010_widens_the_outcome_check_and_round_trips(fresh_dsn: str) -> None:  # noqa: F811
    assert main_head() == "0010_billing_outcome"
    _alembic(fresh_dsn, "upgrade", "0009_memory_map")
    with psycopg.connect(fresh_dsn) as conn:
        for o in OLD_OUTCOMES:
            _insert(conn, o)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "billing_or_quota")
        conn.commit()
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == len(
            OLD_OUTCOMES
        )  # no data change
        for o in (*OLD_OUTCOMES, "billing_or_quota"):  # old values keep working (old code's writes)
            _insert(conn, o)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "made_up")
        conn.commit()
    # the ledger model and the CHECK agree
    assert set(OUTCOMES) == {*OLD_OUTCOMES, "billing_or_quota"}
    assert LedgerRow("t", "p", "m", "v", "s", "live", "billing_or_quota").outcome == "billing_or_quota"
    _alembic(fresh_dsn, "downgrade", "0009_memory_map")
    with psycopg.connect(fresh_dsn) as conn:
        assert (
            conn.execute("SELECT count(*) FROM llm_calls WHERE outcome = 'billing_or_quota'").fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT count(*) FROM llm_calls WHERE outcome = 'http_error'").fetchone()[0] == 3
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "billing_or_quota")
    _alembic(fresh_dsn, "upgrade", "main@head")


CHECK_SQL = (
    "SELECT conname, convalidated, pg_get_constraintdef(oid) FROM pg_constraint"
    " WHERE conrelid = 'llm_calls'::regclass AND contype = 'c' AND conname LIKE 'llm_calls_outcome_check%%'"
    " ORDER BY conname"
)


def test_migration_0010_final_constraint_name_and_rerun_after_a_leftover(
    fresh_dsn: str,  # noqa: F811
) -> None:
    """(R4.1 review Sol F-2) the staged swap ends with one validated ``llm_calls_outcome_check`` that
    accepts the new value and has no ``_v2`` left; a leftover staged constraint (an interrupted run)
    is replaced by the next run; downgrade is symmetric."""
    _alembic(fresh_dsn, "upgrade", "0009_memory_map")
    with psycopg.connect(fresh_dsn) as conn:
        for o in OLD_OUTCOMES * 3:  # pre-existing rows the validation scan must pass over
            _insert(conn, o)
        # an interrupted earlier run: the staged constraint exists, NOT VALID, with a wrong value list
        conn.execute(
            "ALTER TABLE llm_calls ADD CONSTRAINT llm_calls_outcome_check_v2 CHECK (outcome = 'ok') NOT VALID"
        )
        conn.commit()
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        rows = conn.execute(CHECK_SQL).fetchall()
        assert [(r[0], r[1]) for r in rows] == [("llm_calls_outcome_check", True)]
        assert "billing_or_quota" in rows[0][2]
        _insert(conn, "billing_or_quota")  # the new value is accepted
        assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == len(OLD_OUTCOMES) * 3 + 1
        conn.commit()
    _alembic(fresh_dsn, "downgrade", "0009_memory_map")
    with psycopg.connect(fresh_dsn) as conn:
        rows = conn.execute(CHECK_SQL).fetchall()
        assert [(r[0], r[1]) for r in rows] == [("llm_calls_outcome_check", True)]
        assert "billing_or_quota" not in rows[0][2]
        assert conn.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == len(OLD_OUTCOMES) * 3 + 1
