"""D-118 write-time supersession through the real write path (``memory.write`` ``items[].updates``).

* revise: the target gets a NEW version byte-identical outside the quoted span (B-real's
  ``version_revise``), its survivor keeps the old text before the cut, its pinned self-link names
  the writer and the carrying item; supersede: the target is closed and linked; live == replay.
* The new memory is ALWAYS written: a version conflict, a hidden target, a cross-project update
  without the capability or against the D-083 policy, a batch conflict, a future-dated carrier —
  each rejects only its own update, with a code, a reason and a hint.
* Historical records (episodes, session notes, decision rows) are link-only in both modes, whatever
  ``HLM_LIBRARIAN_REVISE_KINDS`` says.
* Locks (J/D-095): the item locks come first (a writer waiting on them sees the head and the
  policy committed meanwhile); two concurrent writes updating the same memory: one applies, the
  other gets ``E_VERSION_CONFLICT``.
* Reversal: ONE compensating ``librarian`` event (``revert_write_update``) per update.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import default_deps, write
from hlmemo.db import write_queries as q
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.reversal import revert_write_update
from tests.integration._write_fixtures import (
    MAIN,
    OTHER,
    World,
    count,
    dump_projections,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC)
D_EFF = datetime(2026, 6, 1, tzinfo=UTC)
D_LATE = datetime(2026, 7, 15, tzinfo=UTC)
OLD = (
    "Cache settings",
    "The API cache TTL is 60 seconds for every endpoint.\n"
    "The cache backend is Redis 7 on the api host.\n"
    "Cache keys are prefixed with the service name.",
)
NEW_TTL = ("Cache TTL raised", "The API cache TTL is now 300 seconds for every endpoint.")
SPAN, REPL = "60 seconds", "300 seconds"
EXCLUDE_SQL = (
    'UPDATE projects SET policy = policy || \'{"librarian_cross_project": "exclude"}\' WHERE project_id = %s'
)


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        return await seed_world(conn)


async def _write(connect, ctx: AuthContext, project: str, items: list[dict[str, Any]], deps, **kw) -> dict:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(conn, ctx, write_req(project, items, **kw), deps=deps)
        await conn.commit()
    return res.model_dump(mode="json")


async def _old(connect, world: World, deps, *, body: str = OLD[1], kind: str = "fact", **kw) -> dict:  # noqa: ANN001
    kw.setdefault("valid_from", D_OLD.isoformat())
    (v,) = (await _write(connect, world.ctx_a, MAIN, [{**item(OLD[0], body, **kw), "kind": kind}], deps))[
        "versions"
    ]
    return v


def _upd(target: dict, old_span: str = SPAN, mode: str = "revise", **kw: Any) -> dict[str, Any]:
    return {"item": f"v{target['version_id']}", "old_span": old_span, "mode": mode, **kw}


async def _rows(connect, logical_id: int) -> list[dict[str, Any]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            """
            SELECT version_id, body, valid_from, nullif(valid_to, 'infinity'),
                   superseded_at = 'infinity', supersedes_version_id, source_event_id
              FROM memory_versions WHERE logical_id = %s ORDER BY version_id
            """,
            (logical_id,),
        )
        keys = ("vid", "body", "vf", "vt", "current", "sup", "event")
        out = [dict(zip(keys, r, strict=True)) for r in await cur.fetchall()]
        await conn.rollback()
    return out


def _current(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["current"]]


async def _links(connect) -> list[tuple]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT src_logical_id, dst_logical_id, dst_version_id, rel, valid_from, props"
            " FROM links WHERE superseded_at = 'infinity' ORDER BY link_id"
        )
        rows = await cur.fetchall()
        await conn.rollback()
    return rows


async def _event(connect, event_id: int) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT payload FROM events WHERE event_id = %s", (event_id,))
        (payload,) = await cur.fetchone()
        await conn.rollback()
    return payload


async def _write_event_of(connect, version_id: int) -> int:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT source_event_id FROM memory_versions WHERE version_id = %s", (version_id,)
        )
        (ev,) = await cur.fetchone()
        await conn.rollback()
    return int(ev)


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_projections(conn)
        for table in before:
            diff = sorted(set(before[table]) ^ set(after[table]))
            assert not diff, (table, diff[:4])
        assert after == before


async def _server_now(connect) -> datetime:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT clock_timestamp()")
        (now,) = await cur.fetchone()
        await conn.rollback()
    return now


async def _device(connect, name: str, grants: dict[int, Role]) -> AuthContext:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,"
            " approved_by_device_id) VALUES (%s, 'personal', %s, %s, 'trusted', now(), 1)"
            " RETURNING device_id",
            (name, f"fp-{name}", f"h-{name}"),
        )
        (did,) = await cur.fetchone()
        for pid, role in grants.items():
            await conn.execute(
                "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
                " VALUES (%s, %s, %s, 1)",
                (did, pid, role.value),
            )
        await conn.commit()
    return AuthContext(
        device_id=did,
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants=grants,
        client="pytest/0",
    )


# --------------------------------------------------------------------------- revise
async def test_revise_applies_byte_exact_outside_the_span_live_equals_replay(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, valid_from=D_EFF.isoformat(), updates=[_upd(old, replacement=REPL)])],
        deps,
    )
    (new,) = ack["versions"]  # the new memory is written
    (u,) = ack["updates"]
    rows = await _rows(connect, old["logical_id"])
    orig, survivor, head = rows
    assert u == {"index": 0, "update": 0, "status": "applied", "mode": "revise", "clue": f"v{head['vid']}"}
    assert not orig["current"] and survivor["current"] and head["current"]
    # the old text stays valid before the carrying item's valid_from; the revised text from it on
    assert (survivor["body"], survivor["vf"], survivor["vt"]) == (OLD[1], D_OLD, D_EFF)
    assert (head["vf"], head["vt"], head["sup"], survivor["sup"]) == (
        D_EFF,
        None,
        old["version_id"],
        old["version_id"],
    )
    start, end = OLD[1].index(SPAN), OLD[1].index(SPAN) + len(SPAN)
    assert head["body"] == OLD[1][:start] + REPL + OLD[1][end:]
    assert head["body"].encode().startswith(OLD[1][:start].encode())
    assert head["body"].encode().endswith(OLD[1][end:].encode())
    event_id = await _write_event_of(connect, new["version_id"])
    assert head["event"] == survivor["event"] == event_id  # ONE write event carries everything
    ((src, dst, dst_v, rel, vf, props),) = await _links(connect)
    assert (src, dst, dst_v, rel, vf) == (
        old["logical_id"],
        old["logical_id"],
        old["version_id"],
        "supersedes",
        D_EFF,
    )
    assert props["by"] == "writer" and props["relation"] == "revises" and props["scope"] == "part"
    assert (props["quote"], props["replacement"]) == (SPAN, REPL)
    assert (props["from_logical_id"], props["from_version_id"]) == (new["logical_id"], new["version_id"])
    payload = await _event(connect, event_id)
    (entry,) = payload["resolved"]["updates"]
    (mut,) = entry["mutations"]
    assert entry["status"] == "applied" and entry["action"] == "revise"
    assert (mut["op"], mut["write_event_id"], mut["update"]) == ("version_revise", event_id, [0, 0])
    assert mut["body_sha256"] == hashlib.sha256(head["body"].encode()).hexdigest()
    assert mut["cut_rule"] == "effective_date"
    async with await connect() as conn:  # re-embedding through the normal outbox
        cur = await conn.execute(
            "SELECT count(*) FROM jobs WHERE kind = 'embed' AND source_event_id = %s"
            " AND (payload->>'version_id')::bigint = ANY(%s)",
            (event_id, [survivor["vid"], head["vid"]]),
        )
        assert await cur.fetchone() == (2,)
    live = await _rows(connect, old["logical_id"])
    await _replay_identical(connect)
    assert await _rows(connect, old["logical_id"]) == live


async def test_revise_cut_is_now_when_the_carrier_starts_before_the_target(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps, valid_from=D_LATE.isoformat())
    before = await _server_now(connect)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, valid_from=D_EFF.isoformat(), updates=[_upd(old, replacement=REPL)])],
        deps,
    )
    assert ack["updates"][0]["status"] == "applied"
    _orig, survivor, head = await _rows(connect, old["logical_id"])
    assert survivor["vt"] == head["vf"] and head["vf"] >= before  # the write's own clock
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    (mut,) = (await _event(connect, event_id))["resolved"]["updates"][0]["mutations"]
    assert mut["cut_rule"] == "approval"


async def test_an_omitted_replacement_uses_a_one_statement_body_only(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect, world.ctx_a, MAIN, [item("Backend", " Redis 8 now\n", updates=[_upd(old, "Redis 7")])], deps
    )
    assert ack["updates"][0]["status"] == "applied"
    head = _current(await _rows(connect, old["logical_id"]))[-1]
    assert head["body"] == OLD[1].replace("Redis 7", "Redis 8 now")  # the trimmed one-statement body
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item("TTL", "It is 300 seconds. Keys stay prefixed.", updates=[_upd({"version_id": head["vid"]})])],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", "replacement_required")


async def test_the_replacement_must_be_in_the_carrying_body_not_another_batch_item(
    connect, world, deps
) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item("Cache TTL raised", "The TTL was raised.", updates=[_upd(old, replacement=REPL)]),
            item("Numbers", f"The TTL is {REPL} now."),  # the replacement lives HERE, not in the carrier
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["reason"]) == ("rejected", "replacement_not_in_body")
    assert len(ack["versions"]) == 2
    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]


@pytest.mark.parametrize(
    ("upd", "reason"),
    [
        ({"old_span": "90 seconds"}, "span_not_found"),
        ({"old_span": "cache"}, "span_not_unique"),
        ({"old_span": "0 seconds"}, "span_word_boundary"),
        ({"replacement": "900 seconds"}, "replacement_not_in_body"),
        ({"old_span": OLD[1]}, "span_whole"),
        ({"old_span": "TTL", "replacement": "The API cache TTL is now 300 seconds"}, "length_ratio"),
    ],
)
async def test_a_failed_guard_rejects_the_update_and_writes_the_memory(
    connect, world, deps, upd, reason
) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    spec = {**_upd(old, replacement=REPL), **upd}
    ack = await _write(connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[spec])], deps)
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", reason) and u["hint"]
    assert len(ack["versions"]) == 1
    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]
    await _replay_identical(connect)


# --------------------------------------------------------------------------- supersede
async def test_supersede_closes_the_old_item_and_links_it(connect, world, deps) -> None:  # noqa: ANN001
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
    (u,) = ack["updates"]
    assert u == {"index": 0, "update": 0, "status": "applied", "mode": "supersede"}
    (survivor,) = _current(await _rows(connect, old["logical_id"]))
    assert (survivor["body"], survivor["vf"], survivor["vt"]) == (OLD[1], D_OLD, D_EFF)  # closed at the cut
    ((src, dst, dst_v, rel, vf, props),) = await _links(connect)
    assert (src, dst, dst_v, rel, vf) == (
        new["logical_id"],
        old["logical_id"],
        old["version_id"],
        "supersedes",
        D_EFF,
    )
    assert props == {
        "by": "writer",
        "mode": "supersede",
        "scope": "whole",
        "quote": "The cache backend is Redis 7",
    }
    event_id = await _write_event_of(connect, new["version_id"])
    muts = (await _event(connect, event_id))["resolved"]["updates"][0]["mutations"]
    assert [m["op"] for m in muts] == ["version_close", "link_insert"]
    assert all(m["write_event_id"] == event_id and m["update"] == [0, 0] for m in muts)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- the memory is always written
async def test_a_version_conflict_writes_the_new_memory_and_rejects_the_update(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    (rev,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item(OLD[0], OLD[1] + "\nOwner: ops."),
                    "logical_id": old["logical_id"],
                    "expected_version_id": old["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    state = await _rows(connect, old["logical_id"])
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(old, replacement=REPL)])], deps
    )
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_VERSION_CONFLICT", "version_conflict")
    assert u["current_clue"] == f"v{rev['version_id']}"
    assert len(ack["versions"]) == 1 and ack["versions"][0]["logical_id"] != old["logical_id"]
    assert await _rows(connect, old["logical_id"]) == state
    # a clue contradicted by an explicit expected_version is refused too (consult 74 #1)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                *NEW_TTL,
                updates=[
                    _upd(
                        {"version_id": rev["version_id"]},
                        replacement=REPL,
                        expected_version=old["version_id"],
                    )
                ],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["reason"] == "expected_version_mismatch"
    await _replay_identical(connect)


async def test_hidden_or_unknown_targets_are_one_uniform_not_found(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    (scoped,) = (
        await _write(
            connect, world.ctx_a, MAIN, [item("Mine", OLD[1], device_scope=f"device:{world.dev_a}")], deps
        )
    )["versions"]
    (foreign,) = (await _write(connect, world.ctx_b, OTHER, [item("Other", OLD[1])], deps))["versions"]
    ctx_c = await _device(connect, "dev-c", {world.main_id: Role.WRITE})
    ack = await _write(
        connect,
        ctx_c,
        MAIN,
        [
            item(
                *NEW_TTL,
                updates=[
                    _upd(scoped, replacement=REPL),  # device-scoped to dev-a
                    _upd(foreign, replacement=REPL),  # another project, not shared with MAIN
                    {"item": "v999999", "old_span": SPAN, "mode": "revise"},  # does not exist
                    _upd(old, replacement=REPL),  # visible: applies
                ],
            )
        ],
        deps,
    )
    assert [(u["status"], u.get("code")) for u in ack["updates"]] == [
        ("rejected", "E_NOT_FOUND"),
        ("rejected", "E_NOT_FOUND"),
        ("rejected", "E_NOT_FOUND"),
        ("applied", None),
    ]
    assert {u["hint"] for u in ack["updates"][:3]} == {
        ack["updates"][0]["hint"]
    }  # nothing distinguishes them


async def test_a_future_dated_carrier_writes_but_does_not_mutate(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ep = await _old(connect, world, deps, kind="episode", body="Session: " + OLD[1])
    soon = (await _server_now(connect)) + timedelta(minutes=4)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                *NEW_TTL,
                valid_from=soon.isoformat(),
                updates=[
                    _upd(old, replacement=REPL),
                    _upd(old, mode="supersede", old_span="Redis 7"),
                ],
            ),
            item(
                "Later",
                "Episodes stay as they were.",
                valid_from=soon.isoformat(),
                updates=[_upd(ep, mode="supersede", old_span="Redis 7")],
            ),
        ],
        deps,
    )
    u0, u1, u2 = ack["updates"]
    # the two updates of item 0 name the same memory: a batch conflict before the time rule
    assert u0["reason"] == u1["reason"] == "batch_conflict"
    assert (u2["status"], u2["reason"]) == ("linked", "historical_kind")  # a link is no mutation
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(*NEW_TTL, valid_from=soon.isoformat(), updates=[_upd(old, replacement=REPL)]),
            item("Gone", "No cache any more.", valid_from=soon.isoformat()),
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["reason"]) == ("rejected", "future_valid_from")
    assert len(ack["versions"]) == 2
    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Gone",
                "No cache any more.",
                valid_from=soon.isoformat(),
                updates=[_upd(old, mode="supersede", old_span="Redis 7")],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["reason"] == "future_valid_from"
    assert _current(await _rows(connect, old["logical_id"]))[0]["vt"] is None  # never closed early


# --------------------------------------------------------------------------- batch conflicts
async def test_a_to_b_chains_and_double_targets_are_batch_conflicts(connect, world, deps) -> None:  # noqa: ANN001
    """Consult 74 #5: the conflict set is every id the WHOLE batch mutates, whatever the order."""
    x = await _old(connect, world, deps)
    y = await _old(
        connect, world, deps, body="The queue is RabbitMQ 3 on the worker host.\nIt is monitored.\nBy ops."
    )
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            {
                **item("X", OLD[1] + "\nRevised."),
                "logical_id": x["logical_id"],
                "expected_version_id": x["version_id"],
                "updates": [_upd(y, old_span="RabbitMQ 3", mode="supersede")],
            },
            {
                **item("Y", "The queue is RabbitMQ 4 on the worker host.\nIt is monitored.\nBy ops."),
                "logical_id": y["logical_id"],
                "expected_version_id": y["version_id"],
                "updates": [_upd(x, mode="supersede", old_span="Redis 7")],
            },
        ],
        deps,
    )
    assert [(u["status"], u["reason"]) for u in ack["updates"]] == [("rejected", "batch_conflict")] * 2
    assert len(ack["versions"]) == 2  # both revisions are written
    for target, text in ((x, OLD[1] + "\nRevised."), (y, "RabbitMQ 4")):
        head = _current(await _rows(connect, target["logical_id"]))[-1]
        assert head["vt"] is None and text in head["body"]  # the revision stands; nothing closed
    await _replay_identical(connect)


