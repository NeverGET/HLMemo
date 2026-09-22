"""psycopg3 AsyncConnectionPool built from `Settings` (PHASE0-SPEC §6 `db/pool.py`)."""

from __future__ import annotations

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from hlmemo.config import Settings, get_settings


async def configure_connection(conn: AsyncConnection) -> None:
    """Per-connection setup: pgvector adapters + UTC session time zone."""
    from pgvector.psycopg import register_vector_async

    await register_vector_async(conn)
    await conn.execute("SET TIME ZONE 'UTC'")


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
        configure=configure_connection,
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
