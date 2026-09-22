"""D-037/F16: version-local raw edges and lossless budgeted edge continuation pages."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, drilldown, raw
from hlmemo.core.write_service import default_deps, write
from tests.integration._write_fixtures import MAIN, OTHER, item, seed_world, write_req

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def rdeps():
    return ReadDeps(meter=Meter(), model_dir=Path("."), cursor_secret=b"d037-read-link-tests")


@pytest.fixture
async def linked_world(connect):
    """A legal large edge projection independent of the write path's 32-links/batch cap."""
    async with await connect() as conn:
        world = await seed_world(conn)
        deps = default_deps()
        ack = await write(
            conn,
            world.ctx_a,
            write_req(MAIN, [item(f"target {i}", "target body") for i in range(49)]),
            deps=deps,
        )
        targets = ack.versions
        source = (
            await write(
                conn,
                world.ctx_a,
                write_req(MAIN, [item("source", "source body", valid_from="2026-01-01T00:00:00Z")]),
                deps=deps,
            )
        ).versions[0]
        await conn.execute(
            """
            INSERT INTO links (project_id, project_ids, src_logical_id, dst_logical_id,
                               dst_version_id, rel, valid_from, recorded_at, source_event_id)
            SELECT s.project_id, s.project_ids, s.logical_id, t.logical_id, t.version_id,
                   'derived_from', s.valid_from, s.recorded_at, s.source_event_id
            FROM memory_versions s CROSS JOIN memory_versions t
            WHERE s.version_id = %s AND t.version_id = ANY(%s)
            """,
            (source.version_id, [t.version_id for t in targets]),
        )
        await conn.commit()
        return world, source, targets


@pytest.mark.parametrize("tool", [raw, drilldown])
async def test_large_link_lists_page_without_loss(connect, linked_world, rdeps, tool):
    world, source, targets = linked_world
    base = {"project": MAIN, "token_budget": 1000 if tool is raw else 700}
    base.update({"version_id": source.version_id} if tool is raw else {"clue_ids": [f"v{source.version_id}"]})
    links = []
    texts = []
    cursors = set()
    cursor = None
    link_only = False
    async with await connect() as conn:
        for _ in range(100):
            result = await tool(conn, world.ctx_a, {**base, "cursor": cursor}, deps=rdeps)
            assert rdeps.meter.count(result) == result["budget"]["used"] <= base["token_budget"]
            if tool is raw:
                links.extend(ln["dst_version_id"] for ln in result["links"])
                texts.extend(c["text"] for c in result["chunks"])
                link_only |= bool(result["links"]) and not result["chunks"]
            else:
                links.extend(int(ln["clue"][1:]) for it in result["items"] for ln in it["links"])
                texts.extend(it["text"] for it in result["items"] if it["text"])
                link_only |= any(it["links"] and not it["text"] for it in result["items"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
            assert cursor not in cursors
            if not cursors:
                # A newly committed edge must not shift or extend an existing cursor snapshot.
                await conn.execute(
                    """INSERT INTO links (project_id, project_ids, src_logical_id, dst_logical_id,
                                          dst_version_id, rel, valid_from, recorded_at, source_event_id)
                       SELECT project_id, project_ids, logical_id, %s, %s, 'relates_to',
                              valid_from, clock_timestamp(), source_event_id
                       FROM memory_versions WHERE version_id=%s""",
                    (targets[0].logical_id, targets[0].version_id, source.version_id),
                )
                await conn.commit()
            cursors.add(cursor)
        else:
            pytest.fail("cursor did not terminate")
    assert cursors and link_only
    assert texts == ["source body"]
    assert links == [t.version_id for t in targets]


async def test_raw_edges_overlap_addressed_version_on_both_axes(connect, linked_world, rdeps):
    world, source, targets = linked_world
    async with await connect() as conn:
        # Make one edge valid only before the source and another known only before it.
        await conn.execute(
            "UPDATE links SET valid_from='2025-01-01', valid_to='2025-12-31' WHERE dst_version_id=%s",
            (targets[0].version_id,),
        )
        await conn.execute(
            """UPDATE links SET recorded_at='2025-01-01', superseded_at='2025-12-31'
               WHERE dst_version_id=%s""",
            (targets[1].version_id,),
        )
        # Superseding a pinned destination must not remove its authorized evidence.
        target = targets[2]
        await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "new target",
                        "revised",
                        logical_id=target.logical_id,
                        expected_version_id=target.version_id,
                    )
                ],
            ),
            deps=default_deps(),
        )
        await conn.commit()
        result = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": source.version_id, "token_budget": 32000},
            deps=rdeps,
        )
        assert [ln["dst_version_id"] for ln in result["links"]] == [t.version_id for t in targets[2:]]


async def test_raw_historical_version_retains_its_edges(connect, linked_world, rdeps):
    world, source, targets = linked_world
    async with await connect() as conn:
        revised = (
            await write(
                conn,
                world.ctx_a,
                write_req(
                    MAIN,
                    [
                        item(
                            "source",
                            "revised",
                            logical_id=source.logical_id,
                            expected_version_id=source.version_id,
                        )
                    ],
                ),
                deps=default_deps(),
            )
        ).versions[0]
        # Close every original edge precisely when the source version was superseded.
        await conn.execute(
            """UPDATE links SET superseded_at=(SELECT recorded_at FROM memory_versions WHERE version_id=%s)
               WHERE src_logical_id=%s""",
            (revised.version_id, source.logical_id),
        )
        await conn.commit()
        for vid, expected in [(source.version_id, len(targets)), (revised.version_id, 0)]:
            result = await raw(
                conn, world.ctx_a, {"project": MAIN, "version_id": vid, "token_budget": 32000}, deps=rdeps
            )
            assert len(result["links"]) == expected


@pytest.mark.parametrize("tool", [raw, drilldown])
async def test_link_cursor_bound_to_read_parameters(connect, linked_world, rdeps, tool):
    world, source, _ = linked_world
    base = {"project": MAIN, "token_budget": 1000 if tool is raw else 700}
    base.update({"version_id": source.version_id} if tool is raw else {"clue_ids": [f"v{source.version_id}"]})
    async with await connect() as conn:
        result = await tool(conn, world.ctx_a, base, deps=rdeps)
        assert result["next_cursor"]
        changes = [{"project": OTHER}]
        if tool is drilldown:
            changes += [
                {"include_archived": True},
                {"valid_at": datetime(2026, 9, 1, tzinfo=UTC).isoformat()},
            ]
        for change in changes:
            with pytest.raises(ToolError) as exc:
                await tool(conn, world.ctx_a, {**base, **change, "cursor": result["next_cursor"]}, deps=rdeps)
            # Authz may fail first on a foreign project, without disclosing the source.
            assert exc.value.code in {"E_INVALID_CURSOR", "E_NOT_FOUND"}
