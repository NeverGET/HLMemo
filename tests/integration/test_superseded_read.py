"""D-207 defect #5 (B3 read side): supersession status on reads, computed from LIVE ``supersedes`` links.

* ``memory.raw`` carries an INCOMING ``superseded_by`` list (``[{logical_id, version_id, scope,
  valid_from, valid_to, quote?}]``): who supersedes the addressed version, whole or part;
* ``memory.query`` hits carry ``superseded: true`` plus ``superseded_by: [{clue, scope}]`` when a
  live link targets their item (part-scope links are marked ``part``); a hit without one renders
  byte-identically to before (no key at all);
* the superseder must be visible to the caller (authz (a)): a hidden one is never named, and a link
  whose superseder is hidden does not flag the hit;
* a reverted link (transaction time ended) no longer flags anything; an as-of read before the
  revert still sees it.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.auth.context import Role
from hlmemo.core.read_service import query, raw
from hlmemo.librarian.reversal import revert_write_update
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._write_fixtures import MAIN, World, item
from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by import
    D_EFF,
    D_OLD,
    NEW_TTL,
    OLD,
    REPL,
    SPAN,
    _current,
    _device,
    _links,
    _old,
    _replay_identical,
    _rows,
    _upd,
    _write,
    _write_event_of,
    deps,
    world,
)

pytestmark = pytest.mark.integration

Q = "API cache TTL seconds endpoint"


async def _raw(connect, world: World, read_deps, version_id: int, **kw: Any) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": version_id, "token_budget": 4000, **kw},
            deps=read_deps,
        )
        await conn.commit()
    return out


#: the query never returns the superseders (written as lessons): with the superseder among the hits,
#: D-057 would hide a whole-superseded item outright; the flag is for the case it is NOT among them
KINDS = ["fact", "episode", "session_note"]


async def _hits(connect, ctx, read_deps, query_text: str = Q, **kw: Any) -> list[dict[str, Any]]:  # noqa: ANN001
    async with await connect() as conn:
        out = await query(
            conn,
            ctx,
            {"project": MAIN, "query": query_text, "token_budget": 4000, "kinds": KINDS, **kw},
            deps=read_deps,
        )
        await conn.commit()
    return list(out["hits"])


def _by_vid(hits: list[dict[str, Any]], vid: int) -> dict[str, Any]:
    (h,) = [h for h in hits if h["clue"].split(".")[0] == f"v{vid}"][:1] or [None]
    assert h is not None, (vid, [x["clue"] for x in hits])
    return h


# --------------------------------------------------------------------------- memory.raw
async def test_raw_names_the_whole_superseder_of_a_closed_item(connect, world, deps, read_deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache replaced",
                "The cache was removed; responses are not cached.",
                valid_from=D_EFF.isoformat(),
                updates=[_upd(old, old_span="The cache backend is Redis 7", mode="supersede")],
            )
        ],
        deps,
    )
    (new,) = ack["versions"]
    out = await _raw(connect, world, read_deps, old["version_id"])
    assert out["superseded_by"] == [
        {
            "logical_id": new["logical_id"],
            "version_id": new["version_id"],
            "scope": "whole",
            "valid_from": out["superseded_by"][0]["valid_from"],
            "valid_to": None,
        }
    ]
    assert datetime.fromisoformat(out["superseded_by"][0]["valid_from"].replace("Z", "+00:00")) == D_EFF
    assert out["links"] == []  # the OUTGOING view is unchanged: the old item links nowhere
    # the close's survivor [D_OLD, D_EFF) is the same item: the supersession starts where it ends
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT version_id FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'",
            (old["logical_id"],),
        )
        (survivor,) = [r[0] for r in await cur.fetchall()]
        await conn.rollback()
    assert [e["logical_id"] for e in (await _raw(connect, world, read_deps, survivor))["superseded_by"]] == [
        new["logical_id"]
    ]
    # an item nobody supersedes: an explicit empty list (the field is always present on raw)
    assert (await _raw(connect, world, read_deps, new["version_id"]))["superseded_by"] == []


async def test_a_supersede_quoting_the_outdated_title_closes_and_is_named_on_raw(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A writer whose target's TITLE is what went stale quotes the title ("Cache settings" occurs in
    no body line): the supersede applies, closes the old item at the cut, its link is whole-scope
    with ``quote_in: "title"``, and memory.raw of the old version names the superseder (no quote: a
    whole entry). A revise quoting the title is told to supersede instead."""
    title = OLD[0]
    assert title not in OLD[1]
    old = await _old(connect, world, deps)
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(old, title, replacement=REPL)])], deps
    )
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", "span_in_title")
    assert "supersede" in u["hint"]
    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache replaced",
                "The cache was removed; responses are not cached.",
                valid_from=D_EFF.isoformat(),
                updates=[_upd(old, title, mode="supersede")],
            )
        ],
        deps,
    )
    (new,) = ack["versions"]
    assert ack["updates"] == [{"index": 0, "update": 0, "status": "applied", "mode": "supersede"}]
    (survivor,) = _current(await _rows(connect, old["logical_id"]))
    assert (survivor["body"], survivor["vf"], survivor["vt"]) == (OLD[1], D_OLD, D_EFF)  # closed at the cut
    ((src, dst, dst_v, rel, _vf, props),) = await _links(connect)
    assert (src, dst, dst_v, rel) == (new["logical_id"], old["logical_id"], old["version_id"], "supersedes")
    assert props == {
        "by": "writer",
        "mode": "supersede",
        "scope": "whole",
        "quote": title,
        "quote_in": "title",
    }
    out = await _raw(connect, world, read_deps, old["version_id"])
    assert out["superseded_by"] == [
        {
            "logical_id": new["logical_id"],
            "version_id": new["version_id"],
            "scope": "whole",
            "valid_from": out["superseded_by"][0]["valid_from"],
            "valid_to": None,
        }
    ]
    assert datetime.fromisoformat(out["superseded_by"][0]["valid_from"].replace("Z", "+00:00")) == D_EFF
    await _replay_identical(connect)