# --------------------------------------------------------------------------- historical records
@pytest.mark.parametrize(
    ("kind", "body", "reason"),
    [
        ("episode", "Session log: " + OLD[1], "historical_kind"),
        ("session_note", "Notes: " + OLD[1], "historical_kind"),
        ("fact", "D-047 | 2026-01-01 | ACCEPTED | " + OLD[1], "decision_record"),
    ],
)
async def test_historical_records_are_link_only_whatever_the_kinds_config(
    connect, world, deps, monkeypatch: pytest.MonkeyPatch, kind: str, body: str, reason: str
) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_LIBRARIAN_REVISE_KINDS", "fact,lesson,doc_chunk,episode,session_note")
    target = await _old(connect, world, deps, kind=kind, body=body)
    before = await _rows(connect, target["logical_id"])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(*NEW_TTL, updates=[_upd(target, replacement=REPL)]),
            item("Removed", "The cache backend was removed.", updates=[]),
        ],
        deps,
    )
    (u,) = ack["updates"]
    assert (u["status"], u["reason"]) == ("linked", reason) and "code" not in u
    ack2 = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Removed",
                "The cache backend was removed.",
                updates=[_upd(target, mode="supersede", old_span="Redis 7")],
            )
        ],
        deps,
    )
    assert (ack2["updates"][0]["status"], ack2["updates"][0]["reason"]) == ("linked", reason)
    assert await _rows(connect, target["logical_id"]) == before  # text and validity untouched
    links = await _links(connect)
    assert [(ln[1], ln[3], ln[5]["scope"]) for ln in links] == [
        (target["logical_id"], "supersedes", "part"),  # revise mode: the quoted statement
        (target["logical_id"], "supersedes", "whole"),
    ]
    assert links[0][5]["quote"] == SPAN
    await _replay_identical(connect)


