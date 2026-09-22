"""D-039/N5: card updates load affected edges without losing historical provenance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hlmemo.core.read_service import default_read_deps, raw
from hlmemo.core.write_service import call_the_day, default_deps, write
from hlmemo.db import write_queries as q
from hlmemo.db.replay import rebuild_projections
from tests.integration._write_fixtures import MAIN, dump_projections, item, seed_world, write_req

pytestmark = pytest.mark.integration
D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture(scope="session")
def deps():
    return default_deps()


async def test_forward_card_revisions_only_load_latest_source_set(connect, deps, monkeypatch):
    async with await connect() as conn:
        world = await seed_world(conn)
        loaded = []
        original = q.current_links_from

        async def observe(c, logical_id, **kwargs):
            links = await original(c, logical_id, **kwargs)
            if logical_id == world.main_card_lid:
                loaded.append(len(links))
            return links

        monkeypatch.setattr(q, "current_links_from", observe)
        head = None
        first = None
        for i in range(30):
            result = await call_the_day(
                conn,
                world.ctx_a,
                {
                    "project": MAIN,
                    "request_id": str(uuid.uuid4()),
                    "session_id": str(uuid.uuid4()),
                    "client": "pytest/0",
                    "notes": f"session {i}",
                    "card_update": {"body": f"card {i}", "expected_version_id": head},
                },
                deps=deps,
            )
            first = first or result
            head = result.versions[-1].version_id
            await conn.commit()
        assert loaded == [0] + [1] * 29
        count = await (
            await conn.execute(
                "SELECT count(*) FROM links WHERE src_logical_id=%s AND superseded_at='infinity'",
                (world.main_card_lid,),
            )
        ).fetchone()
        assert count == (30,), "historical provenance stays retained; only the hot-path load is bounded"
        history = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": first.versions[-1].version_id, "token_budget": 32000},
            deps=default_read_deps(),
        )
        assert {link["dst_version_id"] for link in history["links"]} == {first.versions[0].version_id}
        await conn.commit()
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_backdated_card_correction_keeps_both_outside_link_survivors(connect, deps):
    async with await connect() as conn:
        world = await seed_world(conn)
        sources = (
            await write(
                conn,
                world.ctx_a,
                write_req(MAIN, [item(f"source {i}", "body") for i in range(3)]),
                deps=deps,
            )
        ).versions
        head = None

        async def revise(day, source, end=None):
            nonlocal head
            ack = await write(
                conn,
                world.ctx_a,
                write_req(
                    MAIN,
                    [
                        item(
                            "card",
                            f"card at day {day}",
                            kind="project_card",
                            expected_version_id=head,
                            valid_from=(D0 + day * DAY).isoformat(),
                            valid_to=(D0 + end * DAY).isoformat() if end is not None else None,
                            links=[
                                {
                                    "rel": "derived_from",
                                    "target": sources[source].logical_id,
                                    "target_version_id": sources[source].version_id,
                                }
                            ],
                        )
                    ],
                ),
                deps=deps,
            )
            head = ack.versions[0].version_id
            await conn.commit()

        await revise(0, 0)
        await revise(2, 1)
        await revise(4, 0)
        affected = await q.current_links_from(
            conn, world.main_card_lid, valid_from=D0 + DAY, valid_to=D0 + 3 * DAY
        )
        assert {link.dst_version_id for link in affected} == {
            sources[0].version_id,
            sources[1].version_id,
        }
        assert len(affected) == 2
        await revise(1, 2, end=3)
        for day, source in [(0.5, 0), (1.5, 2), (2.5, 2), (3.5, 1), (4.5, 0)]:
            at = D0 + day * DAY
            links = await q.current_links_from(
                conn, world.main_card_lid, valid_from=at, valid_to=at + timedelta(seconds=1)
            )
            assert [link.dst_version_id for link in links] == [sources[source].version_id]
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