async def test_raw_marks_part_scope_and_a_revision_names_the_revised_version(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    ep = await _old(connect, world, deps, kind="episode", body="Session log: " + OLD[1])
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(ep, replacement=REPL)])], deps
    )
    assert ack["updates"][0]["status"] == "linked"  # a historical kind: link only, scope part
    (entry,) = (await _raw(connect, world, read_deps, ep["version_id"]))["superseded_by"]
    assert (entry["logical_id"], entry["scope"], entry["quote"]) == (
        ack["versions"][0]["logical_id"],
        "part",
        SPAN,
    )
    # a span revision: the pre-revision version is revised in part BY the item's own new version
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, valid_from=D_EFF.isoformat(), updates=[_upd(old, replacement=REPL)])],
        deps,
    )
    head = int(ack["updates"][0]["clue"][1:])
    (self_entry,) = (await _raw(connect, world, read_deps, old["version_id"]))["superseded_by"]
    assert (self_entry["logical_id"], self_entry["version_id"], self_entry["scope"]) == (
        old["logical_id"],
        head,
        "part",
    )
    assert (await _raw(connect, world, read_deps, head))["superseded_by"] == []  # the new head is current


# --------------------------------------------------------------------------- memory.query
async def test_query_hits_carry_superseded_including_part_scope(connect, world, deps, read_deps) -> None:  # noqa: ANN001
    whole = await _old(connect, world, deps, kind="episode", body="Session log: " + OLD[1])
    part = await _old(
        connect,
        world,
        deps,
        kind="session_note",
        body="Notes: the API cache TTL is 60 seconds for every endpoint today.\nOther notes follow.",
    )
    plain = await _old(connect, world, deps, body="The API cache TTL review happens every endpoint audit.")
    before = await _hits(connect, world.ctx_a, read_deps)
    for v in (whole, part, plain):
        assert "superseded" not in _by_vid(before, v["version_id"])  # no link: the hit is unchanged
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache gone",
                "The cache was removed from the api host.",
                kind="lesson",
                updates=[_upd(whole, old_span="Redis 7", mode="supersede")],
            ),
            item(*NEW_TTL, kind="lesson", updates=[_upd(part, replacement=REPL)]),
        ],
        deps,
    )
    assert [u["status"] for u in ack["updates"]] == ["linked", "linked"]
    # with the superseder among the hits, D-057 keeps hiding a whole-superseded item outright
    assert whole["version_id"] not in {
        int(h["clue"][1:].split(".")[0]) for h in await _hits(connect, world.ctx_a, read_deps, kinds=None)
    }
    w_new, p_new = (f"v{v['version_id']}" for v in ack["versions"])
    hits = await _hits(connect, world.ctx_a, read_deps)
    hw, hp, hn = (_by_vid(hits, v["version_id"]) for v in (whole, part, plain))
    assert (hw["superseded"], hw["superseded_by"]) == (True, [{"clue": w_new, "scope": "whole"}])
    assert (hp["superseded"], hp["superseded_by"]) == (True, [{"clue": p_new, "scope": "part"}])
    assert "superseded" not in hn and "superseded_by" not in hn
    # bi-temporal: as of before the write nothing was superseded
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT recorded_at FROM memory_versions WHERE version_id = %s", (plain["version_id"],)
        )
        (t0,) = await cur.fetchone()
        await conn.rollback()
    old_view = await _hits(connect, world.ctx_a, read_deps, known_at=t0.isoformat())
    assert "superseded" not in _by_vid(old_view, whole["version_id"])


