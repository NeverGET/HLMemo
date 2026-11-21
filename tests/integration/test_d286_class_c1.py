"""D-286: the librarian class grant C1 through the real W2b pipeline (scripted Oracle: these tests
prove the deterministic machinery around the model, never model quality).

* C1 applied: a granted project, the switch on and a C1 judgement give exactly ONE link, the pinned
  part ``supersedes`` link with ``props.auto = "C1"`` and the grant's decision; no ``contradicts``
  link, no close, no question; the job's event records role ``observer``; replay is identical.
* Every class condition false gives no direct apply (the question path as before).
* Switch off, an ungranted project, one ungranted touched project, a revoked grant: today's
  behaviour byte for byte (same questions, same events, no new mutation kind).
* With C1 on, the observer is unchanged for every other class (relates_to, whole closes,
  pair_check, approved batches, accepted_pending answers).
* Grants: admin only, per project only, the latest decision wins.
* Revert ends the link (readers stop flagging), records the pair (never applied again), and the
  30-day rate above .05 demotes the project; ``class applied`` marks reverted links.
* Readers: ``memory.raw`` ``superseded_by`` and ``links`` carry ``applied_by`` for C1 links only.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import raw
from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian import classes
from hlmemo.librarian.jobs import enqueue, job_spec
from hlmemo.librarian.provider import lineage_scope
from hlmemo.librarian.roles import record_batch_decision
from tests.conftest import TRUNCATE_SQL
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    ScriptedLLM,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, dump_w2b, embed, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world, write_req

pytestmark = pytest.mark.integration

D1 = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D3 = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
MULTI = (
    "Cache setup",
    "The API cache TTL is 60 seconds.\n- The cache is stored in Redis 7 on the api host.\n"
    "- Cache keys are prefixed with the tenant id.",
)
TTL = ("Cache TTL changed", "Since June the API cache TTL is 300 seconds.")
REL = {(TTL[0], MULTI[0]): ("contradicts", "new", "high")}
PART = {(TTL[0], MULTI[0]): "part"}
_TIME_KEYS = {"recorded_at", "done_at", "run_after", "expires_at", "created_at"}


def c1_oracle(**kw: Any) -> Oracle:
    return Oracle(**{"relations": dict(REL), "scope": dict(PART), **kw})


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _grant(
    connect, world: World, on: bool = True, project: int | None = None, decision: str = "D-286"
) -> int:  # noqa: ANN001
    async with await connect() as conn:
        event_id = await classes.record_class_decision(
            conn,
            cls="C1",
            on=on,
            decided_by=world.ctx_admin,
            decision=decision,
            project_id=world.main_id if project is None else project,
        )
        await conn.commit()
    return event_id


async def _drain(db_dsn, connect, oracle: Any, *, c1: bool = True, role: str = "observer", **kw: Any) -> int:  # noqa: ANN001
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role=role, librarian_c1=c1, **kw), provider, connect)
    n = await worker.drain(concurrency=1)
    await provider.aclose()
    return n


async def _pair(
    connect,  # noqa: ANN001
    world: World,
    embedder,  # noqa: ANN001
    *,
    old: tuple[str, str] = MULTI,
    new: tuple[str, str] = TTL,
    old_kw: dict[str, Any] | None = None,
    new_kw: dict[str, Any] | None = None,
    old_project: str = MAIN,
    old_from: str = D1,
    new_from: str = D3,
) -> tuple[Any, Any]:
    (o,) = await write_items(
        connect, world.ctx_a, old_project, [item(*old, valid_from=old_from, **(old_kw or {}))]
    )
    (n,) = await write_items(connect, world.ctx_a, MAIN, [item(*new, valid_from=new_from, **(new_kw or {}))])
    await embed(connect, embedder)
    return o, n


async def _links(connect) -> list[tuple[Any, ...]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT rel, src_logical_id, dst_logical_id, dst_version_id, props, superseded_at = 'infinity'"
            " FROM links ORDER BY link_id"
        )
        return list(await cur.fetchall())


async def _c1_links(connect) -> int:  # noqa: ANN001
    async with await connect() as conn:
        return await count(conn, "links", "props->>'auto' = 'C1'")


async def _questions(connect) -> list[tuple[Any, ...]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT kind, status, subject_clues, proposal FROM librarian_questions"
            " ORDER BY created_at, question_id"
        )
        return list(await cur.fetchall())


async def _resolved(connect) -> list[dict[str, Any]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'resolved' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' ORDER BY event_id"
        )
        return [r[0] for r in await cur.fetchall()]


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_w2b(conn) == before


async def _enqueue_review(connect, world: World, version: Any, key: str) -> None:  # noqa: ANN001
    """A second write review of ``version`` (as a backfill would enqueue it)."""
    async with await connect() as conn:
        spec = job_spec(
            kind="librarian_write",
            dedupe_key=f"librarian_write:{key}",
            priority=6,
            payload={
                "op": "write_review",
                "trigger": "backfill",
                "versions": [
                    {
                        "version_id": version.version_id,
                        "kind": "fact",
                        "client_importance": None,
                        "client_stability": False,
                    }
                ],
            },
        )
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()


def _strip_times(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_times(v) for k, v in obj.items() if k not in _TIME_KEYS}
    if isinstance(obj, list):
        return [_strip_times(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- 1. C1 applied
async def test_c1_applies_the_pinned_part_link_alone(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    await _grant(connect, world)
    old, new = await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle())
    [(rel, src, dst, dst_vid, props, live)] = await _links(connect)
    assert (rel, src, dst, dst_vid, live) == (
        "supersedes",
        new.logical_id,
        old.logical_id,
        old.version_id,
        True,
    )
    assert props["auto"] == "C1" and props["decision"] == "D-286" and props["by"] == "librarian"
    assert props["scope"] == "part" and props["quote"].startswith("The API cache TTL is 60 seconds")
    assert props["relation"] == "contradicts" and props["confidence"] == "high"
    assert await _questions(connect) == []  # no question row, no batch
    async with await connect() as conn:
        assert (
            await count(conn, "memory_versions", "superseded_at <> 'infinity' OR valid_to <> 'infinity'") == 0
        )
        assert await count(conn, "librarian_batches") == 0
    review = await _resolved(connect)
    assert [r["role"] for r in review] == ["observer", "observer"]
    last = review[-1]
    assert last["outcome"] == "applied" and last["questions"] == []
    assert [m["op"] for m in last["mutations"] if m["op"] != "signal_upsert"] == ["link_insert"]
    assert {m["op"] for r in review for m in r["mutations"]} == {"signal_upsert", "link_insert"}
    # readers tell the automatic correction apart (memory.raw: incoming superseded_by, outgoing links)
    async with await connect() as conn:
        out_old = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": old.version_id, "token_budget": 4000},
            deps=read_deps,
        )
        out_new = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": new.version_id, "token_budget": 4000},
            deps=read_deps,
        )
        await conn.commit()
    [entry] = out_old["superseded_by"]
    assert entry["scope"] == "part" and entry["applied_by"] == "librarian (C1)"
    [link] = out_new["links"]
    assert link["rel"] == "supersedes" and link["dst_version_id"] == old.version_id
    assert link["applied_by"] == "librarian (C1)"
    # the audit lists it with both items, the quote and the job event
    async with await connect() as conn:
        [row] = await classes.applied(conn, world.main_id)
        await conn.commit()
    assert row["newer"]["clue"] == f"v{new.version_id}" and row["older"]["clue"] == f"v{old.version_id}"
    assert (row["newer"]["title"], row["older"]["title"]) == (TTL[0], MULTI[0])
    assert row["quote"].startswith("The API cache TTL") and row["reverted"] is False
    assert row["job"].startswith("librarian_write:") and row["decision"] == "D-286"
    await _replay_identical(connect)


async def test_readers_label_only_class_links(db_dsn, connect, world: World, embedder, read_deps) -> None:  # noqa: ANN001
    """A writer's supersedes link shows no ``applied_by`` (nor does the proposal of a question)."""
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*MULTI, valid_from=D1)], deps=default_deps())
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        *TTL,
                        valid_from=D3,
                        updates=[
                            {
                                "item": f"v{old.version_id}",
                                "old_span": "The API cache TTL is 60 seconds.",
                                "mode": "supersede",
                            }
                        ],
                    )
                ],
            ),
            deps=default_deps(),
        )
        await conn.commit()
        (new,) = res.versions
        out_old = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": old.version_id, "token_budget": 4000},
            deps=read_deps,
        )
        out_new = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": new.version_id, "token_budget": 4000},
            deps=read_deps,
        )
        await conn.commit()
    assert out_old["superseded_by"] and all("applied_by" not in e for e in out_old["superseded_by"])
    assert out_new["links"] and all("applied_by" not in ln for ln in out_new["links"])