# --------------------------------------------------------------------------- scope + policy
async def _shared_target(connect, world, deps) -> dict:  # noqa: ANN001
    """An item homed in OTHER and shared with MAIN (visible from MAIN; touches both projects)."""
    (v,) = (
        await _write(
            connect,
            world.ctx_a,
            OTHER,
            [item(OLD[0], OLD[1], valid_from=D_OLD.isoformat(), project_ids=[OTHER, MAIN])],
            deps,
        )
    )["versions"]
    return v


async def test_a_cross_project_update_is_refused_without_the_capability(connect, world, deps) -> None:  # noqa: ANN001
    target = await _shared_target(connect, world, deps)
    ctx_c = await _device(connect, "dev-c", {world.main_id: Role.WRITE, world.other_id: Role.READ})
    before = await _rows(connect, target["logical_id"])
    ack = await _write(connect, ctx_c, MAIN, [item(*NEW_TTL, updates=[_upd(target, replacement=REPL)])], deps)
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_FORBIDDEN_PROJECT", "forbidden_project")
    assert await _rows(connect, target["logical_id"]) == before and len(ack["versions"]) == 1
    # the D-083 policy: an excluded project is never related to another one
    async with await connect() as conn:
        await conn.execute(
            EXCLUDE_SQL,
            (world.other_id,),
        )
        await conn.commit()
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, project_ids=[MAIN, OTHER], updates=[_upd(target, replacement=REPL)])],
        deps,
    )
    assert (ack["updates"][0]["code"], ack["updates"][0]["reason"]) == (
        "E_FORBIDDEN_PROJECT",
        "policy_excluded",
    )
    async with await connect() as conn:
        await conn.execute("UPDATE projects SET policy = policy - 'librarian_cross_project'")
        await conn.commit()
    # with write on both projects (the capability) and no exclusion it applies; the replacement's
    # readers must cover the target's (the carrier is shared with OTHER too)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, updates=[_upd(target, replacement=REPL)]), item(*NEW_TTL, project_ids=[MAIN, OTHER])],
        deps,
    )
    assert ack["updates"][0]["reason"] == "replacement_visibility"
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, project_ids=[MAIN, OTHER], updates=[_upd(target, replacement=REPL)])],
        deps,
    )
    assert ack["updates"][0]["status"] == "applied"
    await _replay_identical(connect)


