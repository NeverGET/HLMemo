"""G6 durability — projection rebuild from ``events`` (PHASE0-SPEC §1.1 *Replay*).

Regressions for codex review C2 (links inserted before later items' versions broke the
``dst_version_id`` FK on forward / cyclic batch references) and C3 (a correction spanning two
adjacent link segments must supersede every overlapping link, on the live path and on replay).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from tests.integration._write_fixtures import (
    MAIN,
    World,
    count,
    dump_projections,
    event_payload,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration

D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        return await seed_world(conn)


async def _assert_rebuild_identical(conn, expected_links: int) -> None:
    before = await dump_projections(conn)
    assert len(before["links"]) == expected_links
    stats = await rebuild_projections(conn)
    await conn.commit()
    after = await dump_projections(conn)
    assert after == before
    assert stats.links == expected_links and stats.versions == len(before["memory_versions"])


async def test_rebuild_with_forward_link_reference(connect, world, deps) -> None:
    """items[0] pins ``derived_from "$1"`` — the target version is created *after* the source."""
    req = write_req(
        MAIN,
        [
            item(
                "Quelle",
                "leitet sich aus dem zweiten Item ab",
                links=[{"rel": "derived_from", "target": "$1"}, {"rel": "relates_to", "target": "$1"}],
            ),
            item("Ziel", "kommt erst danach"),
        ],
    )
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        src, dst = res.versions
        cur = await conn.execute(
            "SELECT rel, src_logical_id, dst_logical_id, dst_version_id FROM links ORDER BY link_id"
        )
        assert await cur.fetchall() == [
            ("derived_from", src.logical_id, dst.logical_id, dst.version_id),
            ("relates_to", src.logical_id, dst.logical_id, None),
        ]
        await _assert_rebuild_identical(conn, expected_links=2)
        assert await count(conn, "links", "dst_version_id = %s", (dst.version_id,)) == 1


async def test_rebuild_with_cyclic_batch_links(connect, world, deps) -> None:
    """items[0] → "$1" and items[1] → "$0", both version-pinned: no insertion order of
    (version, links) per item satisfies the FK — only versions-first does."""
    req = write_req(
        MAIN,
        [
            item("A", "A hängt von B ab", links=[{"rel": "derived_from", "target": "$1"}]),
            item("B", "B hängt von A ab", links=[{"rel": "derived_from", "target": "$0"}]),
        ],
    )
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        a, b = res.versions
        cur = await conn.execute(
            "SELECT src_logical_id, dst_logical_id, dst_version_id FROM links ORDER BY link_id"
        )
        assert await cur.fetchall() == [
            (a.logical_id, b.logical_id, b.version_id),
            (b.logical_id, a.logical_id, a.version_id),
        ]
        await _assert_rebuild_identical(conn, expected_links=2)


async def test_rebuild_preserves_verbatim_request_and_resolved_defaults(connect, world, deps) -> None:
    """C5: request provenance preserves absent/default/null distinctions and UUID spelling;
    rebuilding consumes the resolved defaults without rewriting that authoritative request."""
    req = write_req(MAIN, [item("Original", "Unveränderte Provenienz", valid_to=None)], occurred_at=None)
    req["request_id"] = "ABCDEFAB-1234-4234-9234-ABCDEFABCDEF"
    async with await connect() as conn:
        await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT payload, payload_sha256 FROM events WHERE request_id = %s", (req["request_id"],)
        )
        payload, sha = await cur.fetchone()
        assert payload["request"] == req
        assert set(payload) == {"request", "resolved"}
        canon = json.dumps(payload["request"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        assert sha == hashlib.sha256(canon.encode()).hexdigest()
        resolved = payload["resolved"]["write"]["items"][0]
        assert resolved["device_scope"] == "all" and resolved["tags"] == []
        assert resolved["pinned"] is False and resolved["stability"] == "volatile"
        assert "valid_to" in payload["request"]["items"][0]
        assert "device_scope" not in payload["request"]["items"][0]
        await _assert_rebuild_identical(conn, expected_links=0)
        assert (await event_payload(conn, req["request_id"])) == payload


async def test_rebuild_identical_after_spanning_correction(connect, world, deps) -> None:
    """Two adjacent current link segments [D0,D7) and [D7,∞); a correction of [D5,D10) that
    re-declares the edge supersedes both and inserts the replacement plus two survivors."""
    async with await connect() as conn:
        tgt = await write(conn, world.ctx_a, write_req(MAIN, [item("Ziel", "Zielobjekt")]), deps=deps)
        await conn.commit()
        t_lid = tgt.versions[0].logical_id
        link = [{"rel": "relates_to", "target": t_lid}]
        r1 = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Quelle",
                        "Segment eins",
                        valid_from=D0.isoformat(),
                        valid_to=(D0 + 7 * DAY).isoformat(),
                        links=link,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        lid, v1 = r1.versions[0].logical_id, r1.versions[0].version_id
        r2 = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Quelle",
                        "Segment zwei",
                        logical_id=lid,
                        expected_version_id=v1,
                        valid_from=(D0 + 7 * DAY).isoformat(),
                        links=link,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        assert await count(conn, "links", "src_logical_id = %s AND superseded_at = 'infinity'", (lid,)) == 2

        fix = write_req(
            MAIN,
            [
                item(
                    "Quelle",
                    "Korrektur über beide Segmente",
                    logical_id=lid,
                    expected_version_id=r2.versions[0].version_id,
                    valid_from=(D0 + 5 * DAY).isoformat(),
                    valid_to=(D0 + 10 * DAY).isoformat(),
                    links=link,
                )
            ],
        )
        r3 = await write(conn, world.ctx_a, fix, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT link_id, valid_from, nullif(valid_to, 'infinity'), superseded_at = 'infinity',"
            " supersedes_link_id FROM links WHERE src_logical_id = %s ORDER BY link_id",
            (lid,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 5
        (l1, _, _, cur1, _), (l2, _, _, cur2, _) = rows[:2]
        l3, vf3, vt3, cur3, sup3 = rows[-1]
        assert (cur1, cur2, cur3) == (False, False, True)
        assert (vf3, vt3) == (D0 + 5 * DAY, D0 + 10 * DAY) and sup3 in (l1, l2)
        assert [(r[1], r[2], r[3]) for r in rows[2:4]] == [
            (D0, D0 + 5 * DAY, True),
            (D0 + 10 * DAY, None, True),
        ]
        payload = await event_payload(conn, fix["request_id"])
        assert payload["resolved"]["superseded_links"] == sorted([l1, l2])
        assert payload["resolved"]["items"][0]["links"][0]["supersedes_link_id"] == sup3
        assert r3.versions[0].version_id > r2.versions[0].version_id

        await _assert_rebuild_identical(conn, expected_links=5)
