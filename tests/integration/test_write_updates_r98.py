"""Review 98 (round 2, GO-with-fixes) #1 (MEDIUM): an unrelated revision of an item must not drop a
valid pinned PART correction. A part link from another item (a historical record's link-only revise)
still speaks about a LATER version of its target that holds the quoted span verbatim
(``read_queries.pinned_part_carry``), on every read channel: ``memory.raw`` ``superseded_by``,
``memory.query`` hit flags, the D-057 ``partial`` list and the D-184 ``memory.ask`` labels.

The carry keeps the review 96 Astra #1 rule: only for a reader of the PINNED version. A whole link
and a span revision's self-link stay bound to their version (and its body-identical copies).
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext
from hlmemo.db import librarian_queries as lq
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._write_fixtures import MAIN, World, item
from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by import
    OLD,
    _old,
    _upd,
    _write,
    deps,
    world,
)
from tests.integration.test_write_updates_r96 import _devices, _hit, _hits, _raw

pytestmark = pytest.mark.integration

SPAN = "the cache TTL is 60 seconds"
V1 = "Session log: the cache TTL is 60 seconds. The cache owner is Alice."
V2 = "Session log: the cache TTL is 60 seconds. The cache owner is Bob."  # unrelated change
V3 = "Session log: the cache TTL is 75 seconds. The cache owner is Bob."  # the span is gone
CARRIER = ("Cache TTL raised", "Since June the cache TTL is now 90 seconds.")
REPL = "the cache TTL is now 90 seconds"
QUERY = "cache TTL 60 seconds owner"


async def _revise(connect, world: World, deps, prev: dict, body: str, ctx: AuthContext | None = None) -> dict:  # noqa: ANN001
    (v,) = (
        await _write(
            connect,
            ctx or world.ctx_a,
            MAIN,
            [
                {
                    **item("Session log", body, kind="episode", device_scope="all"),
                    "logical_id": prev["logical_id"],
                    "expected_version_id": prev["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    return v


async def _ask_links(connect, ctx: AuthContext, world: World, lid: int, vid: int) -> list[tuple]:  # noqa: ANN001
    """D-184 (``memory.ask`` status labels) for the version ``vid`` of the item ``lid``."""
    async with await connect() as conn:
        now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
        out = await lq.supersessions_of(
            conn,
            [lid],
            pid=world.main_id,
            scopes=list(ctx.scope_values()),
            valid_at=now,
            known_at=now,
            version_of={lid: vid},
        )
        await conn.rollback()
    return out


async def _among(connect, ctx: AuthContext, world: World, pairs: list[tuple[int, int]]) -> tuple:  # noqa: ANN001
    """D-057 (``memory.query`` hide/demote) over the hits ``(logical id, version id)``."""
    async with await connect() as conn:
        now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
        out = await lq.supersession_among(
            conn,
            [lid for lid, _ in pairs],
            pid=world.main_id,
            scopes=list(ctx.scope_values()),
            valid_at=now,
            known_at=now,
            version_of=dict(pairs),
        )
        await conn.rollback()
    return out


async def _part_link(connect, world: World, deps, ep: dict, **kw: Any) -> dict:  # noqa: ANN001
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*CARRIER, device_scope="all", updates=[_upd(ep, old_span=SPAN, replacement=REPL)], **kw)],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"  # a historical kind: a pinned part link only
    (carrier,) = ack["versions"]
    return carrier


async def test_r98_1_an_unrelated_revision_keeps_a_pinned_part_correction(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """V1 "TTL 60, owner Alice" gets a pinned part correction of "TTL 60"; V2 changes only the owner:
    V2 is still partly superseded on every channel. V3 drops the span: no longer."""
    ep = await _old(connect, world, deps, kind="episode", body=V1)
    carrier = await _part_link(connect, world, deps, ep)
    lid, cl = ep["logical_id"], carrier["logical_id"]
    v2 = await _revise(connect, world, deps, ep, V2)
    assert v2["version_id"] > ep["version_id"]

    (entry,) = (await _raw(connect, world.ctx_a, read_deps, v2["version_id"]))["superseded_by"]
    assert (entry["logical_id"], entry["scope"], entry["quote"]) == (cl, "part", SPAN)
    hit = _hit(await _hits(connect, world.ctx_a, read_deps, QUERY), v2["version_id"])
    assert (hit["superseded"], hit["superseded_by"]) == (
        True,
        [{"clue": f"v{carrier['version_id']}", "scope": "part"}],
    )
    assert await _ask_links(connect, world.ctx_a, world, lid, v2["version_id"]) == [(cl, lid, True, SPAN)]
    assert await _among(
        connect, world.ctx_a, world, [(lid, v2["version_id"]), (cl, carrier["version_id"])]
    ) == (
        set(),
        [(cl, lid, SPAN)],
    )

    v3 = await _revise(connect, world, deps, v2, V3)
    assert (await _raw(connect, world.ctx_a, read_deps, v3["version_id"]))["superseded_by"] == []
    hit = _hit(await _hits(connect, world.ctx_a, read_deps, "cache TTL 75 seconds owner"), v3["version_id"])
    assert "superseded" not in hit and "superseded_by" not in hit
    assert await _ask_links(connect, world.ctx_a, world, lid, v3["version_id"]) == []
    assert await _among(
        connect, world.ctx_a, world, [(lid, v3["version_id"]), (cl, carrier["version_id"])]
    ) == (
        set(),
        [],
    )


async def test_r98_1_the_carry_needs_a_reader_of_the_pinned_version(connect, world, deps, read_deps) -> None:  # noqa: ANN001
    """The pinned V1 is device:A only; A's public V2 keeps the quoted span (so the quote itself is no
    secret there). B and C cannot read V1: V2 is never flagged for them and no channel names the
    carrier's quote of V1 or anything else of V1; A still sees the carried correction."""
    secret = "The vault passphrase is tangerine falcon."
    ep = await _old(
        connect, world, deps, kind="episode", body=f"{V1} {secret}", device_scope=f"device:{world.dev_a}"
    )
    ctx_b, ctx_c = await _devices(connect, world)
    carrier = await _part_link(connect, world, deps, ep)
    v2 = await _revise(connect, world, deps, ep, V2)
    lid, cl = ep["logical_id"], carrier["logical_id"]
    for ctx in (ctx_b, ctx_c):
        out = await _raw(connect, ctx, read_deps, v2["version_id"])
        assert out["superseded_by"] == []
        assert "tangerine" not in json.dumps(out)
        hit = _hit(await _hits(connect, ctx, read_deps, QUERY), v2["version_id"])
        assert "superseded" not in hit and "superseded_by" not in hit
        assert await _ask_links(connect, ctx, world, lid, v2["version_id"]) == []
        assert await _among(connect, ctx, world, [(lid, v2["version_id"]), (cl, carrier["version_id"])]) == (
            set(),
            [],
        )
    (entry,) = (await _raw(connect, world.ctx_a, read_deps, v2["version_id"]))["superseded_by"]
    assert (entry["logical_id"], entry["scope"], entry["quote"]) == (cl, "part", SPAN)


