"""Title lexical index (D-055): item titles/paths become an RRF candidate list.

Revision ID: 0003_title_lexical
Revises: 0001_phase0 (branch `phase0`; applied by `alembic upgrade phase0@head`)

Adds the IMMUTABLE SQL function ``hlm_title_norm(text)`` — NFKC, ``lower()`` under the builtin
``pg_c_utf8`` collation (PostgreSQL 17 builtin provider: Unicode simple case mapping, independent
of the database locale and of libc/ICU versions), the ``casefold()`` differences ß→ss and final
ς→σ, ı→i, NFD, combining marks dropped — which also turns path/identifier separators
(``/ . _ - # § : \\``) into spaces so ``docs/api/auth.md`` indexes as ``docs api auth md``; and a
GIN expression index over ``to_tsvector('simple', hlm_title_norm(title))`` on ``memory_versions``.
The query side applies the *same* function to the raw query tokens (``title_candidates``), so
title and query are folded identically by construction (D-055, Sol review #2).

Online-safe: no column, no table rewrite; the index is built with ``CREATE INDEX CONCURRENTLY``
outside a transaction (writes continue). An INVALID leftover of an interrupted build is dropped
with ``DROP INDEX CONCURRENTLY`` first, also outside a transaction. Reversible: downgrade drops
the index concurrently, then the function.
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
           normalize(
             replace(replace(replace(lower(normalize(t, NFKC) COLLATE pg_c_utf8), 'ß', 'ss'), 'ς', 'σ'), 'ı', 'i'),
             NFD),
           '[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]', '', 'g'),
         '[/._#§:\\-]+', ' ', 'g');
"""

INVALID = """
SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'mv_title_tsv' AND NOT i.indisvalid
"""

INDEX = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS mv_title_tsv ON memory_versions
  USING gin (to_tsvector('simple'::regconfig, hlm_title_norm(title)));
"""

DROP_INDEX = "DROP INDEX CONCURRENTLY IF EXISTS mv_title_tsv;"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(FUNCTION)
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        if bind.exec_driver_sql(INVALID).first() is not None:  # interrupted earlier build
            bind.exec_driver_sql(DROP_INDEX)
        bind.exec_driver_sql(INDEX)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql(DROP_INDEX)
    op.get_bind().exec_driver_sql("DROP FUNCTION IF EXISTS hlm_title_norm(text);")