# --------------------------------------------------------------------------- locks (J/D-095)
async def test_concurrent_writes_updating_the_same_item_one_wins_the_other_conflicts(
    connect, world, deps
) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    blocker = await connect()
    await q.lock_logical_ids(blocker, [old["logical_id"]])  # both writers queue on the item lock

    async def writer(body: str) -> dict:
        return await _write(
            connect, world.ctx_a, MAIN, [item("TTL", body, updates=[_upd(old, replacement=REPL)])], deps
        )

    tasks = [asyncio.create_task(writer(f"The API cache TTL is {REPL} (writer {i}).")) for i in range(2)]
    await asyncio.sleep(0.5)
    assert not any(t.done() for t in tasks)  # waiting on the item lock
    await blocker.commit()
    await blocker.close()
    acks = await asyncio.gather(*tasks)
    outcomes = sorted((a["updates"][0]["status"], a["updates"][0].get("code")) for a in acks)
    assert outcomes == [("applied", None), ("rejected", "E_VERSION_CONFLICT")]
    assert all(len(a["versions"]) == 1 for a in acks)  # both memories written
    (head,) = [r for r in _current(await _rows(connect, old["logical_id"])) if r["vt"] is None]
    assert head["body"].count(REPL) == 1
    await _replay_identical(connect)


