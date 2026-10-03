"""Migration ``0011_question_withdrawn`` on a fresh database (``<test db>_b``).

It only widens the ``librarian_questions.status`` CHECK by ``withdrawn``: before it the value is
refused, after it accepted, every older status is accepted on both sides (code from before 0011 never
writes the new value), an unknown status is still refused. The swap ends with ONE validated
``librarian_questions_status_check`` and no staged ``_v2``; a leftover staged constraint (an
interrupted run) is replaced. Downgrade REFUSES while a question is ``withdrawn`` (those rows are
projections of withdraw events) and leaves the schema exactly as it was; without such rows it
restores the old CHECK.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import psycopg
import pytest

from tests._heads import main_head
from tests.integration.test_migration_0006 import ROOT, _alembic, fresh_dsn  # noqa: F401 - fixture by import

pytestmark = pytest.mark.integration

OLD_STATUSES = (
    "open",
    "approved",
    "rejected",
    "superseded",
    "answered",
    "applied",
    "expired",
    "authority_lost",
    "accepted_pending",
)
STAGED = "librarian_questions_status_check_v2"
CHECK_SQL = (
    "SELECT conname, convalidated, pg_get_constraintdef(oid) FROM pg_constraint"
    " WHERE conrelid = 'librarian_questions'::regclass AND contype = 'c'"
    " AND conname LIKE 'librarian_questions_status_check%%' ORDER BY conname"
)
INSERT = (
    "INSERT INTO librarian_questions (question_id, job_key, batch_id, project_id, project_ids, kind,"
    " subject_clues, subject_version_ids, proposal, status, created_at, source_event_id)"
    " VALUES (gen_random_uuid(), 'k', gen_random_uuid(), %s, ARRAY[%s]::bigint[], 'link', '{}', '{}', '{}',"
    " %s, now(), %s)"
)


def _event(conn: psycopg.Connection) -> tuple[int, int]:
    dev = conn.execute("SELECT device_id FROM devices WHERE name = 'librarian'").fetchone()[0]
    pid = conn.execute("SELECT project_id FROM projects WHERE slug = 'hlm-global'").fetchone()[0]
    ev = conn.execute(
        "INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,"
        " occurred_at) VALUES (%s, %s, 't', gen_random_uuid(), 'librarian', '{}', 'x', now())"
        " RETURNING event_id",
        (pid, dev),
    ).fetchone()[0]
    return pid, ev


def _insert(conn: psycopg.Connection, status: str) -> None:
    pid, ev = _event(conn)
    conn.execute(INSERT, (pid, pid, status, ev))


def _check(conn: psycopg.Connection) -> list[tuple[str, bool, bool]]:
    """``[(name, validated, accepts withdrawn)]`` of the status CHECK constraints."""
    return [(n, v, "'withdrawn'" in d) for n, v, d in conn.execute(CHECK_SQL).fetchall()]


def test_migration_0011_widens_the_status_check_and_round_trips(fresh_dsn: str) -> None:  # noqa: F811
    assert main_head() == "0011_question_withdrawn"
    _alembic(fresh_dsn, "upgrade", "0010_billing_outcome")
    with psycopg.connect(fresh_dsn) as conn:
        for s in OLD_STATUSES:
            _insert(conn, s)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "withdrawn")
        conn.commit()
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM librarian_questions").fetchone()[0] == len(OLD_STATUSES)
        assert _check(conn) == [("librarian_questions_status_check", True, True)]
        for s in (*OLD_STATUSES, "withdrawn"):  # old values keep working (old code's writes)
            _insert(conn, s)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "made_up")
        conn.execute("DELETE FROM librarian_questions WHERE status = 'withdrawn'")
        conn.commit()
    _alembic(fresh_dsn, "downgrade", "0010_billing_outcome")  # no withdrawn row: the old CHECK
    with psycopg.connect(fresh_dsn) as conn:
        assert _check(conn) == [("librarian_questions_status_check", True, False)]
        assert conn.execute("SELECT count(*) FROM librarian_questions").fetchone()[0] == 2 * len(OLD_STATUSES)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            _insert(conn, "withdrawn")
    _alembic(fresh_dsn, "upgrade", "main@head")


def test_migration_0011_downgrade_refuses_while_questions_are_withdrawn(fresh_dsn: str) -> None:  # noqa: F811
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        _insert(conn, "withdrawn")
        _insert(conn, "accepted_pending")
        conn.commit()
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0010_billing_outcome"],
        cwd=ROOT,
        env={**os.environ, "HLM_DB_DSN": fresh_dsn},
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0 and "downgrade refused" in proc.stderr
    with psycopg.connect(fresh_dsn) as conn:  # nothing changed: version, rows, the one CHECK
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0011_question_withdrawn"
        )
        assert _check(conn) == [("librarian_questions_status_check", True, True)]  # no staged _v2 left
        assert conn.execute(
            "SELECT count(*) FROM librarian_questions WHERE status = 'withdrawn'"
        ).fetchone() == (1,)
        _insert(conn, "withdrawn")  # the schema still takes new withdraws
        conn.execute("DELETE FROM librarian_questions WHERE status = 'withdrawn'")
        conn.commit()
    _alembic(fresh_dsn, "downgrade", "0010_billing_outcome")
    with psycopg.connect(fresh_dsn) as conn:
        assert _check(conn) == [("librarian_questions_status_check", True, False)]


def _downgrade(dsn: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0010_billing_outcome"],
        cwd=ROOT,
        env={**os.environ, "HLM_DB_DSN": dsn},
        text=True,
        capture_output=True,
        timeout=120,
    )


def _late_reader(dsn: str, stop: threading.Event, held: threading.Event) -> None:
    """A reader that takes ``ACCESS SHARE`` on the table the moment a staged ``_v2`` is visible to
    other sessions (i.e. committed on its own) and holds it for 6 s: the contention that made the old
    refusal's cleanup ``DROP`` time out (review 107 #2)."""
    with psycopg.connect(dsn) as conn:
        deadline = time.monotonic() + 30
        while not stop.is_set() and time.monotonic() < deadline:
            if conn.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", (STAGED,)).fetchone():
                conn.execute("SELECT count(*) FROM librarian_questions")  # ACCESS SHARE until rollback
                held.set()
                stop.wait(6)
                break
            conn.rollback()
        conn.rollback()


