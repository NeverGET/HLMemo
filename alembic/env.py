"""Alembic environment: psycopg3 engine from HLM_DB_DSN (or `-x dsn=...`); SQL-only revisions.

Revision graph (see alembic/versions/*):
  branch `phase0`/`main`: 0001_phase0 -> 0003_title_lexical -> 0004_title_norm_fold (D-055)
                   -> 0005_w0_access (label `main`, D-061) -> 0006_librarian (W2a, D-062)
                   -> 0007_import (W1.5, D-069)
                   -> applied by `alembic upgrade main@head`
                   (`main@head` also upgrades a database at 0001, 0004 or 0005;
                   `phase0@head` is the same head)
  branch `hnsw`:   0002_hnsw (own base)   -> NEVER applied automatically; `alembic upgrade hnsw@head`
Because two heads exist, a bare `alembic upgrade head` is refused by Alembic on purpose.
"""

from __future__ import annotations

import logging.config

from alembic import context
from sqlalchemy import create_engine, pool

from hlmemo.config import get_settings
from hlmemo.db.pool import sqlalchemy_url

config = context.config
if config.config_file_name is not None:
    logging.config.fileConfig(config.config_file_name)

target_metadata = None  # no autogenerate: DDL is hand-written SQL (PHASE0-SPEC §1)


def _dsn() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return x.get("dsn") or get_settings().db_dsn


def run_migrations_offline() -> None:
    context.configure(url=sqlalchemy_url(_dsn()), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(_dsn()), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, transaction_per_migration=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
