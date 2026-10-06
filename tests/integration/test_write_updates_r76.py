"""D-118 review 76 (dual, FIX-NEEDED) regressions for the write-time updates; each one failed on
8b54800 and checks live AND replay (``_replay_identical``).

* #1 (HIGH): the cut is chosen against the TARGETED head only; an earlier valid-time segment —
  history the quoted head never represented, possibly a historical kind — is never cut.
* #2 (HIGH): an item that also declares the same ``supersedes`` link: a per-update conflict (the
  update never owns an untagged link, so every applied update is fully revertible).
* #3 (MEDIUM): the target is the version valid at the write clock, not ``max(version_id)``.
* #4 (MEDIUM): an existing edge that only overlaps the update's link interval: a conflict.
* #5 (MEDIUM): a link-only revert's T follows the link it ends, even under a clock regression.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.db import write_queries as q
from hlmemo.librarian.reversal import revert_write_update
from tests.integration._write_fixtures import MAIN, World, item
from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by import
    D_EFF,
    D_LATE,
    D_OLD,
    NEW_TTL,
    OLD,
    REPL,
    SPAN,
    _current,
    _links,
    _old,
    _replay_identical,
    _rows,
    _server_now,
    _upd,
    _write,
    _write_event_of,
    deps,
    world,
)

pytestmark = pytest.mark.integration

D_JAN15 = datetime(2026, 1, 15, tzinfo=UTC)
D_FEB = datetime(2026, 2, 1, tzinfo=UTC)
D_MAR = datetime(2026, 3, 1, tzinfo=UTC)
D_APR = datetime(2026, 4, 1, tzinfo=UTC)
B2 = (
    "The API cache TTL is 120 seconds for every endpoint.\n"
    "The cache backend is Redis 7.2 on the api host.\n"
    "Cache keys carry the service name."
)


async def _revise_item(connect, world: World, deps, v: dict, body: str, **kw: Any) -> dict:  # noqa: ANN001
    """A write-path revision of ``v``'s item (backdated corrections make valid-time segments)."""
    kind = kw.pop("kind", "fact")
    (out,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item(OLD[0], body, **kw),
                    "kind": kind,
                    "logical_id": v["logical_id"],
                    "expected_version_id": v["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    return out


async def _gone(connect, world: World, deps, target: dict, valid_from: datetime) -> dict:  # noqa: ANN001
    return await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache gone",
                "The cache was removed.",
                valid_from=valid_from.isoformat(),
                updates=[_upd(target, old_span="Redis 7.2", mode="supersede")],
            )
        ],
        deps,
    )


# --------------------------------------------------------------------------- #1 cut vs targeted head
async def test_r76_1_a_backdated_supersede_cuts_only_the_quoted_head(connect, world, deps) -> None:  # noqa: ANN001
    """Segments [Jan,Jun) + [Jun,∞); a carrier dated March supersedes the [Jun,∞) head. March lies
    before that head, so the cut is the write's clock; [Jan,Jun) stays exactly as it was."""
    old = await _old(connect, world, deps)
    head = await _revise_item(connect, world, deps, old, B2, valid_from=D_EFF.isoformat())
    (early,) = [r for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] == D_EFF]
    before = await _server_now(connect)
    ack = await _gone(connect, world, deps, head, D_MAR)
    assert ack["updates"][0]["status"] == "applied"
    cur = _current(await _rows(connect, old["logical_id"]))
    assert early in cur  # the earlier segment: the same row, the same validity
    (closed,) = [r for r in cur if r["vid"] != early["vid"]]
    assert closed["body"] == B2 and closed["vf"] == D_EFF and closed["vt"] >= before
    await _replay_identical(connect)


async def test_r76_1_a_historical_segment_is_never_cut_by_a_supersede_of_a_later_head(
    connect, world, deps
) -> None:  # noqa: ANN001
    """An episode segment [Jan,Feb) and a fact head [Feb,∞) of one item; a carrier dated Jan 15
    supersedes the fact: the episode segment is never touched."""
    ep = await _old(connect, world, deps, kind="episode", body="Session log: " + OLD[1])
    head = await _revise_item(connect, world, deps, ep, B2, valid_from=D_FEB.isoformat())
    (episode,) = [r for r in _current(await _rows(connect, ep["logical_id"])) if r["vt"] == D_FEB]
    ack = await _gone(connect, world, deps, head, D_JAN15)
    assert ack["updates"][0]["status"] == "applied"
    cur = _current(await _rows(connect, ep["logical_id"]))
    assert episode in cur and episode["body"].startswith("Session log: ")
    assert [r["vf"] for r in cur] == [D_OLD, D_FEB]
    await _replay_identical(connect)


