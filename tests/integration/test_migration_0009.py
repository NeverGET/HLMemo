"""Migration ``0009_memory_map`` (D-136) on a fresh database (``<test db>_b``).

It only CREATES the L2 summary cache ``memory_map_summaries`` (additive, CC-1); downgrade to
``0008_librarian_tasks`` drops exactly that table and upgrade round-trips. The cache is not a replay
projection: ``replay.PROJECTION_TABLES`` does not name it and nothing references it.
"""

from __future__ import annotations

import psycopg
import pytest

from hlmemo.db.replay import PROJECTION_TABLES
from tests._heads import main_head
from tests.integration.test_migration_0006 import _alembic, fresh_dsn  # noqa: F401 - fixture by import

pytestmark = pytest.mark.integration

COLUMNS = {
    "project_id",
    "source_key",
    "digest",
    "member_ids",
    "summary",
    "status",
    "failures",
    "profile",
    "prompt_version",
    "updated_at",
}


def _tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def test_migration_0009_creates_only_the_cache_and_round_trips(fresh_dsn: str) -> None:  # noqa: F811
    assert main_head() == "0010_billing_outcome"
    _alembic(fresh_dsn, "upgrade", "0008_librarian_tasks")
    with psycopg.connect(fresh_dsn) as conn:
        before = _tables(conn)
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert _tables(conn) - before == {"memory_map_summaries"}
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'memory_map_summaries'"
            ).fetchall()
        }
        assert cols == COLUMNS
        pid = conn.execute("SELECT project_id FROM projects WHERE slug = 'hlm-global'").fetchone()[0]
        conn.execute(
            "INSERT INTO memory_map_summaries (project_id, source_key, digest, member_ids, summary)"
            " VALUES (%s, 'markdown:docs/x.md', %s, '{1,2}', 'A summary.')",
            (pid, "a" * 64),
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO memory_map_summaries (project_id, source_key, digest, member_ids)"
                " VALUES (%s, 'k2', 'not-a-digest', '{}')",
                (pid,),
            )
        refs = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE confrelid = 'memory_map_summaries'::regclass"
        ).fetchone()[0]
        assert refs == 0  # nothing references the cache: dropping or truncating it is always safe
        conn.commit()
    assert "memory_map_summaries" not in PROJECTION_TABLES  # not rebuilt (or truncated) by replay
    _alembic(fresh_dsn, "downgrade", "0008_librarian_tasks")
    with psycopg.connect(fresh_dsn) as conn:
        assert _tables(conn) == before
    _alembic(fresh_dsn, "upgrade", "main@head")
    with psycopg.connect(fresh_dsn) as conn:
        assert "memory_map_summaries" in _tables(conn)