# --------------------------------------------------------------------------- 2. each condition false
CASES: dict[str, dict[str, Any]] = {
    "1 whole scope": {"oracle": {"scope": {}}},
    "2 question tier": {"oracle": {"relations": {(TTL[0], MULTI[0]): ("contradicts", "new", "med")}}},
    "2 verifier disagrees": {
        "oracle": {
            "verify": lambda a, b: {
                "same_subject": True,
                "conflict": False,
                "current": "both",
                "replaces_all": False,
                "adds_detail": "none",
            }
        }
    },
    "3 both lessons": {"old_kw": {"kind": "lesson"}, "new_kw": {"kind": "lesson"}},
    "3 decision row": {"old": ("Cache decision", "D-047 | 2026-01-01 | The API cache TTL is 60 seconds.")},
    "3 older episode": {"old_kw": {"kind": "episode"}},
    "4 device scope differs": {"new_kw": {"device_scope": "class:personal"}},
    "4 older pinned": {"old_kw": {"pinned": True}},
    "4 cross project": {"old_project": OTHER, "grant_other": True},
    "5 newer is older in valid time": {"old_from": D3, "new_from": D1},
    "6 unverified quote": {"oracle": {"quote_ok": False}},
}


@pytest.mark.parametrize("case", sorted(CASES))
async def test_each_class_condition_false_applies_nothing(
    db_dsn, connect, world: World, embedder, case: str
) -> None:  # noqa: ANN001
    spec = CASES[case]
    await _grant(connect, world)
    if spec.get("grant_other"):
        await _grant(connect, world, project=world.other_id)
    old = spec.get("old", MULTI)
    oracle_kw = dict(spec.get("oracle", {}))
    if old is not MULTI:
        oracle_kw.setdefault("relations", {(TTL[0], old[0]): ("contradicts", "new", "high")})
        oracle_kw.setdefault("scope", {(TTL[0], old[0]): "part"})
    await _pair(
        connect,
        world,
        embedder,
        old=old,
        old_kw=spec.get("old_kw"),
        new_kw=spec.get("new_kw"),
        old_project=spec.get("old_project", MAIN),
        old_from=spec.get("old_from", D1),
        new_from=spec.get("new_from", D3),
    )
    await _drain(db_dsn, connect, c1_oracle(**oracle_kw))
    assert await _links(connect) == [], case  # observer: nothing applied at all
    for _kind, status, _clues, prop in await _questions(connect):
        assert status == "open" and all("auto" not in (a.get("props") or {}) for a in prop["actions"])
    assert {m["op"] for r in await _resolved(connect) for m in r["mutations"]} <= {"signal_upsert"}


