"""Shared bits for the R1 judge repros."""
from datetime import UTC, datetime, timedelta

import pytest

from hlmemo.core.read_service import default_read_deps
from hlmemo.core.write_service import default_deps
from tests.integration._write_fixtures import World, seed_world

D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture(scope="session")
def rdeps():
    return default_read_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        return await seed_world(conn)