async def test_the_policy_is_read_after_the_item_locks(connect, world, deps) -> None:  # noqa: ANN001
    """Lock order items → policy: a policy change committed while the writer waits on the item
    lock is what the writer sees."""
    target = await _shared_target(connect, world, deps)
    blocker = await connect()
    await q.lock_logical_ids(blocker, [target["logical_id"]])
    task = asyncio.create_task(
        _write(
            connect,
            world.ctx_a,
            MAIN,
            [item(*NEW_TTL, project_ids=[MAIN, OTHER], updates=[_upd(target, replacement=REPL)])],
            deps,
        )
    )
    await asyncio.sleep(0.4)
    assert not task.done()
    async with await connect() as conn:  # the policy changes while the writer waits
        await conn.execute(
            EXCLUDE_SQL,
            (world.other_id,),
        )
        await conn.commit()
    await blocker.commit()
    await blocker.close()
    ack = await task
    assert ack["updates"][0]["reason"] == "policy_excluded"
    assert [r["body"] for r in _current(await _rows(connect, target["logical_id"]))] == [OLD[1]]


# --------------------------------------------------------------------------- idempotency
async def test_a_retry_returns_the_stored_ack_and_a_plain_write_has_no_updates_key(
    connect, world, deps
) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    req = write_req(MAIN, [item(*NEW_TTL, updates=[_upd(old, replacement=REPL)])])
    async with await connect() as conn:
        first = (await write(conn, world.ctx_a, req, deps=deps)).model_dump(mode="json")
        await conn.commit()
    async with await connect() as conn:
        again = (await write(conn, world.ctx_a, req, deps=deps)).model_dump(mode="json")
        await conn.commit()
    assert again["replayed"] is True and again["updates"] == first["updates"]
    assert len(_current(await _rows(connect, old["logical_id"]))) == 2  # applied once
    plain = await _write(connect, world.ctx_a, MAIN, [item("Plain", "Nothing to update here.")], deps)
    assert "updates" not in plain


