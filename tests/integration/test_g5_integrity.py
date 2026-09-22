"""G5 — `project_ids` integrity rules of the write service (PHASE0-SPEC §1, §7).

Nonexistent slug → E_FORBIDDEN_PROJECT (no enumeration); `null` element, duplicate and missing
home → E_INVALID_ARG; in every case nothing is written.
"""

from __future__ import annotations

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import default_deps, write
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world, write_req

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        return await seed_world(conn)


@pytest.mark.parametrize(
    ("project_ids", "code", "reason"),
    [
        ([MAIN, "g5-does-not-exist"], "E_FORBIDDEN_PROJECT", None),
        ([MAIN, None], "E_INVALID_ARG", "null"),
        ([MAIN, OTHER, OTHER], "E_INVALID_ARG", "duplicate_project"),
        ([OTHER], "E_INVALID_ARG", "missing_home"),
        ([], "E_INVALID_ARG", None),
    ],
    ids=["nonexistent", "null", "duplicate", "missing_home", "empty"],
)
async def test_project_ids_integrity(connect, world, deps, project_ids, code, reason) -> None:
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await write(
                conn, world.ctx_a, write_req(MAIN, [item("X", "y", project_ids=project_ids)]), deps=deps
            )
        await conn.rollback()
        err = ei.value
        assert err.code == code, err
        if reason == "null":
            assert "null" in err.message
        elif reason is not None:
            assert err.details.get("reason") == reason
        for table in ("events", "memory_versions", "chunks", "links", "jobs"):
            assert await count(conn, table) == 0, table


async def test_project_ids_home_first_and_all_grants(connect, world, deps) -> None:
    """A valid cross-project item stores project_ids home-first; chunks/links copy it verbatim."""
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("Cross", "geteilt", project_ids=[OTHER, MAIN]),
                    item(
                        "Link",
                        "zeigt",
                        project_ids=[OTHER, MAIN],
                        links=[{"rel": "relates_to", "target": "$0"}],
                    ),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        cur = await conn.execute("SELECT project_id, project_ids FROM memory_versions ORDER BY version_id")
        for pid, pids in await cur.fetchall():
            assert pid == world.main_id and pids == [world.main_id, world.other_id]
        cur = await conn.execute("SELECT project_ids FROM chunks UNION ALL SELECT project_ids FROM links")
        assert {tuple(r[0]) for r in await cur.fetchall()} == {(world.main_id, world.other_id)}
        # dev-b holds write on OTHER only: listing MAIN → E_FORBIDDEN_PROJECT, nothing written
        with pytest.raises(ToolError) as ei:
            await write(
                conn, world.ctx_b, write_req(OTHER, [item("Nope", "x", project_ids=[OTHER, MAIN])]), deps=deps
            )
        await conn.rollback()
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
        assert await count(conn, "events") == 1 and len(res.versions) == 2