async def test_a_hidden_superseder_is_never_named_and_a_revert_clears_the_flag(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    ep = await _old(connect, world, deps, kind="episode", body="Session log: " + OLD[1])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Private correction",
                "The cache was removed from the api host.",
                kind="lesson",
                device_scope=f"device:{world.dev_a}",
                updates=[_upd(ep, old_span="Redis 7", mode="supersede")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"
    # dev-a sees its own device-scoped superseder
    assert _by_vid(await _hits(connect, world.ctx_a, read_deps), ep["version_id"])["superseded"] is True
    # another device with read on MAIN cannot see the superseder: no flag, nothing named
    ctx_c = await _device(connect, "dev-c", {world.main_id: Role.READ})
    hit = _by_vid(await _hits(connect, ctx_c, read_deps), ep["version_id"])
    assert "superseded" not in hit and "superseded_by" not in hit
    async with await connect() as conn:
        out = await raw(
            conn,
            ctx_c,
            {"project": MAIN, "version_id": ep["version_id"], "token_budget": 4000},
            deps=read_deps,
        )
        await conn.commit()
    assert out["superseded_by"] == []
    # the revert ends the link: the flag is gone for the owner too
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    async with await connect() as conn:
        await revert_write_update(conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="x")
        await conn.commit()
    assert "superseded" not in _by_vid(await _hits(connect, world.ctx_a, read_deps), ep["version_id"])
    assert (await _raw(connect, world, read_deps, ep["version_id"]))["superseded_by"] == []


async def test_raw_superseded_by_respects_the_valid_time_of_the_version(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """A link that ended before the addressed version began is not about it: a pointer superseded
    the item for [Jan, Mar) only; a backdated revision from April has nothing superseding it."""
    old = await _old(connect, world, deps)
    d_mar, d_apr = datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC)
    await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Pointer",
                "The cache notes moved to the ops runbook.",
                valid_from=D_OLD.isoformat(),
                valid_to=d_mar.isoformat(),
                # PV-2: a raw supersedes link needs an import's source
                source={"system": "markdown", "path": "docs/pointer.md", "sha256": "0" * 64},
                links=[{"rel": "supersedes", "target": old["logical_id"]}],
            )
        ],
        deps,
    )
    (entry,) = (await _raw(connect, world, read_deps, old["version_id"]))["superseded_by"]
    assert entry["scope"] == "whole" and entry["valid_to"] is not None  # the link holds [Jan, Mar)
    (rev,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item(OLD[0], OLD[1] + "\nReviewed in April.", valid_from=d_apr.isoformat()),
                    "logical_id": old["logical_id"],
                    "expected_version_id": old["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    assert (await _raw(connect, world, read_deps, rev["version_id"]))["superseded_by"] == []
    (survivor,) = [r for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] == d_apr]
    assert len((await _raw(connect, world, read_deps, survivor["vid"]))["superseded_by"]) == 1


# --------------------------------------------------------------------------- the brief uses it
async def test_the_brief_excludes_an_item_whose_superseder_is_outside_its_pool(
    connect, world, deps, read_deps
) -> None:  # noqa: ANN001
    """The AL5 brief (``brief/fetch.py``) reads only session notes and lessons. A FACT supersedes a
    session note: the old pool fallback cannot see it (no pool item links to the note); the new
    server fields (hit ``superseded`` / raw ``superseded_by``) exclude the note."""
    from hlmemo.brief import fetch as F

    note = await _old(
        connect,
        world,
        deps,
        kind="session_note",
        body="Session notes\n\n## Decisions\n- The API cache TTL is 60 seconds for every endpoint.",
        valid_from=None,
    )
    keep = await _old(
        connect, world, deps, kind="lesson", body="Lesson: always measure the cache first.", valid_from=None
    )
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache decision reversed",
                "The cache was removed; the TTL decision no longer applies.",
                updates=[_upd(note, old_span="The API cache TTL is 60 seconds", mode="supersede")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "linked"  # a session note keeps its text: link only

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = query if tool == "memory.query" else raw
        async with await connect() as conn:
            out = await fn(conn, world.ctx_a, args, deps=read_deps)
            await conn.commit()
        return out

    snap = await F.gather_snapshot(call, MAIN)
    assert (f"v{note['version_id']}", "superseded") in snap.excluded
    assert snap.sessions == []
    assert [i.version_id for i in snap.lessons] == [keep["version_id"]]
