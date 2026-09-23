"""W2b pipeline (PHASE2-4-ROADMAP W2b): trigger, candidates, placement, relation, D-067 guards,
resolution per role, the read-side supersession rule, and replay identity with the new kinds.

The provider is a scripted oracle (``_w2b_fixtures.Oracle``); these tests prove the deterministic
machinery around the model, never model quality (that is G-LIVE-B).
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from hlmemo.core.read_service import query
from hlmemo.core.write_service import default_deps
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.roles import record_role_decision
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, dump_w2b, embed, parse_input, resolved_of, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
OLD = ("Cache TTL", "The API cache TTL is 60 seconds for every endpoint.")
NEW = ("Cache TTL changed", "Since June the API cache TTL is 300 seconds for every endpoint.")
CONTRA = {(NEW[0], OLD[0]): ("contradicts", "new", "high")}


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _role(connect, world: World, role: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await record_role_decision(conn, role=role, decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()


async def _pair(connect, world: World, embedder, *, same_valid: bool = False, importance: int | None = 8):  # noqa: ANN001, ANN202
    (old,) = await write_items(
        connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D_OLD, importance=importance)]
    )
    (new,) = await write_items(
        connect, world.ctx_a, MAIN, [item(*NEW, valid_from=D_OLD if same_valid else D_NEW)]
    )
    await embed(connect, embedder)
    return old, new


async def _drain(db_dsn, connect, oracle: Oracle, role: str = "observer", **kw) -> ScriptedLLM:  # noqa: ANN001
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role=role, **kw), provider, connect)
    await worker.drain()
    await provider.aclose()
    return llm


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
        for table in before:
            diff = sorted(set(before[table]) ^ set(after[table]))
            assert not diff, (table, diff[:4])
        assert after == before


# --------------------------------------------------------------------------- trigger
async def test_write_enqueues_librarian_job_in_the_same_transaction(connect, world: World) -> None:  # noqa: ANN001
    (v,) = await write_items(
        connect, world.ctx_a, MAIN, [item("A", "alpha", importance=4, stability="stable")]
    )
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT e.event_id, j.dedupe_key, j.priority, j.payload, j.source_event_id = e.event_id,"
            " e.payload->'resolved'->'librarian_jobs'->0->>'job_id' = j.job_id::text"
            " FROM jobs j JOIN events e ON e.kind = 'write' WHERE j.kind = 'librarian_write'"
        )
        event_id, key, prio, payload, same_event, recorded = await cur.fetchone()
        assert key == f"librarian_write:{event_id}" and prio == 3 and same_event and recorded
        assert payload["op"] == "write_review" and payload["trigger"] == "write"
        assert payload["versions"] == [
            {"version_id": v.version_id, "kind": "fact", "client_importance": 4, "client_stability": True}
        ]
        assert payload["capabilities"]["trigger_device_id"] == world.dev_a
    # disabled librarian or a project with policy.librarian=off: nothing is enqueued
    await write_items(connect, world.ctx_a, MAIN, [item("B", "beta")], deps=default_deps())
    async with await connect() as conn:
        await conn.execute(
            'UPDATE projects SET policy = \'{"librarian":"off"}\' WHERE project_id = %s', (world.other_id,)
        )
        await conn.commit()
    await write_items(connect, world.ctx_a, OTHER, [item("C", "gamma")])
    async with await connect() as conn:
        assert await count(conn, "jobs", "kind = 'librarian_write'") == 1
    await _replay_identical(connect)


async def test_review_starts_after_the_embed_delay(connect, world: World) -> None:  # noqa: ANN001
    import dataclasses

    from tests.integration._w2b_fixtures import review_deps

    deps = dataclasses.replace(review_deps(), librarian_delay_s=3.0)
    await write_items(connect, world.ctx_a, MAIN, [item("A", "alpha")], deps=deps)
    await write_items(
        connect, world.ctx_a, MAIN, [{**item("S", "session"), "kind": "session_note"}], deps=deps
    )
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT extract(epoch FROM run_after - created_at)::float8 FROM jobs"
            " WHERE kind = 'librarian_write' ORDER BY job_id"
        )
        assert [r[0] for r in await cur.fetchall()] == [3.0, 0.0]  # placement-only jobs do not wait
    await _replay_identical(connect)


async def test_large_batch_is_split_into_jobs(connect, world: World) -> None:  # noqa: ANN001
    await write_items(connect, world.ctx_a, MAIN, [item(f"T{i}", f"text {i}") for i in range(9)])
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT dedupe_key, jsonb_array_length(payload->'versions') FROM jobs"
            " WHERE kind = 'librarian_write' ORDER BY job_id"
        )
        rows = await cur.fetchall()
    assert [n for _, n in rows] == [4, 4, 1]
    assert rows[1][0].endswith(":1") and rows[2][0].endswith(":2")


# --------------------------------------------------------------------------- observer
async def test_observer_writes_signals_and_proposals_only(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    old, new = await _pair(connect, world, embedder)
    oracle = Oracle(relations=CONTRA, placement={OLD[0]: (3, "volatile"), NEW[0]: (7, "stable")})
    llm = await _drain(db_dsn, connect, oracle)
    assert {t for t, _ in oracle.seen} == {"place", "relate", "relate_verify"}
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        assert await count(conn, "memory_versions", "superseded_at <> 'infinity'") == 0  # 0 invalidations
        cur = await conn.execute(
            "SELECT version_id, importance, importance_src, stability_suggested FROM version_signals"
            " ORDER BY 1"
        )
        # the client set importance 8 on OLD: copied as the client's, never replaced by the model's 3
        assert await cur.fetchall() == [
            (old.version_id, 8, "client", "volatile"),
            (new.version_id, 7, "librarian", "stable"),
        ]
        cur = await conn.execute("SELECT kind, status, subject_clues, proposal FROM librarian_questions")
        ((kind, status, clues, proposal),) = await cur.fetchall()
        assert (kind, status) == ("contradiction", "open")
        assert clues == [f"v{new.version_id}", f"v{old.version_id}"]
        assert [a["op"] for a in proposal["actions"]] == ["link_insert", "link_insert", "version_close"]
        assert proposal["auto_class"] is True and proposal["verification"]["agreed"] is True
        cur = await conn.execute("SELECT status FROM librarian_batches")
        assert await cur.fetchall() == [("open",)]
        outcomes = [r["outcome"] for r in await resolved_of(conn)]
        assert sorted(outcomes) == ["annotated", "proposed"]
        cur = await conn.execute(
            "SELECT payload->'request'->'candidates' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' ORDER BY event_id DESC LIMIT 1"
        )
        (cands,) = await cur.fetchone()
        assert cands and cands[0]["id"] == f"v{old.version_id}" and not cands[0]["cross"]
    assert llm.calls >= 4
    await _replay_identical(connect)


# --------------------------------------------------------------------------- autonomous
async def test_autonomous_applies_the_auto_class_close(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    await _role(connect, world, "autonomous")
    old, new = await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, Oracle(relations=CONTRA), role="autonomous")
    async with await connect() as conn:
        cur = await conn.execute("SELECT rel FROM links ORDER BY link_id")
        assert [r[0] for r in await cur.fetchall()] == ["contradicts", "supersedes"]
        cur = await conn.execute(
            "SELECT version_id, nullif(valid_to, 'infinity'), superseded_at = 'infinity' FROM memory_versions"
            " WHERE logical_id = %s ORDER BY version_id",
            (old.logical_id,),
        )
        rows = await cur.fetchall()
        assert rows[0][0] == old.version_id and rows[0][2] is False  # the old row is superseded
        assert rows[1][1] == datetime(2026, 6, 1, tzinfo=UTC) and rows[1][2] is True  # closed copy
        assert await count(conn, "librarian_questions") == 0
        assert (
            await count(
                conn, "jobs", "kind = 'embed' AND (payload->>'version_id')::bigint = %s", (rows[1][0],)
            )
            == 1
        )
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "API cache TTL", "token_budget": 2000},
            deps=read_deps,
        )
        titles = [h["title"] for h in res["hits"]]
        assert NEW[0] in titles and OLD[0] not in titles  # the old value is no longer valid now
        past = await query(
            conn,
            world.ctx_a,
            {
                "project": MAIN,
                "query": "API cache TTL",
                "token_budget": 2000,
                "valid_at": "2026-03-01T00:00:00Z",
            },
            deps=read_deps,
        )
        assert [h["title"] for h in past["hits"]] == [OLD[0]]  # history is kept (bi-temporal)
        await conn.commit()
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_supersedes_link_hides_superseded_hit(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    """Same valid_from: no close is possible, but the applied supersedes link hides the old hit."""
    await _role(connect, world, "autonomous")
    await _pair(connect, world, embedder, same_valid=True)
    await _drain(db_dsn, connect, Oracle(relations=CONTRA), role="autonomous")
    async with await connect() as conn:
        assert await count(conn, "links", "rel = 'supersedes'") == 1
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "API cache TTL", "token_budget": 2000},
            deps=read_deps,
        )
        titles = [h["title"] for h in res["hits"]]
        assert NEW[0] in titles and OLD[0] not in titles
        assert res["contract_version"] == "query/2"


async def test_verifier_disagreement_downgrades(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _role(connect, world, "autonomous")
    await _pair(connect, world, embedder)
    oracle = Oracle(
        relations=CONTRA, verify=lambda a, b: {"same_subject": True, "conflict": True, "current": "unclear"}
    )
    await _drain(db_dsn, connect, oracle, role="autonomous")
    async with await connect() as conn:
        assert await count(conn, "links") == 0  # never applied without agreement
        cur = await conn.execute("SELECT proposal FROM librarian_questions")
        ((prop,),) = await cur.fetchall()
        assert prop["supersedes"] == "none" and [a["rel"] for a in prop["actions"]] == ["contradicts"]
        assert "verifier_direction_disputed" in prop["flags"] and prop["auto_class"] is False


async def test_verifier_rejection_drops_the_proposal(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _pair(connect, world, embedder)
    oracle = Oracle(
        relations=CONTRA, verify=lambda a, b: {"same_subject": False, "conflict": False, "current": "both"}
    )
    await _drain(db_dsn, connect, oracle)
    async with await connect() as conn:
        assert await count(conn, "librarian_questions") == 0
        cur = await conn.execute(
            "SELECT payload->'request'->'judgements' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' ORDER BY event_id DESC LIMIT 1"
        )
        (judged,) = await cur.fetchone()
        assert judged[0]["relation"] == "none" and "verifier_rejected" in judged[0]["flags"]


async def test_unverified_quote_is_never_an_action(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _role(connect, world, "autonomous")
    await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, Oracle(relations=CONTRA, quote_ok=False), role="autonomous")
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        cur = await conn.execute("SELECT status, proposal FROM librarian_questions")
        ((status, prop),) = await cur.fetchall()
        assert status == "open" and "quote_unverified" in prop["flags"] and prop["tier"] == "question"


async def test_uncited_ids_are_dropped(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _pair(connect, world, embedder)
    oracle = Oracle(
        extra_results=[
            {"id": "v999999", "relation": "contradicts", "supersedes": "new", "confidence": "high"}
        ]
    )
    await _drain(db_dsn, connect, oracle)
    async with await connect() as conn:
        assert await count(conn, "librarian_questions") == 0
        cur = await conn.execute(
            "SELECT payload->'request'->'guards'->>'uncited' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' AND payload->'request'->'guards' ? 'uncited'"
        )
        assert [r[0] for r in await cur.fetchall()] == ["1", "1"]


async def test_low_confidence_duplicate_is_an_abstention(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, Oracle(relations={(NEW[0], OLD[0]): ("duplicate", "none", "low")}))
    async with await connect() as conn:
        assert await count(conn, "librarian_questions") == 0


# --------------------------------------------------------------------------- cross-project
async def test_cross_project_duplicate_is_widen_scope_only(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    await _role(connect, world, "autonomous")
    lesson_o = ("Never pipe an ssh heredoc", "Never use bash -s with an ssh heredoc: stdin is swallowed.")
    lesson_m = ("ssh heredoc stdin", "Using bash -s over ssh with a heredoc swallows stdin; avoid it.")
    await write_items(connect, world.ctx_a, OTHER, [{**item(*lesson_o, valid_from=D_OLD), "kind": "lesson"}])
    (m,) = await write_items(connect, world.ctx_a, MAIN, [{**item(*lesson_m), "kind": "lesson"}])
    await embed(connect, embedder)
    oracle = Oracle(relations={(lesson_m[0], lesson_o[0]): ("duplicate", "none", "high")})
    await _drain(db_dsn, connect, oracle, role="autonomous")
    async with await connect() as conn:
        assert await count(conn, "links") == 0  # propose-only in every role (D-058)
        cur = await conn.execute("SELECT kind, status, project_ids, proposal FROM librarian_questions")
        rows = await cur.fetchall()
        assert [(k, s) for k, s, _, _ in rows] == [("widen_scope", "open")]
        _, _, pids, prop = rows[0]
        assert sorted(pids) == sorted([world.main_id, world.other_id])
        assert prop["actions"][0]["op"] == "widen_scope" and prop["actions"][0]["add_project_ids"] == [
            world.main_id
        ]
        assert prop["verification"]["kind"] == "widen" and prop["verification"]["agreed"] is True
        cur = await conn.execute(
            "SELECT payload->'request'->'candidates' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->'subjects' ? %s",
            (f"v{m.version_id}",),
        )
        (cands,) = await cur.fetchone()
        assert [c["cross"] for c in cands] == [True]


async def test_cross_project_candidates_need_a_current_read_grant(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """dev-b writes in OTHER only: MAIN items are never candidates (no grant), whatever the model."""
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [{**item("Main lesson", "Rotate the API keys monthly."), "kind": "lesson"}],
    )
    (o,) = await write_items(
        connect,
        world.ctx_b,
        OTHER,
        [{**item("Other lesson", "Rotate the API keys every month."), "kind": "lesson"}],
    )
    await embed(connect, embedder)
    dup = ("duplicate", "none", "high")
    oracle = Oracle(relations={("Other lesson", "Main lesson"): dup, ("Main lesson", "Other lesson"): dup})
    llm = await _drain(db_dsn, connect, oracle)
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->'candidates' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->'subjects' ? %s",
            (f"v{o.version_id}",),
        )
        assert (await cur.fetchone())[0] == []
        assert await count(conn, "librarian_questions", "kind = 'widen_scope'") == 1  # dev-a's own job
    for req in llm.requests:  # dev-b's job never sent MAIN content
        task, inp = parse_input(req)
        if task == "relate" and inp["new"]["title"] == "Other lesson":
            assert "Rotate the API keys monthly" not in req["messages"][1]["content"]
        if task == "place" and [i["title"] for i in inp["items"]] == ["Other lesson"]:
            assert "Rotate the API keys monthly" not in req["messages"][1]["content"]


# --------------------------------------------------------------------------- embeddings
async def test_job_waits_for_embeddings_without_consuming_attempts(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    await write_items(connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D_OLD)])
    await _drain(db_dsn, connect, Oracle())
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts, last_error FROM jobs WHERE kind = 'librarian_write'"
        )
        assert await cur.fetchone() == ("queued", 0, "E_NOT_READY")
        await conn.execute("UPDATE jobs SET run_after = now() WHERE kind = 'librarian_write'")
        await conn.commit()
    await embed(connect, embedder)
    await _drain(db_dsn, connect, Oracle())
    async with await connect() as conn:
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status = 'done'") == 1
    # a disabled wait (HLM_LIBRARIAN_EMBED_WAIT_S=0) runs lexical-only at once
    (v,) = await write_items(connect, world.ctx_a, MAIN, [item(*NEW, valid_from=D_NEW)])
    await _drain(db_dsn, connect, Oracle(), librarian_embed_wait_s=0)
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->'lexical_only' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' ORDER BY event_id DESC LIMIT 1"
        )
        assert (await cur.fetchone())[0] == [f"v{v.version_id}"]
    await _replay_identical(connect)


async def test_request_ids_are_deterministic(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    old, new = await _pair(connect, world, embedder)
    await _drain(db_dsn, connect, Oracle(relations=CONTRA))
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT request_id, payload->'resolved'->'done'->>'dedupe_key' FROM events"
            " WHERE kind = 'librarian' AND payload->'request'->>'op' = 'write_review'"
        )
        from hlmemo.librarian.events import NS_LIBRARIAN

        for rid, key in await cur.fetchall():
            assert rid == uuid.uuid5(NS_LIBRARIAN, f"job:{key}")


async def test_sol41_rules_with_unreadable_refs_are_not_loaded(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """A librarian-rule referencing a MAIN clue never reaches a job of a device without MAIN."""
    from hlmemo.librarian.memory import write_rule

    (m,) = await write_items(connect, world.ctx_a, MAIN, [item("Main secret title", "main body")])
    async with await connect() as conn:
        await write_rule(
            conn,
            title="r",
            text="Owner rejected a link proposal (duplicate) for vX.",
            clue_refs=[f"v{m.version_id}"],
            dedupe="t1",
        )
        await write_rule(
            conn, title="r2", text="Prefer none for unrelated deploy notes.", clue_refs=[], dedupe="t2"
        )
        await conn.commit()
    await write_items(connect, world.ctx_b, OTHER, [item("Other note", "other body")])
    await embed(connect, embedder)
    llm = await _drain(db_dsn, connect, Oracle())
    for req in llm.requests:
        task, _inp = parse_input(req)
        content = req["messages"][1]["content"]
        if '"Other note"' in content and task == "place":
            assert f"v{m.version_id}" not in content.split("RULES", 1)[-1]
            assert "Prefer none for unrelated deploy notes." in content


async def test_same_pair_from_both_sides_is_one_question(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """Both reviews see the pair (the old item's job ran after the new one was written): only one
    pending question, the second is counted as a duplicate proposal."""
    await _pair(connect, world, embedder)
    both = {**CONTRA, (OLD[0], NEW[0]): ("contradicts", "old", "high")}
    await _drain(db_dsn, connect, Oracle(relations=both))
    async with await connect() as conn:
        assert await count(conn, "librarian_questions") == 1
        cur = await conn.execute(
            "SELECT sum((payload->'request'->>'duplicate_proposals')::int) FROM events"
            " WHERE kind = 'librarian'"
        )
        assert await cur.fetchone() == (1,)
    await _replay_identical(connect)