async def test_stale_older_item_is_never_linked(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """7: the older item is revised between the model's judgement and the apply."""
    await _grant(connect, world)
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*MULTI, valid_from=D1)])
    await embed(connect, embedder)
    await _drain(db_dsn, connect, c1_oracle())
    (new,) = await write_items(connect, world.ctx_a, MAIN, [item(*TTL, valid_from=D3)])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=c1_oracle())
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_c1=True), provider, connect)
    job = await worker.lease_one()
    assert job is not None
    with lineage_scope(job.lineage):
        plan = await worker.handlers[job.payload["op"]].plan(worker, job)
    assert [p.c1 is not None for p in plan.proposals] == [True]
    async with await connect() as conn:  # the older item changes after the model assessed it
        await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        MULTI[0],
                        MULTI[1] + "\n- Reworded.",
                        logical_id=old.logical_id,
                        expected_version_id=old.version_id,
                    )
                ],
            ),
            deps=default_deps(),
        )
        await conn.commit()
    async with await connect() as conn:
        await worker.apply(conn, job, plan)
    await provider.aclose()
    assert await _links(connect) == []
    [(_kind, status, _clues, _prop)] = await _questions(connect)
    assert status == "superseded"  # today's stale path


async def test_pending_question_for_the_pair_blocks_c1(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """7: the pair already waits for Cemal (asked while C1 was off): a later review applies nothing."""
    _old, new = await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle(), c1=False)
    [(_k, status, _c, _p)] = await _questions(connect)
    assert status == "open"
    await _grant(connect, world)
    await _enqueue_review(connect, world, new, "again")
    await _drain(db_dsn, connect, c1_oracle())
    assert await _links(connect) == []
    assert [s for _k, s, _c, _p in await _questions(connect)] == ["open"]  # no duplicate either