async def test_r98_1_a_whole_link_and_a_self_link_stay_bound_to_their_version(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A WHOLE link (a historical supersede) pinned to V1 never carries to V2, even though V2 still
    holds its quote. A span revision whose replacement still contains the old span: the revised head
    is not superseded by its own self-link (which pins the pre-revision version)."""
    ep = await _old(connect, world, deps, kind="episode", body=V1)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*CARRIER, device_scope="all", updates=[_upd(ep, old_span=SPAN, mode="supersede")])],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"
    v2 = await _revise(connect, world, deps, ep, V2)
    assert (await _raw(connect, world.ctx_a, read_deps, v2["version_id"]))["superseded_by"] == []
    assert await _ask_links(connect, world.ctx_a, world, ep["logical_id"], v2["version_id"]) == []
    hit = _hit(await _hits(connect, world.ctx_a, read_deps, QUERY), v2["version_id"])
    assert "superseded" not in hit
    # the pinned V1 itself keeps its whole link
    assert len((await _raw(connect, world.ctx_a, read_deps, ep["version_id"]))["superseded_by"]) == 1

    fact = await _old(connect, world, deps)  # OLD: "The API cache TTL is 60 seconds for every endpoint. ..."
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache TTL clarified",
                "The API cache TTL is 60 seconds, then 300 seconds for every endpoint.",
                updates=[_upd(fact, old_span="60 seconds", replacement="60 seconds, then 300 seconds")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "applied"
    head = int(ack["updates"][0]["clue"][1:])
    async with await connect() as conn:
        body = (
            await (
                await conn.execute("SELECT body FROM memory_versions WHERE version_id = %s", (head,))
            ).fetchone()
        )[0]
        await conn.rollback()
    assert "60 seconds" in body and body != OLD[1]  # the revised head still holds the quote
    assert (await _raw(connect, world.ctx_a, read_deps, head))["superseded_by"] == []
    (self_entry,) = (await _raw(connect, world.ctx_a, read_deps, fact["version_id"]))["superseded_by"]
    assert (self_entry["logical_id"], self_entry["scope"]) == (fact["logical_id"], "part")
    hit = _hit(await _hits(connect, world.ctx_a, read_deps, "API cache TTL 60 seconds"), head)
    assert "superseded" not in hit
