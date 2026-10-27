"""D-267: a clue that lives in another project the caller can read names that project in ``E_NOT_FOUND``.

A test drive (2026-10-24) got clues of another project from ``memory.risk_check`` (cross-project),
drilled them in its own project and got a bare ``E_NOT_FOUND``. Now drilldown and raw name the project,
but only when the caller holds READ there and the row passes that project's own checks (device scope,
time, status); every other miss stays the uniform ``E_NOT_FOUND`` of G5 with no details."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, drilldown, raw
from hlmemo.core.write_service import default_deps, write
from tests.integration._write_fixtures import MAIN, OTHER, World, item, seed_world, write_req

pytestmark = pytest.mark.integration


class _StubEmbedder:
    def embed_query(self, query: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


@pytest.fixture(scope="session")
def wdeps():
    return default_deps()


@pytest.fixture(scope="session")
def rdeps() -> ReadDeps:
    deps = ReadDeps(meter=Meter(), model_dir=Path("."), cursor_secret=b"d267-foreign-clue")
    deps._embedder = _StubEmbedder()  # type: ignore[assignment]
    return deps


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        return await seed_world(conn)


async def _one(conn, ctx, project: str, wdeps, **kw: Any) -> int:  # noqa: ANN001
    ack = await write(conn, ctx, write_req(project, [item("Foreign", "foreign body d267", **kw)]), deps=wdeps)
    await conn.commit()
    return ack.versions[0].version_id


async def _drill(conn, ctx, project: str, vid: int, rdeps: ReadDeps) -> ToolError:  # noqa: ANN001
    with pytest.raises(ToolError) as ei:
        await drilldown(
            conn, ctx, {"project": project, "clue_ids": [f"v{vid}"], "token_budget": 2000}, deps=rdeps
        )
    return ei.value


async def _raw(conn, ctx, project: str, vid: int, rdeps: ReadDeps) -> ToolError:  # noqa: ANN001
    with pytest.raises(ToolError) as ei:
        await raw(conn, ctx, {"project": project, "version_id": vid, "token_budget": 2000}, deps=rdeps)
    return ei.value


def _plain(e: ToolError, what: str) -> None:
    assert e.code == "E_NOT_FOUND" and e.message == f"{what} not found" and e.details == {}


async def test_a_clue_of_a_readable_project_names_it(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        vid = await _one(conn, world.ctx_a, OTHER, wdeps)
        for e, what in (
            (await _drill(conn, world.ctx_a, MAIN, vid, rdeps), "clue"),
            (await _raw(conn, world.ctx_a, MAIN, vid, rdeps), "version"),
        ):
            assert e.code == "E_NOT_FOUND" and e.details == {"project": OTHER}
            assert (
                e.message
                == f"{what} not found in this project; it is in project {OTHER}: pass project {OTHER}"
            )
        res = await drilldown(
            conn, world.ctx_a, {"project": OTHER, "clue_ids": [f"v{vid}"], "token_budget": 2000}, deps=rdeps
        )
        assert "foreign body d267" in str(res)


async def test_no_grant_there_means_the_plain_error(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        vid = await _one(conn, world.ctx_a, MAIN, wdeps)  # device B has no grant on MAIN
        _plain(await _drill(conn, world.ctx_b, OTHER, vid, rdeps), "clue")
        _plain(await _raw(conn, world.ctx_b, OTHER, vid, rdeps), "version")


async def test_a_row_the_device_scope_hides_there_stays_unnamed(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        vid = await _one(conn, world.ctx_a, OTHER, wdeps, device_scope="class:work")  # A is a personal device
        _plain(await _drill(conn, world.ctx_a, MAIN, vid, rdeps), "clue")
        _plain(await _raw(conn, world.ctx_a, MAIN, vid, rdeps), "version")


async def test_an_unknown_version_stays_plain(connect, world: World, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        _plain(await _drill(conn, world.ctx_a, MAIN, 10**9, rdeps), "clue")
        _plain(await _raw(conn, world.ctx_a, MAIN, 10**9, rdeps), "version")


async def test_a_superseded_row_there_is_not_named_by_drilldown(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    """Drilldown reads live rows only: a row no longer live in the other project is not named either."""
    async with await connect() as conn:
        vid = await _one(conn, world.ctx_a, OTHER, wdeps)
        cur = await conn.execute("SELECT logical_id FROM memory_versions WHERE version_id = %s", (vid,))
        (lid,) = await cur.fetchone()
        ack = await write(
            conn,
            world.ctx_a,
            write_req(OTHER, [item("Foreign", "revised body", logical_id=lid, expected_version_id=vid)]),
            deps=wdeps,
        )
        await conn.commit()
        assert ack.versions[0].version_id != vid
        _plain(await _drill(conn, world.ctx_a, MAIN, vid, rdeps), "clue")
        e = await _raw(conn, world.ctx_a, MAIN, vid, rdeps)  # raw shows history: the project is named
        assert e.details == {"project": OTHER}
