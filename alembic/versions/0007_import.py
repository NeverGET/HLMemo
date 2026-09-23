"""Import provenance + code references + the D-015 skeleton-card backfill (W1.5; D-069).

Revision ID: 0007_import
Revises: 0006_librarian (branch `main`; the orchestrator links 0008 after it, D-069)

Adds, never rewrites (CC-1), and never holds a lock that stops writers for long (Sol 42 #1):
  * ``memory_versions.source jsonb NULL`` — W1.5 provenance ``{system, path, sha256, mtime?,
    commit?, commit_date?}`` (mtime/commit_date are provenance only, never valid time);
  * ``memory_versions.source_key text NULL`` — a plain column (no table rewrite) that every insert
    derives in SQL from the row's own ``source`` (``db/write_queries.source_key_sql``), pinned by
    CHECK ``mv_source_key_derived``; CHECK ``mv_source_shape`` requires the keys ``system``,
    ``path`` and ``sha256`` as non-empty strings (Sol 42 #2: a missing key is FALSE, never
    UNKNOWN, so a sourced row always has a non-NULL key). Both are added NOT VALID and validated
    under SHARE UPDATE EXCLUSIVE (reads and writes continue);
  * UNIQUE index ``mv_source_owner (project_id, source_key) WHERE source_key IS NOT NULL AND
    superseded_at = 'infinity' AND valid_to = 'infinity'`` built CONCURRENTLY: at most one
    logical item per (project, key) holds the key with an open, current row (one logical item has
    at most one such row: its current valid-time segments never overlap). A closed item (valid
    time ended, W1.5 ``close``) releases its key;
  * ``code_refs(version_id, path, commit NULL)`` — the ``describes`` projection (rebuilt by replay).

Lock discipline: every DDL statement runs alone in autocommit mode with ``lock_timeout`` 500 ms
and is retried (idempotent: IF NOT EXISTS / guarded), so a writer never queues behind a waiting
ALTER for more than ~0.5 s even when a long transaction holds the table; the ACCESS EXCLUSIVE
steps are catalog-only (no scan, no rewrite). The CONCURRENTLY build and the VALIDATE steps never
block INSERT/UPDATE. ``tests/integration/test_migration_0007.py`` measures writer latency during
the upgrade with a long reader holding the table.

Then the D-015 backfill (carried item): every live user project (not one of the reserved system
projects of 0006) whose ``card_logical_id`` has no version gets the deterministic skeleton card as
an ordinary ``write`` event by device 1 (``core/skeleton_card.py`` → ``core/write_service``), one
short transaction per project after the DDL; idempotent. Needs the e5 tokenizer and the o200k
tiktoken cache (the image has both) only when a project lacks a card.

The downgrade is one guarded transaction and refuses while any version carries a source or a code
reference (dropping the columns would silently lose projection data only a replay could restore).
"""

from __future__ import annotations

import asyncio
import os
import time

from alembic import op

revision = "0007_import"
down_revision = "0006_librarian"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "500ms"  # the longest a writer can queue behind one waiting catalog-only step
LONG_LOCK_TIMEOUT = "60s"  # CONCURRENTLY / VALIDATE: they only wait on other DDL, never on writers
ATTEMPTS = 80
RETRY_SLEEP_S = 0.25
RETRY_HINT = (
    "0007_import: gave up after {n} lock_timeout ({t}) attempts waiting for {what}; nothing was left "
    "half-applied. Retry `alembic upgrade` at a quieter moment."
)
# Pinned against db/write_queries.source_key_sql by tests/unit/test_importers.py.
SOURCE_KEY_EXPR = "CASE WHEN source IS NULL THEN NULL ELSE (source ->> 'system') || ':' || (source ->> 'path') END"
SOURCE_SHAPE_EXPR = """source IS NULL OR COALESCE(
        jsonb_typeof(source) = 'object'
        AND source ? 'system' AND source ? 'path' AND source ? 'sha256'
        AND jsonb_typeof(source -> 'system') = 'string'
        AND jsonb_typeof(source -> 'path') = 'string'
        AND jsonb_typeof(source -> 'sha256') = 'string'
        AND length(source ->> 'system') BETWEEN 1 AND 32
        AND length(source ->> 'path') BETWEEN 1 AND 512
        AND length(source ->> 'sha256') = 64
        AND source_key IS NOT NULL, false)"""
OWNER_INDEX = "mv_source_owner"
OWNER_PREDICATE = "source_key IS NOT NULL AND superseded_at = 'infinity' AND valid_to = 'infinity'"

