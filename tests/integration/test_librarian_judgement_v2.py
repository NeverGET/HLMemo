"""Judgement v2 through the real W2b pipeline (D-076 hold-out failures; scripted Oracle, so these
tests prove the deterministic machinery around the model, never model quality — that is G-LIVE-B):

* fact-level supersession: a multi-fact item whose ONE statement is superseded gets a scoped
  ``supersedes`` link quoting the outdated span and NO close; applied, it never hides the item on
  the read side, it only ranks it after the item that replaced the statement;
* a whole-item supersession of a multi-statement item is proposed with a close, but never
  applied automatically;
* the strict action tier (verifier ``confirm``, quotes, same kind) and strict duplicates;
* the refine direction check (flipped link, counted in the audit);
* doc_chunk items as contradiction candidates and (dated) subjects;
* approve-all staleness chains: dependency order, rebase, re-plan of conflicts and of legacy
  (v1) whole-item closes.
Replay stays identical in every scenario.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.core.read_service import query
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.roles import record_batch_decision, record_role_decision
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, dump_w2b, embed, parse_input, write_items
from tests.integration._write_fixtures import MAIN, World, count, item, seed_world

pytestmark = pytest.mark.integration

D1 = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D2 = datetime(2026, 3, 1, tzinfo=UTC).isoformat()
D3 = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
MULTI = (
    "Cache setup",
    "The API cache TTL is 60 seconds.\n- The cache is stored in Redis 7 on the api host.\n"
    "- Cache keys are prefixed with the tenant id.",
)
TTL = ("Cache TTL changed", "Since June the API cache TTL is 300 seconds.")


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _drain(db_dsn, connect, oracle: Any, role: str = "observer", **kw: Any) -> ScriptedLLM:  # noqa: ANN001
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role=role, **kw), provider, connect).drain()
    await provider.aclose()
    return llm


async def _role(connect, world: World, role: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await record_role_decision(conn, role=role, decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()


async def _approve_all(db_dsn, connect, world: World) -> None:  # noqa: ANN001
    """The owner approves every open batch; an assistant worker applies them."""
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT batch_id::text FROM librarian_questions WHERE status = 'open'"
        )
        for (batch,) in await cur.fetchall():
            await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    await _role(connect, world, "assistant")
    await _drain(db_dsn, connect, Oracle(), role="assistant")


async def _questions(connect) -> list[tuple[str, str, dict[str, Any]]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT kind, status, proposal FROM librarian_questions ORDER BY created_at")
        return [(k, s, p) for k, s, p in await cur.fetchall()]


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
        for table in before:
            assert sorted(set(before[table]) ^ set(after[table])) == [], table
        assert after == before


async def _audit(connect, key: str, op: str = "write_review") -> list[Any]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->%s FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = %s AND payload->'request' ? %s ORDER BY event_id",
            (key, op, key),
        )
        return [r[0] for r in await cur.fetchall()]


# --------------------------------------------------------------------------- fact-level supersession
async def test_partial_supersession_links_the_fact_never_closes_the_item(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*MULTI, valid_from=D1)])
    (new,) = await write_items(connect, world.ctx_a, MAIN, [item(*TTL, valid_from=D3)])
    await embed(connect, embedder)
    rel = {(TTL[0], MULTI[0]): ("contradicts", "new", "high")}
    await _role(connect, world, "autonomous")
    await _drain(
        db_dsn, connect, Oracle(relations=rel, scope={(TTL[0], MULTI[0]): "part"}), role="autonomous"
    )
    [(kind, status, prop)] = await _questions(connect)
    assert (kind, status) == ("contradiction", "open")  # never auto: a fact-level supersession asks
    assert [(a["op"], a.get("rel")) for a in prop["actions"]] == [
        ("link_insert", "contradicts"),
        ("link_insert", "supersedes"),
    ]  # NO version_close of the multi-fact item
    sup = prop["actions"][1]["props"]
    assert sup["scope"] == "part" and sup["quote"].startswith("The API cache TTL is 60 seconds")
    assert prop["scope"] == "part" and prop["close_ok"] is False
    assert prop["resolution"] == "split_and_supersede" and prop["auto_class"] is False
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "API cache TTL", "token_budget": 2000},
            deps=read_deps,
        )
        await conn.commit()
    n, o = f"v{new.version_id}", f"v{old.version_id}"
    assert (
        res["librarian"]["notices"][0]["text"]
        == f"contradiction: {n} vs {o}; proposed: {n} supersedes part of {o}"
    )
    redis_q = {"project": MAIN, "query": "cache stored in Redis on the api host", "token_budget": 3000}
    async with await connect() as conn:  # D-087: the ranking BEFORE the partial link exists
        redis_before = await query(conn, world.ctx_a, redis_q, deps=read_deps)
        await conn.commit()
    await _approve_all(db_dsn, connect, world)
    async with await connect() as conn:
        cur = await conn.execute("SELECT rel, props->>'scope' FROM links ORDER BY link_id")
        assert await cur.fetchall() == [("contradicts", None), ("supersedes", "part")]
        assert await count(conn, "memory_versions", "superseded_at <> 'infinity'") == 0  # nothing closed
        ttl = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "API cache TTL seconds", "token_budget": 3000},
            deps=read_deps,
        )
        redis = await query(conn, world.ctx_a, redis_q, deps=read_deps)
        await conn.commit()
    titles = [h["title"] for h in ttl["hits"]]
    # the query matched the OUTDATED statement: the item stays visible, ranked after its superseder
    assert TTL[0] in titles and MULTI[0] in titles and titles.index(TTL[0]) < titles.index(MULTI[0])
    titles = [h["title"] for h in redis["hits"]]
    # D-087 regression: the query matched a still-valid statement of the multi-fact item, which
    # keeps EXACTLY its rank (and the whole order) once the partial link is applied
    assert titles[0] == MULTI[0]
    assert [h["title"] for h in redis_before["hits"]] == titles
    await _replay_identical(connect)


async def test_whole_multi_statement_close_is_proposed_but_never_automatic(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    new_all = (
        "Cache moved",
        "Since June the API cache TTL is 300 s, the cache lives in Valkey 8, keys carry no prefix.",
    )
    await write_items(connect, world.ctx_a, MAIN, [item(*MULTI, valid_from=D1)])
    await write_items(connect, world.ctx_a, MAIN, [item(*new_all, valid_from=D3)])
    await embed(connect, embedder)
    await _role(connect, world, "autonomous")
    rel = {(new_all[0], MULTI[0]): ("contradicts", "new", "high")}
    await _drain(db_dsn, connect, Oracle(relations=rel), role="autonomous")  # Oracle: scope whole
    [(_kind, status, prop)] = await _questions(connect)
    assert status == "open" and prop["close_ok"] is True and prop["auto_class"] is False
    assert [a["op"] for a in prop["actions"]] == ["link_insert", "link_insert", "version_close"]
    async with await connect() as conn:
        assert (
            await count(conn, "links") == 0
            and await count(conn, "memory_versions", "valid_to <> 'infinity'") == 0
        )
    await _replay_identical(connect)


# --------------------------------------------------------------------------- strict action tier
async def test_strict_action_tier_and_duplicates(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    same = ("Lint on push", "GitHub Actions runs lint and the unit tests on every push to main.")
    copy = ("Lint on push (import)", "GitHub Actions runs lint and the unit tests on every push to main.")
    para = ("CI trigger", "Every push to main triggers GitHub Actions, which runs lint and the unit tests.")
    lesson = ("TTL lesson", "The API cache TTL is 60 seconds.")
    await write_items(connect, world.ctx_a, MAIN, [item(*same, valid_from=D1)])
    await write_items(connect, world.ctx_a, MAIN, [{**item(*lesson, valid_from=D1), "kind": "lesson"}])
    await write_items(connect, world.ctx_a, MAIN, [item(*copy, valid_from=D2), item(*para, valid_from=D2)])
    await write_items(connect, world.ctx_a, MAIN, [item(*TTL, valid_from=D3)])
    await embed(connect, embedder)
    await _role(connect, world, "autonomous")
    dup = ("duplicate", "none", "high")
    rel = {
        (copy[0], same[0]): dup,
        (para[0], same[0]): dup,
        (TTL[0], lesson[0]): ("contradicts", "new", "high"),  # a fact vs a lesson: never automatic
    }
    llm = await _drain(db_dsn, connect, Oracle(relations=rel), role="autonomous")
    confirms = [
        p
        for task, inp in (parse_input(r) for r in llm.requests)
        if task == "relate_verify"
        for p in inp["pairs"]
    ]
    assert confirms  # the action-tier pairs got their second opinion
    async with await connect() as conn:
        cur = await conn.execute("SELECT rel, props FROM links ORDER BY link_id")
        links = await cur.fetchall()
    # the near-identical copy is a duplicate, applied automatically (verified, same kind, quotes)
    assert [(r, p.get("dup"), p["relation"]) for r, p in links] == [("relates_to", True, "duplicate")]
    by_rel = {p["relation"]: (s, p) for _k, s, p in await _questions(connect)}
    status, prop = by_rel["relates"]  # the paraphrase: a restated claim, never a dup flag, asked
    assert status == "open" and prop["tier"] == "question" and not prop["auto_class"]
    assert "duplicate_not_identical" in prop["flags"] and prop["actions"][0]["props"].get("dup") is None
    status, prop = by_rel["contradicts"]
    assert status == "open" and prop["tier"] == "question" and "action_kind" in prop["flags"]
    assert not prop["auto_class"]
    await _replay_identical(connect)


# --------------------------------------------------------------------------- refine direction
async def test_refine_direction_is_checked_and_flips_are_audited(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    specific = ("Backups", "The database is backed up every night at 03:30 and each backup is kept 14 days.")
    general = ("Backup note", "The database is backed up every night.")
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*specific, valid_from=D1)])
    (new,) = await write_items(connect, world.ctx_a, MAIN, [item(*general, valid_from=D3)])
    await embed(connect, embedder)
    rel = {(general[0], specific[0]): ("refines", "none", "med")}  # the model says the NEW one refines
    await _drain(db_dsn, connect, Oracle(relations=rel, refiner={(general[0], specific[0]): "new"}))
    [(_kind, _status, prop)] = await _questions(connect)
    link = prop["actions"][0]
    assert prop["refiner"] == "old" and "refine_direction_flipped" in prop["flags"]
    assert (link["src_logical_id"], link["dst_logical_id"]) == (old.logical_id, new.logical_id)
    assert sum(g.get("refine_flipped", 0) for g in await _audit(connect, "guards")) == 1
    await _replay_identical(connect)


# --------------------------------------------------------------------------- doc_chunk
async def test_doc_chunks_join_the_contradiction_review(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    doc = (
        "Librarian model",
        "The default librarian model is deepseek/deepseek-v4.1-flash, decided on 2026-09-19.",
    )
    plain = ("Architecture overview", "The system has an api, a worker and a librarian process.")
    fact = ("Default model changed", "The default librarian model is now openai/gpt-6-luna.")
    (d,) = await write_items(connect, world.ctx_a, MAIN, [{**item(*doc, valid_from=D1), "kind": "doc_chunk"}])
    (p,) = await write_items(connect, world.ctx_a, MAIN, [{**item(*plain), "kind": "doc_chunk"}])
    (f,) = await write_items(connect, world.ctx_a, MAIN, [item(*fact, valid_from=D3)])
    await embed(connect, embedder)
    rel = {
        (fact[0], doc[0]): ("contradicts", "new", "high"),
        (doc[0], plain[0]): ("duplicate", "none", "high"),
    }
    llm = await _drain(db_dsn, connect, Oracle(relations=rel))
    relate_subjects = [
        inp["new"]["title"] for task, inp in (parse_input(r) for r in llm.requests) if task == "relate"
    ]
    assert plain[0] not in relate_subjects  # an undated chunk gets placement only
    assert doc[0] in relate_subjects and fact[0] in relate_subjects  # the dated chunk is reviewed
    cands: dict[str, list[str]] = {}
    for entries in await _audit(connect, "candidates"):
        for c in entries:
            cands.setdefault(c["subject"], []).append(c["id"])
    assert f"v{d.version_id}" in cands[f"v{f.version_id}"]  # the chunk is the fact's candidate
    [(kind, _status, prop)] = await _questions(connect)  # the chunk-chunk "duplicate" was dropped
    assert kind == "contradiction" and prop["supersedes"] == "new"
    assert sum(g.get("doc_chunk_link_skipped", 0) for g in await _audit(connect, "guards")) == 1
    assert p.version_id
    await _replay_identical(connect)


# --------------------------------------------------------------------------- approve-all chains
async def test_approve_all_staleness_chain_applies_in_dependency_order(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    a = ("Deploy host", "Production runs on the Hetzner CX33 host.")
    b = ("Deploy host moved", "Production runs on the Hostinger KVM 2 host.")
    c = ("Deploy host moved again", "Production runs on the Hostinger KVM 4 host.")
    (va,) = await write_items(connect, world.ctx_a, MAIN, [item(*a, valid_from=D1)])
    (vb,) = await write_items(connect, world.ctx_a, MAIN, [item(*b, valid_from=D2)])
    (vc,) = await write_items(connect, world.ctx_a, MAIN, [item(*c, valid_from=D3)])
    await embed(connect, embedder)
    con = ("contradicts", "new", "high")
    await _drain(db_dsn, connect, Oracle(relations={(b[0], a[0]): con, (c[0], b[0]): con, (c[0], a[0]): con}))
    assert len(await _questions(connect)) == 3
    await _approve_all(db_dsn, connect, world)
    async with await connect() as conn:
        cur = await conn.execute("SELECT status, count(*) FROM librarian_questions GROUP BY 1")
        assert await cur.fetchall() == [("applied", 3)]  # none dropped
        cur = await conn.execute(
            "SELECT logical_id, valid_to FROM memory_versions WHERE superseded_at = 'infinity'"
            " AND valid_to <> 'infinity' ORDER BY logical_id"
        )
        assert await cur.fetchall() == [
            (va.logical_id, datetime(2026, 3, 1, tzinfo=UTC)),  # A at B (the earliest cut)
            (vb.logical_id, datetime(2026, 6, 1, tzinfo=UTC)),  # B at C
        ]
        assert await count(conn, "links", "rel = 'supersedes'") == 3
    [rebased] = await _audit(connect, "rebased", "apply_batch")
    assert len(rebased) == 1  # C supersedes A: its close is redundant after B's, its links apply
    assert vc.version_id
    await embed(connect, embedder)
    await _replay_identical(connect)


async def _clone_question(connect, qid: str, proposal: dict[str, Any]) -> str:  # noqa: ANN001
    """A second open question in the same batch carrying ``proposal``, recorded through a
    ``librarian`` event (``resolved.questions``) so that replay rebuilds it like any question."""
    from hlmemo.core.temporal import fmt_ts
    from hlmemo.db import write_queries as q
    from hlmemo.librarian.actor import insert_questions
    from hlmemo.librarian.events import CLIENT, insert_system_event
    from hlmemo.librarian.reserved import reserved_ids

    new_id = str(uuid.uuid4())
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT job_key, batch_id::text, project_id, project_ids, kind, subject_clues,"
            " subject_version_ids, expires_at FROM librarian_questions WHERE question_id = %s",
            (qid,),
        )
        job_key, batch, pid, pids, kind, clues, vids, expires = await cur.fetchone()
        row = {
            "question_id": new_id,
            "job_key": job_key,
            "batch_id": batch,
            "project_id": pid,
            "project_ids": list(pids),
            "kind": kind,
            "subject_clues": list(clues),
            "subject_version_ids": list(vids),
            "proposal": proposal,
            "status": "open",
            "expires_at": fmt_ts(expires),
        }
        at = await q.clock_now(conn)
        ids = await reserved_ids(conn)
        event_id = await insert_system_event(
            conn,
            kind="librarian",
            project_id=pid,
            device_id=ids.librarian_device_id,
            client=CLIENT,
            request_id=uuid.uuid4(),
            request={"op": "test_inject"},
            resolved={"recorded_at": fmt_ts(at), "questions": [row]},
            at=at,
        )
        await insert_questions(conn, [row], event_id, at)
        await conn.commit()
    return new_id


async def test_inverse_supersession_and_legacy_close_are_replanned(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    a = ("Deploy host", "Production runs on the Hetzner CX33 host.")
    b = ("Deploy host moved", "Production runs on the Hostinger KVM 2 host.")
    await write_items(connect, world.ctx_a, MAIN, [item(*a, valid_from=D1)])
    await write_items(connect, world.ctx_a, MAIN, [item(*b, valid_from=D3)])
    await embed(connect, embedder)
    await _drain(db_dsn, connect, Oracle(relations={(b[0], a[0]): ("contradicts", "new", "high")}))
    async with await connect() as conn:
        cur = await conn.execute("SELECT question_id::text, proposal FROM librarian_questions")
        [(qid, prop)] = await cur.fetchall()
    sup = next(x for x in prop["actions"] if x.get("rel") == "supersedes")
    inverse = {
        **prop,
        "actions": [
            {**sup, "src_logical_id": sup["dst_logical_id"], "dst_logical_id": sup["src_logical_id"]}
        ],
    }
    legacy = {k: v for k, v in prop.items() if k != "close_ok"}  # a v1 question: a close without evidence
    blind = {**legacy, "capabilities": {**prop["capabilities"], "question": []}}  # cannot re-plan
    inv_id = await _clone_question(connect, qid, inverse)
    leg_id = await _clone_question(connect, qid, legacy)
    blind_id = await _clone_question(connect, qid, blind)
    await _approve_all(db_dsn, connect, world)
    async with await connect() as conn:
        cur = await conn.execute("SELECT question_id::text, status FROM librarian_questions")
        got = dict(await cur.fetchall())
        cur = await conn.execute(
            "SELECT payload->'resolved'->'jobs' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'apply_batch'"
        )
        [(jobs,)] = await cur.fetchall()
    # the link-only inverse applies first (dependency order); the real proposal then conflicts
    assert got[inv_id] == "applied" and got[qid] == "superseded" and got[leg_id] == "superseded"
    assert got[blind_id] == "superseded"
    replans = [j for j in jobs if j["dedupe_key"].startswith("librarian_replan:")]
    assert sorted(j["payload"]["replan_of"] for j in replans) == sorted([qid, leg_id])
    assert all(j["payload"]["op"] == "write_review" and j["payload"]["lineage"] for j in replans)
    assert sorted((await _audit(connect, "replanned", "apply_batch"))[0]) == sorted([qid, leg_id, blind_id])
    [refused] = await _audit(connect, "replan_refused", "apply_batch")  # refused with its reason
    assert len(refused) == 1 and refused[0].startswith(f"{blind_id}: capabilities do not cover")
    async with await connect() as conn:
        assert await count(conn, "memory_versions", "valid_to <> 'infinity'") == 0  # no close at all
    await _replay_identical(connect)
