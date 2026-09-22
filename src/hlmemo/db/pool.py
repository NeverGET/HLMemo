"""psycopg3 AsyncConnectionPool built from `Settings` (PHASE0-SPEC §6 `db/pool.py`)."""

from __future__ import annotations

from functools import partial

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from hlmemo.config import Settings, get_settings


async def configure_connection(conn: AsyncConnection, *, settings: Settings | None = None) -> None:
    """Per-connection adapters, UTC and bounded database work/lock lifetimes."""
    from pgvector.psycopg import register_vector_async

    await register_vector_async(conn)
    await conn.execute("SET TIME ZONE 'UTC'")
    settings = settings or get_settings()
    for name, value in (
        ("lock_timeout", settings.db_lock_timeout_ms),
        ("statement_timeout", settings.db_statement_timeout_ms),
        ("idle_in_transaction_session_timeout", settings.db_idle_in_transaction_timeout_ms),
    ):
        await conn.execute("SELECT set_config(%s, %s, false)", (name, f"{value}ms"))
    # the pool requires configure() to hand back an IDLE connection; with autocommit=False the
    # statements above opened a transaction (SET TIME ZONE is session-scoped, so commit keeps it)
    await conn.commit()


def create_pool(settings: Settings | None = None, *, open: bool = False) -> AsyncConnectionPool:
    """Create (but by default do not open) the application pool.

    Usage: `pool = create_pool(); await pool.open(); ... ; await pool.close()`
    or `async with create_pool() as pool: ...`.
    """
    settings = settings or get_settings()
    return AsyncConnectionPool(
        conninfo=settings.db_dsn,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        configure=partial(configure_connection, settings=settings),
        timeout=settings.pool_timeout_s,
        kwargs={"autocommit": False},
        open=open,
        name="hlmemo",
    )


def sqlalchemy_url(dsn: str) -> str:
    """libpq DSN -> SQLAlchemy URL using the psycopg3 driver (used only by Alembic)."""
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn[len(prefix) :]
    if dsn.startswith("postgresql+psycopg://"):
        return dsn
    # key=value libpq form: let psycopg parse it via the query-less URL form
    return f"postgresql+psycopg:///?{dsn.replace(' ', '&')}"
