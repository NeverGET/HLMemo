"""Title lexical index (D-055): item titles/paths become an RRF candidate list.

Revision ID: 0003_title_lexical
Revises: 0001_phase0 (branch `phase0`; applied by `alembic upgrade phase0@head`)

Adds the IMMUTABLE SQL function ``hlm_title_norm(text)`` — the SQL mirror of
``core/normalize.py::normalize`` (NFKC, lower, ß→ss, ı→i, NFD, combining marks dropped) that
also turns path/identifier separators (``/ . _ - # § : \\``) into spaces so ``docs/api/auth.md``
indexes as ``docs api auth md`` — and a GIN expression index over
``to_tsvector('simple', hlm_title_norm(title))`` on ``memory_versions``.

Online-safe: no column, no table rewrite; the index is built with ``CREATE INDEX CONCURRENTLY``
outside a transaction (writes continue). Reversible: downgrade drops the index concurrently, then
the function. ``IF NOT EXISTS``/``OR REPLACE`` make a re-run after an interrupted concurrent build
converge (an INVALID leftover index is dropped first).
"""

from __future__ import annotations

from alembic import op

revision = "0003_title_lexical"
down_revision = "0001_phase0"
branch_labels = None
depends_on = None

FUNCTION = r"""
CREATE OR REPLACE FUNCTION hlm_title_norm(t text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
RETURN regexp_replace(
         regexp_replace(
           normalize(replace(replace(lower(normalize(t, NFKC)), 'ß', 'ss'), 'ı', 'i'), NFD),
           '[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]', '', 'g'),
         '[/._#§:\\-]+', ' ', 'g');
"""

DROP_INVALID = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
             WHERE c.relname = 'mv_title_tsv' AND NOT i.indisvalid) THEN
    EXECUTE 'DROP INDEX mv_title_tsv';
  END IF;
END $$;
"""

INDEX = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS mv_title_tsv ON memory_versions
  USING gin (to_tsvector('simple'::regconfig, hlm_title_norm(title)));
"""


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(FUNCTION)
    bind.exec_driver_sql(DROP_INVALID)
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql(INDEX)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS mv_title_tsv;")
    op.get_bind().exec_driver_sql("DROP FUNCTION IF EXISTS hlm_title_norm(text);")
