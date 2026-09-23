"""Title folding fix (D-055, Sol review 33 #2): bring ``hlm_title_norm`` to its final definition.

Revision ID: 0004_title_norm_fold
Revises: 0003_title_lexical (branch `phase0`)

Databases that ran the first cut of ``0003_title_lexical`` carry a ``hlm_title_norm`` whose
``lower()`` followed the database locale and did not fold the final sigma, so a title could miss
a query that ``normalize()`` treats as equal. This revision (re)creates the function exactly as
``0003`` now defines it (``pg_c_utf8`` lower, ß→ss, ς→σ, ı→i, NFD, marks dropped) and rebuilds
``mv_title_tsv`` with ``REINDEX INDEX CONCURRENTLY`` (online: no write lock; on a fresh database
it is a cheap no-op rebuild). An INVALID ``mv_title_tsv_ccnew`` leftover of an interrupted reindex
is dropped concurrently first. Downgrade restores the first-cut body and reindexes the same way.
"""

from __future__ import annotations

from alembic import op

revision = "0004_title_norm_fold"
down_revision = "0003_title_lexical"
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
           '[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]', '', 'g'),
         '[/._#§:\\-]+', ' ', 'g');
"""

FIRST_CUT = r"""
CREATE OR REPLACE FUNCTION hlm_title_norm(t text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
RETURN regexp_replace(
         regexp_replace(
           normalize(replace(replace(lower(normalize(t, NFKC)), 'ß', 'ss'), 'ı', 'i'), NFD),
           '[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]', '', 'g'),
         '[/._#§:\\-]+', ' ', 'g');
"""

LEFTOVER = """
SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE starts_with(c.relname::text, 'mv_title_tsv_ccnew') AND NOT i.indisvalid
"""


def _swap(body: str) -> None:
    op.get_bind().exec_driver_sql(body)
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        for (name,) in bind.exec_driver_sql(LEFTOVER).fetchall():
            bind.exec_driver_sql(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
        bind.exec_driver_sql("REINDEX INDEX CONCURRENTLY mv_title_tsv")


def upgrade() -> None:
    _swap(FUNCTION)


def downgrade() -> None:
    _swap(FIRST_CUT)