async def test_daily_budget_per_project(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """9: two C1 pairs in one job with a budget of 1: one link, one question; a budget of 0: none."""
    other = ("Cache size", "The API cache holds 500 entries.\n- It is warmed at startup.")
    new = ("Cache tuned", "Since June the API cache TTL is 300 seconds and it holds 900 entries.")
    await _grant(connect, world)
    await write_items(connect, world.ctx_a, MAIN, [item(*MULTI, valid_from=D1), item(*other, valid_from=D1)])
    (n,) = await write_items(connect, world.ctx_a, MAIN, [item(*new, valid_from=D3)])
    await embed(connect, embedder)
    rel = {
        (new[0], MULTI[0]): ("contradicts", "new", "high"),
        (new[0], other[0]): ("contradicts", "new", "high"),
    }
    scope = {(new[0], MULTI[0]): "part", (new[0], other[0]): "part"}
    await _drain(db_dsn, connect, Oracle(relations=rel, scope=scope), librarian_c1_daily_max=1)
    links = await _links(connect)
    assert [(r, p["auto"]) for r, _s, _d, _v, p, _l in links] == [("supersedes", "C1")]
    [(kind, status, _c, _p)] = await _questions(connect)
    assert (kind, status) == ("contradiction", "open")
    # the same day, a second job: the budget is used up (the reverted links would count too)
    await _enqueue_review(connect, world, n, "again")
    await _drain(db_dsn, connect, Oracle(relations=rel, scope=scope), librarian_c1_daily_max=1)
    assert await _c1_links(connect) == 1


async def test_daily_budget_zero_applies_nothing(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _grant(connect, world)
    await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle(), librarian_c1_daily_max=0)
    assert await _links(connect) == []
    assert [s for _k, s, _c, _p in await _questions(connect)] == ["open"]


# --------------------------------------------------------------------------- 3. switches: byte-identical
async def _scenario(
    connect,
    db_dsn,
    world: World,
    embedder,
    grants: list[tuple[str, bool]],
    c1: bool,  # noqa: ANN001
    multi_project: bool,
) -> dict[str, Any]:
    for project, on in grants:
        await _grant(connect, world, on=on, project=world.main_id if project == MAIN else world.other_id)
    await _pair(connect, world, embedder, old_kw={"project_ids": [MAIN, OTHER]} if multi_project else None)
    await _drain(db_dsn, connect, c1_oracle(), c1=c1)
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id::text, batch_id::text, kind, status, subject_clues, subject_version_ids,"
            " proposal,"
            " source_event_id FROM librarian_questions ORDER BY question_id"
        )
        questions = [list(r) for r in await cur.fetchall()]
        links = await count(conn, "links")
    return {"questions": questions, "links": links, "resolved": _strip_times(await _resolved(connect))}


VARIANTS = {
    "switch off, granted": ([(MAIN, True), (OTHER, True)], False, False),
    "switch on, project not granted": ([(OTHER, True), (OTHER, True)], True, False),
    "switch on, grant revoked": ([(MAIN, True), (MAIN, False)], True, False),
    "switch on, one touched project ungranted": ([(MAIN, True), (OTHER, False)], True, True),
}


@pytest.mark.parametrize("variant", sorted(VARIANTS))
async def test_switch_off_or_ungranted_is_todays_behaviour_byte_for_byte(
    db_dsn, connect, world: World, embedder, variant: str
) -> None:  # noqa: ANN001
    grants, c1, multi = VARIANTS[variant]
    # today: the same number of decision events (so every id lines up), none of them on, switch off
    baseline = await _scenario(connect, db_dsn, world, embedder, [(MAIN, False), (MAIN, False)], False, multi)
    assert baseline["links"] == 0 and len(baseline["questions"]) == 1
    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
        assert await seed_world(conn) == world
        await seed_reserved(conn)
    got = await _scenario(connect, db_dsn, world, embedder, grants, c1, multi)
    assert got == baseline