CATALOG_STEPS: tuple[tuple[str, str], ...] = (
    ("column source", "ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS source jsonb"),
    ("column source_key", "ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS source_key text"),
    (
        "CHECK mv_source_key_derived",
        f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'memory_versions'::regclass
                  AND conname = 'mv_source_key_derived') THEN
    ALTER TABLE memory_versions ADD CONSTRAINT mv_source_key_derived
      CHECK (source_key IS NOT DISTINCT FROM ({SOURCE_KEY_EXPR})) NOT VALID;
  END IF;
END $$""",
    ),
    (
        "CHECK mv_source_shape",
        f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'memory_versions'::regclass
                  AND conname = 'mv_source_shape') THEN
    ALTER TABLE memory_versions ADD CONSTRAINT mv_source_shape CHECK ({SOURCE_SHAPE_EXPR}) NOT VALID;
  END IF;
END $$""",
    ),
    (
        "table code_refs",
        """CREATE TABLE IF NOT EXISTS code_refs (
  version_id bigint NOT NULL REFERENCES memory_versions,
  path       text NOT NULL CHECK (length(path) BETWEEN 1 AND 256),
  commit     text CHECK (commit IS NULL OR commit ~ '^[0-9a-f]{7,64}$'),
  PRIMARY KEY (version_id, path)
)""",
    ),
    ("index code_refs_path", "CREATE INDEX IF NOT EXISTS code_refs_path ON code_refs (path)"),
)

LEFTOVER = f"""
SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = '{OWNER_INDEX}' AND NOT i.indisvalid
"""

DOWNGRADE = f"""
DO $$
BEGIN
  IF to_regclass('code_refs') IS NOT NULL AND EXISTS (SELECT 1 FROM code_refs) THEN
    RAISE EXCEPTION '0007_import downgrade refused: code_refs has rows (replay-only data)';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_name = 'memory_versions' AND column_name = 'source')
     AND EXISTS (SELECT 1 FROM memory_versions WHERE source IS NOT NULL) THEN
    RAISE EXCEPTION '0007_import downgrade refused: memory_versions rows carry a source';
  END IF;
END $$;
DROP INDEX IF EXISTS {OWNER_INDEX};
DROP TABLE IF EXISTS code_refs;
ALTER TABLE memory_versions DROP CONSTRAINT IF EXISTS mv_source_shape;
ALTER TABLE memory_versions DROP CONSTRAINT IF EXISTS mv_source_key_derived;
ALTER TABLE memory_versions DROP COLUMN IF EXISTS source_key;
ALTER TABLE memory_versions DROP COLUMN IF EXISTS source;
"""


def _fault(step: str) -> None:
    """Crash injection for the recovery tests ONLY (``HLM_TESTING=1`` AND
    ``HLM_MIGRATION_FAULT=0007:<step>``), like 0006."""
    if os.environ.get("HLM_TESTING") != "1":
        return
    if os.environ.get("HLM_MIGRATION_FAULT") == f"0007:{step}":
        raise RuntimeError(f"0007_import: injected fault at {step}")


def _lock_timeout(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", exc)
    return getattr(orig, "sqlstate", None) == "55P03"  # lock_not_available


def _retry(bind, what: str, sql: str, *, timeout: str = LOCK_TIMEOUT, before=None) -> None:  # noqa: ANN001
    """One autocommit statement under ``lock_timeout``, retried while the lock is not available."""
    for n in range(1, ATTEMPTS + 1):
        try:
            if before is not None:
                before()
            bind.exec_driver_sql(f"SET lock_timeout = '{timeout}'")
            bind.exec_driver_sql(sql)
            return
        except Exception as exc:
            if not _lock_timeout(exc) or n == ATTEMPTS:
                if _lock_timeout(exc):
                    raise RuntimeError(RETRY_HINT.format(n=n, t=timeout, what=what)) from exc
                raise
            time.sleep(RETRY_SLEEP_S)
        finally:
            bind.exec_driver_sql("RESET lock_timeout")


def _dsn() -> str:
    """The psycopg DSN of alembic's own engine (the backfill uses its own async connection)."""
    url = op.get_bind().engine.url
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def upgrade() -> None:
    with op.get_context().autocommit_block():  # every step is its own short transaction
        bind = op.get_bind()
        for what, sql in CATALOG_STEPS:
            _retry(bind, what, sql)
        _fault("validate")
        for name in ("mv_source_key_derived", "mv_source_shape"):
            _retry(
                bind,
                f"validating {name}",
                f"ALTER TABLE memory_versions VALIDATE CONSTRAINT {name}",
                timeout=LONG_LOCK_TIMEOUT,
            )

        def drop_invalid_leftover() -> None:  # an interrupted concurrent build leaves an INVALID index
            if bind.exec_driver_sql(LEFTOVER).fetchall():
                bind.exec_driver_sql(f"DROP INDEX CONCURRENTLY IF EXISTS {OWNER_INDEX}")

        _fault("index")
        _retry(
            bind,
            f"index {OWNER_INDEX}",
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {OWNER_INDEX}"
            f" ON memory_versions (project_id, source_key) WHERE {OWNER_PREDICATE}",
            timeout=LONG_LOCK_TIMEOUT,
            before=drop_invalid_leftover,
        )
        _fault("backfill")
        from hlmemo.core.skeleton_card import backfill_skeleton_cards

        asyncio.run(backfill_skeleton_cards(_dsn()))


def downgrade() -> None:
    # ONE transaction together with alembic's version update (0006 pattern): all or nothing.
    # The skeleton-card events and versions stay: they are ordinary Phase-0 `write` data.
    bind = op.get_bind()
    bind.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
    try:
        bind.exec_driver_sql(DOWNGRADE)
    except Exception as exc:
        if "downgrade refused" in str(exc):
            raise
        raise RuntimeError(RETRY_HINT.format(n=1, t="3s", what="the downgrade DDL")) from exc
    _fault("downgrade")
