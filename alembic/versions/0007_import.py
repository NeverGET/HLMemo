"""Import provenance + code references + the D-015 skeleton-card backfill (W1.5; D-069).

Revision ID: 0007_import
Revises: 0006_librarian (branch `main`; the orchestrator links 0008 after it, D-069)

Adds, never rewrites (CC-1; Sol consult 40 #6: no table rewrite):
  * ``memory_versions.source jsonb NULL`` — W1.5 provenance ``{system, path, sha256, mtime?,
    commit?, commit_date?}`` (mtime/commit_date are provenance only, never valid time);
  * ``memory_versions.source_key text NULL`` — a plain column that every insert derives in SQL
    from the row's own ``source`` (``db/write_queries.source_key_sql``), pinned by
    CHECK ``mv_source_key_derived`` (added NOT VALID, validated outside the DDL transaction, so
    neither a rewrite nor a long ACCESS EXCLUSIVE scan is needed) and CHECK ``mv_source_shape``;
  * EXCLUDE ``mv_one_source_owner``: at most one CURRENT logical item per (project, source_key)
    (Sol 40 #2: a database guarantee for every write path; several current valid-time segments of
    the SAME logical item stay legal);
  * index ``mv_source_key (project_id, source_key)`` over current rows (built CONCURRENTLY);
  * ``code_refs(version_id, path, commit NULL)`` — the ``describes`` projection (rebuilt by replay).

Then the D-015 backfill (carried item): every user project (not archived, not one of the reserved
system projects of 0006) whose ``card_logical_id`` has no version gets
the deterministic skeleton card as an ordinary ``write`` event by device 1, written by the SAME
code path as ``ops project create`` (``core/skeleton_card.py`` → ``core/write_service``). It runs
AFTER the DDL committed, one short transaction per project (Sol 40 #5: the tokenizer never runs
under a table lock), and is idempotent (a project that already has a card version is skipped;
the request_id is deterministic, so a re-sent write is a replay).
Needs the e5 tokenizer and the o200k tiktoken cache (the image has both) only when a project
lacks a card.

Recovery: every step is idempotent (IF NOT EXISTS, guarded constraints, INVALID-index cleanup,
per-project backfill); an upgrade interrupted anywhere completes when re-run. The downgrade is one
guarded transaction and refuses while any version carries a source or a code reference (dropping
the columns would silently lose projection data that only a replay could restore).
"""

from __future__ import annotations

import asyncio
import os

from alembic import op

revision = "0007_import"
down_revision = "0006_librarian"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "3s"
RETRY_HINT = (
    "0007_import: lock_timeout ({t}) waiting for {what}; nothing was left half-applied. "
    "Retry `alembic upgrade` at a quieter moment (deploy stops writers first)."
)
# Pinned against db/write_queries.source_key_sql by tests/unit/test_import_contract.py.
SOURCE_KEY_EXPR = "CASE WHEN source IS NULL THEN NULL ELSE (source ->> 'system') || ':' || (source ->> 'path') END"

UPGRADE = rf"""
ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS source jsonb;
ALTER TABLE memory_versions ADD COLUMN IF NOT EXISTS source_key text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'memory_versions'::regclass AND conname = 'mv_source_key_derived') THEN
    ALTER TABLE memory_versions ADD CONSTRAINT mv_source_key_derived
      CHECK (source_key IS NOT DISTINCT FROM ({SOURCE_KEY_EXPR})) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'memory_versions'::regclass AND conname = 'mv_source_shape') THEN
    ALTER TABLE memory_versions ADD CONSTRAINT mv_source_shape
      CHECK (source IS NULL OR (jsonb_typeof(source) = 'object'
                                AND jsonb_typeof(source -> 'system') = 'string'
                                AND jsonb_typeof(source -> 'path') = 'string'
                                AND length(source ->> 'path') BETWEEN 1 AND 512
                                AND jsonb_typeof(source -> 'sha256') = 'string')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'memory_versions'::regclass AND conname = 'mv_one_source_owner') THEN
    ALTER TABLE memory_versions ADD CONSTRAINT mv_one_source_owner
      EXCLUDE USING gist (project_id WITH =, source_key WITH =, logical_id WITH <>)
      WHERE (source_key IS NOT NULL AND superseded_at = 'infinity');
  END IF;
END $$;

-- The `describes` projection: one row per (version, path); `commit` pins the source commit the
-- importer read (NULL when unknown). Survivor segments copy their base version's rows.
CREATE TABLE IF NOT EXISTS code_refs (
  version_id bigint NOT NULL REFERENCES memory_versions,
  path       text NOT NULL CHECK (length(path) BETWEEN 1 AND 256),
  commit     text CHECK (commit IS NULL OR commit ~ '^[0-9a-f]{{7,64}}$'),
  PRIMARY KEY (version_id, path)
);
CREATE INDEX IF NOT EXISTS code_refs_path ON code_refs (path);
"""

SOURCE_INDEX = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS mv_source_key ON memory_versions (project_id, source_key)"
    " WHERE source_key IS NOT NULL AND superseded_at = 'infinity'"
)
LEFTOVER = """
SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'mv_source_key' AND NOT i.indisvalid
"""

DOWNGRADE = """
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
DROP INDEX IF EXISTS mv_source_key;
DROP TABLE IF EXISTS code_refs;
ALTER TABLE memory_versions DROP CONSTRAINT IF EXISTS mv_one_source_owner;
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


def _dsn() -> str:
    """The psycopg DSN of alembic's own engine (the backfill uses its own async connection)."""
    url = op.get_bind().engine.url
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")  # the table DDL, one tx
    try:
        bind.exec_driver_sql(UPGRADE)
    except Exception as exc:
        raise RuntimeError(RETRY_HINT.format(t=LOCK_TIMEOUT, what="the table DDL")) from exc
    with op.get_context().autocommit_block():  # commits the DDL first
        bind = op.get_bind()
        _fault("validate")
        bind.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        try:
            # SHARE UPDATE EXCLUSIVE: reads and writes continue while the rows are checked.
            for name in ("mv_source_key_derived", "mv_source_shape"):
                try:
                    bind.exec_driver_sql(f"ALTER TABLE memory_versions VALIDATE CONSTRAINT {name}")
                except Exception as exc:
                    raise RuntimeError(RETRY_HINT.format(t=LOCK_TIMEOUT, what=f"validating {name}")) from exc
        finally:
            bind.exec_driver_sql("RESET lock_timeout")
        if bind.exec_driver_sql(LEFTOVER).fetchall():  # an interrupted concurrent build
            bind.exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS mv_source_key")
        bind.exec_driver_sql(SOURCE_INDEX)
        _fault("backfill")
        from hlmemo.core.skeleton_card import backfill_skeleton_cards

        asyncio.run(backfill_skeleton_cards(_dsn()))


def downgrade() -> None:
    # ONE transaction together with alembic's version update (0006 pattern): all or nothing.
    # The skeleton-card events and versions stay: they are ordinary Phase-0 `write` data.
    bind = op.get_bind()
    bind.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    try:
        bind.exec_driver_sql(DOWNGRADE)
    except Exception as exc:
        if "downgrade refused" in str(exc):
            raise
        raise RuntimeError(RETRY_HINT.format(t=LOCK_TIMEOUT, what="the downgrade DDL")) from exc
    _fault("downgrade")