@pytest.mark.parametrize("reader", ["holds_throughout", "late"])
def test_migration_0011_contended_refusal_leaves_no_staged_constraint(fresh_dsn: str, reader: str) -> None:  # noqa: F811
    """Review 107 #2: a downgrade refused under contention (another session holds ACCESS SHARE) leaves
    the schema exactly as it was: no ``_v2``, alembic at 0011, and a withdraw still succeeds. The old
    downgrade committed its staged OLD constraint alone, then the refusal's cleanup DROP timed out
    behind the reader, and ``_v2`` rejected every later withdraw."""
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        _insert(conn, "withdrawn")
        _insert(conn, "accepted_pending")
        conn.commit()
    stop, held = threading.Event(), threading.Event()
    if reader == "holds_throughout":
        blocker = psycopg.connect(fresh_dsn)
        blocker.execute("SELECT count(*) FROM librarian_questions")  # ACCESS SHARE in an open transaction
        try:
            proc = _downgrade(fresh_dsn)
        finally:
            blocker.rollback()
            blocker.close()
    else:
        thread = threading.Thread(target=_late_reader, args=(fresh_dsn, stop, held), daemon=True)
        thread.start()
        try:
            proc = _downgrade(fresh_dsn)
        finally:
            stop.set()
            thread.join(30)
        assert not held.is_set(), "a staged constraint was visible to other sessions"
    assert proc.returncode != 0
    with psycopg.connect(fresh_dsn) as conn:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0011_question_withdrawn"
        )
        assert _check(conn) == [("librarian_questions_status_check", True, True)]  # no staged _v2 left
        conn.execute("UPDATE librarian_questions SET status = 'withdrawn' WHERE status = 'accepted_pending'")
        conn.commit()  # a withdraw still succeeds
        assert conn.execute(
            "SELECT count(*) FROM librarian_questions WHERE status = 'withdrawn'"
        ).fetchone() == (2,)


def test_migration_0011_rerun_after_a_leftover_staged_constraint(fresh_dsn: str) -> None:  # noqa: F811
    _alembic(fresh_dsn, "upgrade", "0010_billing_outcome")
    with psycopg.connect(fresh_dsn) as conn:
        for s in OLD_STATUSES:
            _insert(conn, s)
        # an interrupted earlier run: the staged constraint exists, NOT VALID, with a wrong value list
        conn.execute(
            "ALTER TABLE librarian_questions ADD CONSTRAINT librarian_questions_status_check_v2"
            " CHECK (status = 'open') NOT VALID"
        )
        conn.commit()
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert _check(conn) == [("librarian_questions_status_check", True, True)]
        _insert(conn, "withdrawn")
        conn.commit()