# --------------------------------------------------------------------------- #3 the target is valid now
async def test_r76_3_the_version_valid_now_is_the_target_not_a_later_middle_correction(
    connect, world, deps
) -> None:  # noqa: ANN001
    """A finite middle correction [Mar,Apr) has the greatest version id; the open survivor [Apr,∞)
    is what a query shows now, and its clue is the valid target."""
    old = await _old(connect, world, deps)
    corr = await _revise_item(
        connect, world, deps, old, B2, valid_from=D_MAR.isoformat(), valid_to=D_APR.isoformat()
    )
    (open_seg,) = [r for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] is None]
    assert open_seg["vid"] < corr["version_id"]
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, updates=[_upd({"version_id": open_seg["vid"]}, replacement=REPL)])],
        deps,
    )
    (u,) = ack["updates"]
    assert u["status"] == "applied"
    cur = _current(await _rows(connect, old["logical_id"]))
    (new_head,) = [r for r in cur if r["vt"] is None]
    assert new_head["body"] == OLD[1].replace(SPAN, REPL) and u["clue"] == f"v{new_head['vid']}"
    assert any(r["vid"] == corr["version_id"] for r in cur)  # the middle correction is untouched
    ack2 = await _write(  # the correction's clue is not valid now: a conflict naming the valid head
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, updates=[_upd(corr, old_span="120 seconds", replacement=REPL)])],
        deps,
    )
    assert (ack2["updates"][0]["reason"], ack2["updates"][0]["current_clue"]) == (
        "version_conflict",
        f"v{new_head['vid']}",
    )
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    async with await connect() as conn:  # and it is revertible
        await revert_write_update(conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="x")
        await conn.commit()
    after = _current(await _rows(connect, old["logical_id"]))
    assert sorted((r["body"], r["vf"], r["vt"]) for r in after) == sorted(
        [(OLD[1], D_OLD, D_MAR), (B2, D_MAR, D_APR), (OLD[1], D_APR, None)]
    )
    await _replay_identical(connect)


# --------------------------------------------------------------------------- #2 duplicate own link
@pytest.mark.parametrize(("kind", "mode"), [("fact", "supersede"), ("episode", "revise")])
async def test_r76_2_a_carrier_declaring_the_same_supersedes_link_is_a_per_update_conflict(
    connect, world, deps, kind: str, mode: str
) -> None:  # noqa: ANN001
    """The item's own ``supersedes`` link to the target plus an update of the same target: the
    update is rejected (it never relies on a link it does not own); the memory and its own link
    are written; there is nothing to revert, so nothing can be half-reverted."""
    body = ("Session: " if kind == "episode" else "") + OLD[1]
    target = await _old(connect, world, deps, kind=kind, body=body)
    before = await _rows(connect, target["logical_id"])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                *NEW_TTL,
                source={"system": "markdown", "path": "docs/new-ttl.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                links=[{"rel": "supersedes", "target": target["logical_id"]}],
                updates=[_upd(target, replacement=REPL, mode=mode)],
            )
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["reason"], u["code"]) == ("rejected", "duplicate_link", "E_INVALID_ARG")
    assert await _rows(connect, target["logical_id"]) == before
    ((src, dst, *_rest),) = await _links(connect)  # the item's own link, owned by the write
    assert (src, dst) == (ack["versions"][0]["logical_id"], target["logical_id"])
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await revert_write_update(conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="x")
        await conn.rollback()
    assert ei.value.code == "E_VERSION_CONFLICT"
    await _replay_identical(connect)


# --------------------------------------------------------------------------- #4 overlapping edge
async def test_r76_4_an_existing_edge_that_only_overlaps_a_backdated_update_is_a_conflict(
    connect, world, deps
) -> None:  # noqa: ANN001
    """The revised carrier already has a live ``supersedes`` edge [Jul,∞) to the target; its update
    backdated to March would close the target in March with no link for [Mar,Jul): rejected on its
    own; the target stays open; the revision is written."""
    target = await _old(connect, world, deps)
    (carrier,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                item(
                    "Cache plan",
                    "The cache will be removed.",
                    valid_from=D_LATE.isoformat(),
                    source={"system": "markdown", "path": "docs/cache-plan.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                    links=[{"rel": "supersedes", "target": target["logical_id"]}],
                )
            ],
            deps,
        )
    )["versions"]
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            {
                **item("Cache plan", "The cache was removed.", valid_from=D_MAR.isoformat()),
                "logical_id": carrier["logical_id"],
                "expected_version_id": carrier["version_id"],
                "updates": [_upd(target, old_span="Redis 7", mode="supersede")],
            }
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["reason"]) == ("rejected", "duplicate_link")
    assert len(ack["versions"]) == 1
    (tgt,) = _current(await _rows(connect, target["logical_id"]))
    assert tgt["vt"] is None and tgt["vid"] == target["version_id"]
    await _replay_identical(connect)


# --------------------------------------------------------------------------- #5 clock regression
async def test_r76_5_a_link_only_revert_survives_a_clock_regression(
    connect, world, deps, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """The revert's T follows the link it ends even when the database clock reads earlier than the
    write that recorded the link (``recorded_at < superseded_at`` holds)."""
    ep = await _old(connect, world, deps, kind="episode", body="Session: " + OLD[1])
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(ep, replacement=REPL)])], deps
    )
    assert ack["updates"][0]["status"] == "linked"
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    real = q.clock_now

    async def behind(conn):  # noqa: ANN001, ANN202
        return (await real(conn)) - timedelta(hours=1)

    monkeypatch.setattr(q, "clock_now", behind)
    async with await connect() as conn:
        out = await revert_write_update(
            conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="x"
        )
        await conn.commit()
    monkeypatch.setattr(q, "clock_now", real)
    assert out["links_superseded"] == 1
    assert await _links(connect) == []
    await _replay_identical(connect)
