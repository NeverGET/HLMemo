"""Review 96 (dual, B3 NO-GO) regressions for the B3 port of the D-118 write-time updates and its
read side. Each HIGH has a reproducer that failed on 5b0f7da. The multi-device cases use REAL grants:
device A (``world.ctx_a``, the writer) and devices B and C, each with its own ``device:<id>`` scope.

* Astra #1 (HIGH): a pinned ``supersedes`` link names its superseder and quote only to a reader of
  the PINNED version, and only on that version or a body-identical copy of it (never on a later
  revision of the item), on ``memory.raw`` and ``memory.query`` alike.
* Sol #1 (HIGH): ``memory.raw`` ``payload_item.updates`` keeps only the entries whose target the
  reader may see; a hidden or unresolvable entry is dropped whole.
* Sol #2 (HIGH): supersede and link-only updates pass the shared old_span rules (exactly once, on
  word boundaries) before mode or kind matter; a historical revise also passes the part rule and the
  replacement rules.
* Sol #3 (HIGH): a closing supersede needs a carrier that is visible wherever the target is.
* Astra #2 (HIGH): an update stays revertible after any number of revise/revert cycles of later
  updates (no fixed bound on the restore chain); live == replay.
* Sol #4 (MEDIUM): ``superseded_by`` needs the link and the version to overlap in valid time; a
  pinned link's own cut survivor is the one exception.
* Sol #5 (MEDIUM): a revised or restored lesson enters the brief with its body (rebuilt from its
  chunks), never with an empty one.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import query, raw
from hlmemo.librarian.reversal import revert_write_update
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._write_fixtures import MAIN, OTHER, World, item
from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by import
    D_EFF,
    D_OLD,
    NEW_TTL,
    OLD,
    REPL,
    _current,
    _device,
    _links,
    _old,
    _replay_identical,
    _rows,
    _shared_target,
    _upd,
    _write,
    _write_event_of,
    deps,
    world,
)

pytestmark = pytest.mark.integration

D_FEB = datetime(2026, 2, 1, tzinfo=UTC)
D_MAR = datetime(2026, 3, 1, tzinfo=UTC)
D_APR = datetime(2026, 4, 1, tzinfo=UTC)
D_MAY = datetime(2026, 5, 1, tzinfo=UTC)
SECRET = "The vault passphrase is tangerine falcon"
CLEAN = "Session log: the vault passphrase was rotated. The deploy went out on Tuesday afternoon."
KINDS = ["fact", "episode", "session_note", "lesson"]


async def _raw(connect, ctx: AuthContext, read_deps, version_id: int) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await raw(
            conn, ctx, {"project": MAIN, "version_id": version_id, "token_budget": 8000}, deps=read_deps
        )
        await conn.commit()
    return out


async def _hits(connect, ctx: AuthContext, read_deps, text: str) -> list[dict[str, Any]]:  # noqa: ANN001
    async with await connect() as conn:
        out = await query(
            conn, ctx, {"project": MAIN, "query": text, "token_budget": 6000, "kinds": KINDS}, deps=read_deps
        )
        await conn.commit()
    return list(out["hits"])


def _hit(hits: list[dict[str, Any]], vid: int) -> dict[str, Any]:
    found = [h for h in hits if h["clue"].split(".")[0] == f"v{vid}"]
    assert found, (vid, [h["clue"] for h in hits])
    return found[0]


async def _devices(connect, world: World) -> tuple[AuthContext, AuthContext]:  # noqa: ANN001
    """B (write on MAIN) and C (read on MAIN and OTHER): two more devices, each with its own scope."""
    ctx_b = await _device(connect, "dev-b96", {world.main_id: Role.WRITE})
    ctx_c = await _device(connect, "dev-c96", {world.main_id: Role.READ, world.other_id: Role.READ})
    assert len({world.ctx_a.device_id, ctx_b.device_id, ctx_c.device_id}) == 3
    return ctx_b, ctx_c


async def _revert(connect, ctx: AuthContext, event_id: int, reason: str = "x") -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await revert_write_update(conn, event_id=event_id, index=0, update=0, by=ctx, reason=reason)
        await conn.commit()
    return out


# --------------------------------------------------------------------------- Astra #1 (HIGH)
async def test_r96_astra1_a_private_quote_never_reaches_a_reader_of_a_later_public_revision(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A writes a device:A episode holding SECRET, then a PUBLIC carrier whose revise update quotes it
    (a historical kind: a public part-scope link pinned to the private version), then revises the
    episode into a public, cleaned version. B and C cannot read the private version: neither
    ``memory.raw`` nor ``memory.query`` of the clean version may carry SECRET or flag it; the link
    is bound to the version it pins, so it does not flag the clean version for A either."""
    ctx_b, ctx_c = await _devices(connect, world)
    ep = await _old(
        connect,
        world,
        deps,
        kind="episode",
        body=f"Session log: {SECRET}. The deploy went out on Tuesday afternoon.",
        device_scope=f"device:{world.dev_a}",
    )
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Vault rotated",
                "The vault passphrase was rotated last week.",
                device_scope="all",
                updates=[_upd(ep, old_span=SECRET, replacement="The vault passphrase was rotated")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"
    (entry,) = (await _raw(connect, world.ctx_a, read_deps, ep["version_id"]))["superseded_by"]
    assert entry["quote"] == SECRET  # the owner of the private version still sees its own quote
    (clean,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item(
                        "Session log",
                        CLEAN,
                        kind="episode",
                        device_scope="all",
                    ),
                    "logical_id": ep["logical_id"],
                    "expected_version_id": ep["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    for ctx in (ctx_b, ctx_c):
        out = await _raw(connect, ctx, read_deps, clean["version_id"])
        assert SECRET not in json.dumps(out), "the private quote leaked through superseded_by"
        assert out["superseded_by"] == []
        hit = _hit(
            await _hits(connect, ctx, read_deps, "vault passphrase rotated deploy"), clean["version_id"]
        )
        assert "superseded" not in hit and "superseded_by" not in hit
    # the link pins the private version: the clean revision is not what it superseded
    assert (await _raw(connect, world.ctx_a, read_deps, clean["version_id"]))["superseded_by"] == []
    # B cannot address the private version at all
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await raw(
                conn,
                ctx_b,
                {"project": MAIN, "version_id": ep["version_id"], "token_budget": 4000},
                deps=read_deps,
            )
        await conn.rollback()
    assert ei.value.code == "E_NOT_FOUND"


async def test_r96_astra1_a_pinned_link_binds_to_its_version_on_every_read_channel(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """The same binding on the older link readers: a WHOLE-scope link-only update (a historical
    supersede) pinned to A's private episode must neither hide the later public revision from B's
    query (D-057: the carrier is among the hits) nor hand its quote to memory.ask's status labels
    (D-184, ``lq.supersessions_of``) for that revision."""
    from hlmemo.db import librarian_queries as lq

    ctx_b, _ctx_c = await _devices(connect, world)
    ep = await _old(
        connect,
        world,
        deps,
        kind="episode",
        body=f"Session log: {SECRET}. The deploy went out on Tuesday afternoon.",
        device_scope=f"device:{world.dev_a}",
    )
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Vault rotated",
                "The vault passphrase was rotated last week.",
                device_scope="all",
                updates=[_upd(ep, old_span=SECRET, mode="supersede")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"
    (carrier,) = ack["versions"]
    (clean,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item("Session log", CLEAN, kind="episode", device_scope="all"),
                    "logical_id": ep["logical_id"],
                    "expected_version_id": ep["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    hits = await _hits(connect, ctx_b, read_deps, "vault passphrase rotated deploy")
    _hit(hits, carrier["version_id"])  # the superseder is among the hits ...
    assert "superseded" not in _hit(hits, clean["version_id"])  # ... and the revision is not hidden

    async def statuses(ctx: AuthContext, vid: int) -> list[tuple[int, int, bool, str]]:
        async with await connect() as conn:
            now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
            out = await lq.supersessions_of(
                conn,
                [ep["logical_id"]],
                pid=world.main_id,
                scopes=list(ctx.scope_values()),
                valid_at=now,
                known_at=now,
                version_of={ep["logical_id"]: vid},
            )
            await conn.rollback()
        return out

    assert await statuses(ctx_b, clean["version_id"]) == []
    assert await statuses(world.ctx_a, clean["version_id"]) == []  # not the version it pinned
    assert await statuses(world.ctx_a, ep["version_id"]) == [
        (carrier["logical_id"], ep["logical_id"], False, SECRET)
    ]


# --------------------------------------------------------------------------- Sol #1 (HIGH)
async def test_r96_sol1_raw_payload_item_drops_update_entries_whose_target_is_hidden(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A public carrier updates a device:A memory (clue form AND the integer form) and a public one;
    B and C read the carrier: only the public target's entry is shown; A sees its own; an entry
    whose target does not exist is dropped for everyone."""
    ctx_b, ctx_c = await _devices(connect, world)
    hidden = await _old(
        connect,
        world,
        deps,
        body="The PRIVATE TOKEN rotates every week on the vault host.",
        device_scope=f"device:{world.dev_a}",
    )
    shared = await _old(connect, world, deps)
    clue_entry = {"item": f"v{hidden['version_id']}", "old_span": "PRIVATE TOKEN", "mode": "supersede"}
    int_entry = {
        "item": hidden["logical_id"],
        "expected_version": hidden["version_id"],
        "old_span": "PRIVATE TOKEN",
        "mode": "supersede",
    }
    shared_entry = _upd(shared, replacement=REPL)
    ghost = {"item": "v999999999", "old_span": "anything at all", "mode": "supersede"}
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Corrections",
                "The token rotation moved. The API cache TTL is now 300 seconds for every endpoint.",
                device_scope="all",
                updates=[clue_entry, int_entry, shared_entry, ghost],
            )
        ],
        deps,
    )
    (carrier,) = ack["versions"]
    for ctx in (ctx_b, ctx_c):
        out = await _raw(connect, ctx, read_deps, carrier["version_id"])
        assert out["payload_item"]["updates"] == [shared_entry]
        assert "PRIVATE TOKEN" not in json.dumps(out)
        assert f"v{hidden['version_id']}" not in json.dumps(out["payload_item"])
    own = await _raw(connect, world.ctx_a, read_deps, carrier["version_id"])
    assert own["payload_item"]["updates"] == [clue_entry, int_entry, shared_entry]


# --------------------------------------------------------------------------- Sol #2 (HIGH)
AMBIG = "The cache is Redis. The cache is monitored by the ops team every day."


async def test_r96_sol2_supersede_and_link_only_refuse_an_ambiguous_old_span(connect, world, deps) -> None:  # noqa: ANN001
    fact = await _old(connect, world, deps, body=AMBIG)
    ep = await _old(connect, world, deps, kind="episode", body="Session log: " + AMBIG)
    before = await _rows(connect, fact["logical_id"])
    links_before = await _links(connect)

    async def one(target: dict, **kw: Any) -> dict[str, Any]:
        ack = await _write(
            connect,
            world.ctx_a,
            MAIN,
            [item("Cache gone", "The cache was removed from the api host.", updates=[_upd(target, **kw)])],
            deps,
        )
        return ack["updates"][0]

    # the D-118 rule: old_span occurs exactly once (supersede = a close; the old code closed it)
    u = await one(fact, old_span="cache", mode="supersede")
    assert (u["status"], u.get("code"), u["reason"]) == ("rejected", "E_INVALID_ARG", "span_not_unique")
    assert await _rows(connect, fact["logical_id"]) == before  # nothing closed
    # the same for a link-only (historical) target, both modes
    for mode in ("supersede", "revise"):
        u = await one(ep, old_span="cache", mode=mode, replacement="removed")
        assert (u["status"], u["reason"]) == ("rejected", "span_not_unique"), mode
    # word boundaries
    u = await one(fact, old_span="Redi", mode="supersede")
    assert (u["status"], u["reason"]) == ("rejected", "span_word_boundary")
    u = await one(ep, old_span="Redi", mode="supersede")
    assert (u["status"], u["reason"]) == ("rejected", "span_word_boundary")
    # a historical revise (a part-scope link) passes the part rule and the replacement rules too
    short = await _old(connect, world, deps, kind="episode", body="Session log: Deploys go out on Tuesdays.")
    u = await one(short, old_span="Deploys go out on Tuesdays", mode="revise", replacement="removed")
    assert (u["status"], u["reason"]) == ("rejected", "span_whole")
    u = await one(ep, old_span="The cache is Redis", mode="revise", replacement="Memcached")
    assert (u["status"], u["reason"]) == ("rejected", "replacement_not_in_body")
    assert await _links(connect) == links_before  # no link was added by any refused update
    # control: an unambiguous quote applies (fact: closed) and links (episode)
    u = await one(fact, old_span="The cache is Redis", mode="supersede")
    assert u["status"] == "applied"
    u = await one(ep, old_span="The cache is Redis", mode="supersede")
    assert (u["status"], u["reason"]) == ("linked", "historical_kind")
    await _replay_identical(connect)


# --------------------------------------------------------------------------- Sol #3 (HIGH)
async def test_r96_sol3_a_narrow_carrier_never_closes_a_broader_target(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A device_scope=all target and an A-only carrier: refused (C would lose the target without
    seeing why); a MAIN+OTHER target and a MAIN-only carrier: refused; a carrier as wide as the
    target closes it."""
    _ctx_b, ctx_c = await _devices(connect, world)
    target = await _old(connect, world, deps)
    before = await _rows(connect, target["logical_id"])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache replaced",
                "The cache was removed; responses are not cached.",
                device_scope=f"device:{world.dev_a}",
                updates=[_upd(target, old_span="The cache backend is Redis 7", mode="supersede")],
            )
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", "replacement_visibility")
    assert len(ack["versions"]) == 1  # the carrier itself is written
    assert await _rows(connect, target["logical_id"]) == before
    seen = await _raw(connect, ctx_c, read_deps, target["version_id"])
    assert (seen["valid_to"], seen["superseded_at"]) == (None, None)  # C still reads it as current
    # MAIN+OTHER target, MAIN-only carrier
    wide = await _shared_target(connect, world, deps)
    wide_before = await _rows(connect, wide["logical_id"])
    gone = item(
        "Cache replaced",
        "The cache was removed; responses are not cached.",
        updates=[_upd(wide, old_span="The cache backend is Redis 7", mode="supersede")],
    )
    ack = await _write(connect, world.ctx_a, MAIN, [gone], deps)
    assert (ack["updates"][0]["status"], ack["updates"][0]["reason"]) == (
        "rejected",
        "replacement_visibility",
    )
    assert await _rows(connect, wide["logical_id"]) == wide_before
    # control: a carrier visible wherever the target is closes it
    ack = await _write(connect, world.ctx_a, MAIN, [{**gone, "project_ids": [MAIN, OTHER]}], deps)
    assert ack["updates"][0]["status"] == "applied"
    assert all(r["vt"] is not None for r in _current(await _rows(connect, wide["logical_id"])))
    await _replay_identical(connect)


# --------------------------------------------------------------------------- Astra #2 (HIGH)
async def test_r96_astra2_a_revise_stays_revertible_after_sixteen_revise_revert_cycles(
    connect, world, deps
) -> None:  # noqa: ANN001
    """Revise E1; then 16 times: revise the current version and revert that revise. E1's result is
    current again (as a chain of 16 restored copies), so E1 is revertible; live == replay."""
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, valid_from=D_EFF.isoformat(), updates=[_upd(old, replacement=REPL)])],
        deps,
    )
    e1 = await _write_event_of(connect, ack["versions"][0]["version_id"])
    head = int(ack["updates"][0]["clue"][1:])
    revised = [r["body"] for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] is None]
    for k in range(16):
        n = f"{400 + k} seconds"
        cyc = await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                item(
                    "TTL again",
                    f"The API cache TTL is now {n}.",
                    updates=[{"item": f"v{head}", "old_span": REPL, "mode": "revise", "replacement": n}],
                )
            ],
            deps,
        )
        assert cyc["updates"][0]["status"] == "applied", (k, cyc["updates"])
        res = await _revert(
            connect, world.ctx_a, await _write_event_of(connect, cyc["versions"][0]["version_id"])
        )
        (restored,) = res["restored"]
        head = int(restored[1:])
        assert [
            r["body"] for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] is None
        ] == revised
    res = await _revert(connect, world.ctx_a, e1, "undo E1")  # E_VERSION_CONFLICT before the fix
    assert len(res["restored"]) == 1 and res["links_superseded"] == 1  # the live copy of E1's self-link
    (cur,) = _current(await _rows(connect, old["logical_id"]))
    assert (cur["body"], cur["vf"], cur["vt"]) == (OLD[1], D_OLD, None)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- Sol #4 (MEDIUM)