# --------------------------------------------------------------------------- MCP wire
async def test_over_mcp_the_schema_is_listed_and_a_rejected_update_is_not_an_error(db_dsn, connect) -> None:  # noqa: ANN001
    import json

    from tests.integration._mcp_fixtures import (
        call_tool_raw,
        fact,
        mcp_rpc,
        running_app,
        write_args,
        writer_on,
    )

    async with running_app(db_dsn) as client:
        token = await writer_on(client, "wu-wire")
        listed = (await mcp_rpc(client, token, "tools/list")).json()["result"]["tools"]
        (w,) = [t for t in listed if t["name"] == "memory.write"]
        assert "updates" in w["inputSchema"]["$defs"]["Item"]["properties"] and "old_span" in w["description"]
        first = await call_tool_raw(client, token, "memory.write", write_args("wu-wire", [fact(*OLD)]))
        (old,) = json.loads(first["content"][0]["text"])["versions"]
        res = await call_tool_raw(
            client,
            token,
            "memory.write",
            write_args(
                "wu-wire",
                [fact(*NEW_TTL, updates=[_upd(old, replacement=REPL), _upd(old, old_span="nope")])],
            ),
        )
        assert not res.get("isError"), res  # the memory is written; the updates report their outcome
        ack = json.loads(res["content"][0]["text"])
        assert [(u["status"], u.get("reason")) for u in ack["updates"]] == [
            ("rejected", "batch_conflict"),
            ("rejected", "batch_conflict"),
        ]
        res = await call_tool_raw(
            client,
            token,
            "memory.write",
            write_args("wu-wire", [fact(*NEW_TTL, updates=[_upd(old, replacement=REPL)])]),
        )
        (u,) = json.loads(res["content"][0]["text"])["updates"]
        assert u["status"] == "applied" and u["clue"].startswith("v")