# --------------------------------------------------------------------------- 4. observer unchanged
async def test_observer_unchanged_for_every_other_class_with_c1_on(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """C1 on and granted: an action-tier duplicate (relates_to) and a whole-scope supersession stay
    questions; an approved batch is not applied (the observer leases no apply job); an accepted
    answer stays accepted_pending; pair_check never applies."""
    dup_a = ("Embed threads", "The embed worker runs with 2 intra-op threads on the api host.")
    dup_b = ("Embed threads again", "The embed worker runs with 2 intra-op threads on the api host.")
    whole_old = ("Queue size", "The job queue holds 100 jobs.")
    whole_new = ("Queue size raised", "Since June the job queue holds 400 jobs.")
    await _grant(connect, world)
    await write_items(
        connect, world.ctx_a, MAIN, [item(*dup_a, valid_from=D1), item(*whole_old, valid_from=D1)]
    )
    await write_items(
        connect, world.ctx_a, MAIN, [item(*dup_b, valid_from=D3), item(*whole_new, valid_from=D3)]
    )
    await embed(connect, embedder)
    rel = {
        (dup_b[0], dup_a[0]): ("duplicate", "none", "high"),
        (whole_new[0], whole_old[0]): ("contradicts", "new", "high"),
    }
    await _drain(db_dsn, connect, Oracle(relations=rel))
    qs = await _questions(connect)
    assert sorted(k for k, _s, _c, _p in qs) == ["contradiction", "link"]
    assert all(s == "open" for _k, s, _c, _p in qs)
    assert any(p["auto_class"] for _k, _s, _c, p in qs)  # the auto-rule class: still a question
    assert await _links(connect) == []
    # pair_check (the W2a contradiction job) never qualifies
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT version_id FROM memory_versions WHERE title = ANY(%s) ORDER BY title",
            ([whole_old[0], whole_new[0]],),
        )
        old_vid, new_vid = [r[0] for r in await cur.fetchall()]
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new_vid,
            candidate_vids=[old_vid],
            key="pc",
        )
        await conn.commit()
    await _drain(db_dsn, connect, CONTRADICTS_B)
    assert await _links(connect) == []
    # an approved batch stays unapplied in observer
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT batch_id::text FROM librarian_questions WHERE status = 'open'"
        )
        for (batch,) in await cur.fetchall():
            await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    await _drain(db_dsn, connect, Oracle())
    assert await _links(connect) == []
    async with await connect() as conn:
        assert await count(conn, "jobs", "payload->>'op' = 'apply_batch' AND status = 'queued'") >= 1
        assert await count(conn, "librarian_questions", "status = 'approved'") >= 1


# --------------------------------------------------------------------------- 5. grants
async def test_grant_is_admin_only_per_project_and_latest_wins(connect, world: World) -> None:  # noqa: ANN001
    async with await connect() as conn:
        for ctx, project, decision in (
            (world.ctx_a, world.main_id, "D-286"),  # a writer device: never
            (world.ctx_admin, None, "D-286"),  # no project-less grant
            (world.ctx_admin, world.main_id, "  "),  # a grant names its decision
        ):
            with pytest.raises(ToolError):
                await classes.record_class_decision(
                    conn, cls="C1", on=True, decided_by=ctx, decision=decision, project_id=project
                )
            await conn.rollback()
        with pytest.raises(ToolError):
            await classes.record_class_decision(
                conn, cls="C2", on=True, decided_by=world.ctx_admin, decision="D-1", project_id=world.main_id
            )
        await conn.rollback()
        assert await classes.grant(conn, world.main_id) == (False, None)
        await conn.commit()
    await _grant(connect, world, decision="D-286")
    async with await connect() as conn:
        assert await classes.grant(conn, world.main_id) == (True, "D-286")
        assert await classes.grant(conn, world.other_id) == (False, None)  # per project only
        await conn.commit()
    await _grant(connect, world, on=False, decision="D-287")
    async with await connect() as conn:
        assert await classes.grant(conn, world.main_id) == (False, "D-287")
        await conn.commit()
    await _grant(connect, world, decision="D-288")
    async with await connect() as conn:
        assert await classes.grant(conn, world.main_id) == (True, "D-288")
        await conn.commit()
    async with await connect() as conn:  # a role decision is not a grant (and the reverse)
        from hlmemo.librarian.roles import latest_role_decision

        assert await latest_role_decision(conn, world.main_id) is None
        await conn.commit()


async def test_guest_account_projects_are_never_granted_nor_applied_in(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """Cemal, 2026-11-21: a guest account's project (``policy.account``, D-274) never gets C1, and a
    granted project moved into an account afterwards applies nothing."""
    async with await connect() as conn:
        await conn.execute(
            'UPDATE projects SET policy = policy || \'{"account": "guest"}\'::jsonb WHERE project_id = %s',
            (world.other_id,),
        )
        await conn.commit()
        with pytest.raises(ToolError):
            await classes.record_class_decision(
                conn,
                cls="C1",
                on=True,
                decided_by=world.ctx_admin,
                decision="D-286",
                project_id=world.other_id,
            )
        await conn.rollback()
        # turning it off stays possible
        await classes.record_class_decision(
            conn, cls="C1", on=False, decided_by=world.ctx_admin, decision="D-286", project_id=world.other_id
        )
        await conn.commit()
    await _grant(connect, world)
    async with await connect() as conn:
        await conn.execute(
            'UPDATE projects SET policy = policy || \'{"account": "guest"}\'::jsonb WHERE project_id = %s',
            (world.main_id,),
        )
        await conn.commit()
    await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle())
    assert await _links(connect) == []
    assert [s for _k, s, _c, _p in await _questions(connect)] == ["open"]