async def test_r96_sol4_superseded_by_needs_the_link_and_the_version_to_overlap(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """An ordinary supersedes link from April: a [Jan,Feb) version (disjoint) and a [Jan,Apr) one
    (equal boundary) are not superseded by it; a [Jan,May) one is. A PINNED link: the version's
    copy that ends before the link starts is not; its own cut survivor (ends where the link starts)
    stays the one exception."""
    vs = []
    for end in (D_FEB, D_APR, D_MAY):
        (v,) = (
            await _write(
                connect,
                world.ctx_a,
                MAIN,
                [item(OLD[0], OLD[1], valid_from=D_OLD.isoformat(), valid_to=end.isoformat())],
                deps,
            )
        )["versions"]
        vs.append(v)
    (ptr,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                item(
                    "Pointer",
                    "The cache notes moved to the ops runbook.",
                    valid_from=D_APR.isoformat(),
                    links=[{"rel": "supersedes", "target": v["logical_id"]} for v in vs],
                )
            ],
            deps,
        )
    )["versions"]
    disjoint, boundary, overlap = [
        (await _raw(connect, world.ctx_a, read_deps, v["version_id"]))["superseded_by"] for v in vs
    ]
    assert disjoint == [] and boundary == []
    assert [e["logical_id"] for e in overlap] == [ptr["logical_id"]]
    # pinned: an episode linked (pinned) from June; a backdated write revision from March leaves a
    # body-identical copy [Jan,Mar): disjoint from the link, so not superseded by it
    ep = await _old(connect, world, deps, kind="episode", body="Session log: " + OLD[1])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache gone",
                "The cache was removed.",
                valid_from=D_EFF.isoformat(),
                updates=[_upd(ep, old_span="Redis 7", mode="supersede")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"
    await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            {
                **item(
                    OLD[0], "Session log: rewritten in March.", kind="episode", valid_from=D_MAR.isoformat()
                ),
                "logical_id": ep["logical_id"],
                "expected_version_id": ep["version_id"],
            }
        ],
        deps,
    )
    (copy,) = [r for r in _current(await _rows(connect, ep["logical_id"])) if r["vt"] == D_MAR]
    assert copy["sup"] == ep["version_id"]
    assert (await _raw(connect, world.ctx_a, read_deps, copy["vid"]))["superseded_by"] == []
    assert len((await _raw(connect, world.ctx_a, read_deps, ep["version_id"]))["superseded_by"]) == 1
    # the cut survivor of a pinned close ends exactly where its link starts: still superseded
    fact = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache gone",
                "The cache was removed.",
                valid_from=D_EFF.isoformat(),
                updates=[_upd(fact, old_span="Redis 7", mode="supersede")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "applied"
    (survivor,) = _current(await _rows(connect, fact["logical_id"]))
    assert survivor["vt"] == D_EFF
    assert len((await _raw(connect, world.ctx_a, read_deps, survivor["vid"]))["superseded_by"]) == 1


# --------------------------------------------------------------------------- Sol #5 (MEDIUM)
LESSON = (
    "Lesson: the API cache TTL is 60 seconds for every endpoint.\n"
    "Rule: measure the cache before tuning it; the mistake was tuning blind."
)


async def test_r96_sol5_a_revised_then_reverted_lesson_enters_the_brief_with_its_body(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    from hlmemo.brief import fetch as F

    lesson = await _old(connect, world, deps, kind="lesson", body=LESSON, valid_from=None)
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(lesson, replacement=REPL)])], deps
    )
    assert ack["updates"][0]["status"] == "applied"
    head = int(ack["updates"][0]["clue"][1:])

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = query if tool == "memory.query" else raw
        async with await connect() as conn:
            out = await fn(conn, world.ctx_a, args, deps=read_deps)
            await conn.commit()
        return out

    assert (await call("memory.raw", {"project": MAIN, "version_id": head, "token_budget": 6000}))[
        "payload_item"
    ] == {}  # a mutation's version: no request item of its own
    snap = await F.gather_snapshot(call, MAIN)
    (got,) = [i for i in snap.lessons if i.version_id == head]
    assert got.body == LESSON.replace("60 seconds", REPL)
    # the revert restores a copy of the original: its body too
    res = await _revert(
        connect, world.ctx_a, await _write_event_of(connect, ack["versions"][0]["version_id"])
    )
    (restored,) = res["restored"]
    snap = await F.gather_snapshot(call, MAIN)
    (got,) = [i for i in snap.lessons if i.handle == restored]
    assert got.body == LESSON
    assert all(i.body for i in snap.lessons)