# --------------------------------------------------------------------------- reversal
async def test_revert_is_one_compensating_event_and_refused_while_a_later_change_depends(
    connect, world, deps
) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW_TTL, valid_from=D_EFF.isoformat(), updates=[_upd(old, replacement=REPL)])],
        deps,
    )
    e1 = await _write_event_of(connect, ack["versions"][0]["version_id"])
    head1 = _current(await _rows(connect, old["logical_id"]))[-1]
    ack2 = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Backend",
                "The cache backend is Redis 8 on the api host.",
                updates=[
                    {
                        "item": f"v{head1['vid']}",
                        "old_span": "Redis 7",
                        "mode": "revise",
                        "replacement": "Redis 8",
                    }
                ],
            )
        ],
        deps,
    )
    assert ack2["updates"][0]["status"] == "applied"
    e2 = await _write_event_of(connect, ack2["versions"][0]["version_id"])

    async def revert(event_id: int) -> dict[str, Any]:
        async with await connect() as conn:
            out = await revert_write_update(
                conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="undo"
            )
            await conn.commit()
        return out

    state = await _rows(connect, old["logical_id"])
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await revert_write_update(conn, event_id=e1, index=0, update=0, by=world.ctx_a, reason="undo")
        await conn.rollback()
    assert ei.value.code == "E_VERSION_CONFLICT" and "depends" in ei.value.message
    assert await _rows(connect, old["logical_id"]) == state
    async with await connect() as conn:
        n_events = await count(conn, "events")
    await revert(e2)
    await revert(e1)
    cur = _current(await _rows(connect, old["logical_id"]))
    assert [r["body"] for r in cur] == [OLD[1]] and cur[0]["vf"] == D_OLD and cur[0]["vt"] is None
    async with await connect() as conn:
        assert await count(conn, "events") == n_events + 2  # ONE compensating event each
        assert await count(conn, "links", "superseded_at = 'infinity'") == 0
        cur2 = await conn.execute(
            "SELECT payload->'request'->>'op' FROM events WHERE kind = 'librarian' ORDER BY event_id"
        )
        assert [r[0] for r in await cur2.fetchall()] == ["revert_write_update"] * 2
    async with await connect() as conn:  # a reversal happens once
        with pytest.raises(ToolError) as ei:
            await revert_write_update(conn, event_id=e1, index=0, update=0, by=world.ctx_a, reason="again")
        await conn.rollback()
    assert ei.value.code == "E_VERSION_CONFLICT"
    await _replay_identical(connect)