def _ops(db_dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HLM_DB_DSN": db_dsn, "HLM_API_PORT": "9"}
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return subprocess.run(
        [sys.executable, "-m", "hlmemo.ops", "librarian", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --------------------------------------------------------------------------- 6. revert and demotion
async def test_revert_ends_the_link_records_the_pair_and_demotes(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    proc = _ops(db_dsn, "class", "set", "C1", "on", "--project", MAIN, "--decision", "D-286")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["on"] is True
    old, new = await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle())
    [(_rel, _s, _d, _v, _p, live)] = await _links(connect)
    assert live
    async with await connect() as conn:
        cur = await conn.execute("SELECT link_id FROM links")
        (link_id,) = await cur.fetchone()
        await conn.commit()
    proc = _ops(db_dsn, "class", "revert", str(link_id), "--reason", "the old item is still right")
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["rate"] == {"applied": 1, "reverted": 1, "rate": 1.0, "days": 30} and res["demoted"] is True
    [(_rel, _s, _d, _v, _p, live)] = await _links(connect)
    assert not live  # ended in transaction time, never deleted
    async with await connect() as conn:
        out = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": old.version_id, "token_budget": 4000},
            deps=read_deps,
        )
        assert out["superseded_by"] == []  # readers no longer flag the older item
        assert await classes.grant(conn, world.main_id) == (False, "D-286")  # auto-demotion
        assert await classes.pair_reverted(conn, new.logical_id, old.logical_id)
        cur = await conn.execute(
            "SELECT payload->'resolved'->'set_class', device_id FROM events"
            " WHERE payload->'request'->>'op' = 'class_revert'"
        )
        demotion, device = await cur.fetchone()
        await conn.commit()
    assert demotion["on"] is False and demotion["reason"] == "auto-demotion" and device == 1
    # a second revert of the same link is refused
    proc = _ops(db_dsn, "class", "revert", str(link_id), "--reason", "again")
    assert proc.returncode != 0
    # applied lists it, marked reverted
    proc = _ops(db_dsn, "class", "applied", "--project", MAIN, "--json")
    assert proc.returncode == 0, proc.stderr
    [row] = json.loads(proc.stdout)["applied"]
    assert row["link_id"] == link_id and row["reverted"] is True and row["revert_event_id"] is not None
    # a new decision re-grants; the reverted pair is still never applied again (rule 8)
    await _grant(connect, world, decision="D-289")
    await _enqueue_review(connect, world, new, "again")
    await _drain(db_dsn, connect, c1_oracle())
    assert await _c1_links(connect) == 1 and [ln[5] for ln in await _links(connect)] == [False]
    assert [s for _k, s, _c, _p in await _questions(connect)] == ["open"]  # asked instead
    await _replay_identical(connect)


async def test_one_revert_in_many_does_not_demote(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """The rate is reverted ÷ applied over 30 days: 1 of 21 (.048) keeps the grant."""
    await _grant(connect, world)
    await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, c1_oracle())
    async with await connect() as conn:  # 20 more (synthetic) C1 links of the project in the window
        cur = await conn.execute("SELECT link_id FROM links")
        (link_id,) = await cur.fetchone()
        for k in range(20):
            await conn.execute(
                "INSERT INTO links (link_id, project_id, project_ids, device_scope, src_logical_id,"
                " dst_logical_id,"
                " rel, props, valid_from, recorded_at, source_event_id) OVERRIDING SYSTEM VALUE"
                " SELECT 9000 + %s, project_id, project_ids, device_scope, src_logical_id, %s, rel, props,"
                " valid_from, recorded_at, source_event_id FROM links WHERE link_id = %s",
                (k, 9000 + k, link_id),
            )
        res = await classes.revert(conn, link_id, by=world.ctx_admin, reason="test")
        await conn.commit()
    assert res["rate"]["applied"] == 21 and res["rate"]["reverted"] == 1 and res["demoted"] is False
    async with await connect() as conn:
        assert (await classes.grant(conn, world.main_id))[0] is True
        await conn.commit()
