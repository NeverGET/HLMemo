"""DEFERRED — HNSW index on embeddings (PHASE0-SPEC §1 `0002_hnsw`, D-024 (17)).

Revision ID: 0002_hnsw
Revises: None — this is the base of its OWN branch, label `hnsw`, so it is never part of
the `phase0` chain and `alembic upgrade phase0@head` (the compose `migrate` service) does
not apply it. Apply manually once `embeddings` exceeds ~200k rows for the pinned model:

    alembic upgrade hnsw@head            # CREATE INDEX CONCURRENTLY, runs outside a transaction
    alembic downgrade hnsw@base          # drops it again

Per-model partial expression index (embeddings.vec is untyped `vector`; the cast selects
the 384-d model rows). Session tuning after apply: `SET hnsw.ef_search = 100;`.
"""
from __future__ import annotations

from alembic import op

revision = "0002_hnsw"
down_revision = None
branch_labels = ("hnsw",)
depends_on = None

CREATE = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS embeddings_hnsw ON embeddings
  USING hnsw ((vec::vector(384)) vector_cosine_ops) WITH (m = 16, ef_construction = 128)
  WHERE model = 'intfloat/multilingual-e5-small' AND dims = 384;
"""


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql(CREATE)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql("DROP INDEX CONCURRENTLY IF EXISTS embeddings_hnsw;")