async def test_revert_of_a_supersede_reopens_the_item_and_ends_the_link(connect, world, deps) -> None:  # noqa: ANN001
    old = await _old(connect, world, deps)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "Cache replaced",
                "No cache any more.",
                updates=[_upd(old, mode="supersede", old_span="Redis 7")],
            )
        ],
        deps,
    )
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    async with await connect() as conn:
        out = await revert_write_update(
            conn, event_id=event_id, index=0, update=0, by=world.ctx_a, reason="undo"
        )
        await conn.commit()
    assert len(out["restored"]) == 1 and out["links_superseded"] == 1
    (cur,) = _current(await _rows(connect, old["logical_id"]))
    assert (cur["body"], cur["vf"], cur["vt"]) == (OLD[1], D_OLD, None)
    assert await _links(connect) == []
    async with await connect() as conn:  # a rejected update has nothing to revert
        ack = await _write(
            connect,
            world.ctx_a,
            MAIN,
            [item("X", "Y.", updates=[{"item": "v999999", "old_span": "a", "mode": "revise"}])],
            deps,
        )
        ev = await _write_event_of(connect, ack["versions"][0]["version_id"])
        with pytest.raises(ToolError) as ei:
            await revert_write_update(conn, event_id=ev, index=0, update=0, by=world.ctx_a, reason="x")
        await conn.rollback()
    assert ei.value.code == "E_VERSION_CONFLICT"
    await _replay_identical(connect)


async def test_the_ops_command_reverts_an_update(
    connect, world, deps, capsys: pytest.CaptureFixture[str]
) -> None:  # noqa: ANN001
    """``python -m hlmemo.ops librarian revert-update E --item I --update K --reason …``."""
    import json

    from hlmemo.ops import librarian as ops_lib
    from hlmemo.ops.cli import build_parser

    old = await _old(connect, world, deps)
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(old, replacement=REPL)])], deps
    )
    event_id = await _write_event_of(connect, ack["versions"][0]["version_id"])
    args = build_parser().parse_args(
        [
            "librarian",
            "revert-update",
            str(event_id),
            "--item",
            "0",
            "--update",
            "0",
            "--reason",
            "wrong memory",
        ]
    )
    async with await connect() as conn:
        assert await ops_lib.dispatch(conn, args) == 0
        await conn.commit()
    out = json.loads(capsys.readouterr().out)
    assert out["reverts_event"] == event_id and len(out["restored"]) == 1
    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request' FROM events WHERE kind = 'librarian' ORDER BY event_id DESC LIMIT 1"
        )
        (req,) = await cur.fetchone()
        await conn.rollback()
    assert (req["op"], req["reverts_event"], req["index"], req["update"], req["reason"]) == (
        "revert_write_update",
        event_id,
        0,
        0,
        "wrong memory",
    )
    await _replay_identical(connect)
