"""W2c gates: questions + ``memory.answer`` (PHASE2-4-ROADMAP W2c, CC-3).

G-Q1 lifecycle + replay: accept / reject / custom (re-plan) / expiry, idempotent answers, every
answer stored as a ``librarian-rule`` fact, the ``librarian`` block of ``memory.query``.
G-Q2 authorization matrix: write on only one of two subject projects → ``E_FORBIDDEN_PROJECT``;
an unreadable subject → ``E_NOT_FOUND``; accepting never widens grants.
G-Q3: a stale subject → ``superseded`` and no mutation.
D-074: in the OBSERVER role an accept is a label (``accepted_pending``) and changes no user item,
link, validity or scope; a promotion applies it through the batch path.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import query
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.questions import answer, expire_due
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, dump_w2b, embed, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
OLD = ("Deploy host", "Production runs on the Hetzner CX33 host in Falkenstein.")
NEW = ("Deploy host moved", "Production moved to the Hostinger KVM 2 host; the Hetzner host is gone.")
CONTRA = {(NEW[0], OLD[0]): ("contradicts", "new", "high")}


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _device(connect, world: World, name: str, grants: dict[int, str]) -> AuthContext:  # noqa: ANN001
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
                (did, pid, role),
            )
        await conn.commit()
    return AuthContext(
        device_id=did,
        device_class="personal",
        is_admin=False,
        token_generation=1,
        grants={pid: Role(role) for pid, role in grants.items()},
        client="pytest/0",
    )


async def _propose(db_dsn, connect, world: World, embedder, *, other_old: bool = False, oracle=None):  # noqa: ANN001, ANN202
    """OLD then NEW (MAIN; OLD in OTHER when ``other_old``), embedded, reviewed as observer."""
    old_project = OTHER if other_old else MAIN
    kind = "fact"
    (old,) = await write_items(
        connect, world.ctx_a, old_project, [{**item(*OLD, valid_from=D_OLD), "kind": kind}]
    )
    (new,) = await write_items(connect, world.ctx_a, MAIN, [{**item(*NEW, valid_from=D_NEW), "kind": kind}])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=oracle or Oracle(relations=CONTRA))
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id::text, kind FROM librarian_questions WHERE status = 'open'"
            " ORDER BY created_at"
        )
        rows = await cur.fetchall()
    return old, new, rows


def _args(qid: str, decision: str, **kw: Any) -> dict[str, Any]:
    return {"project": MAIN, "request_id": str(uuid.uuid4()), "question_id": qid, "decision": decision, **kw}


async def _answer(connect, ctx: AuthContext, args: dict[str, Any], role: str | None = None) -> dict[str, Any]:  # noqa: ANN001
    """``role``: the api's configured librarian role (default: this process's, i.e. observer)."""
    async with await connect() as conn:
        res = await answer(conn, ctx, args, raw=args, configured_role=role)
        await conn.commit()
    return res


async def _promote(connect, world: World, role: str = "assistant") -> dict[str, Any]:  # noqa: ANN001
    """The owner's set_role decision; returns its recorded ``resolved``."""
    from hlmemo.librarian.roles import record_role_decision

    async with await connect() as conn:
        event_id = await record_role_decision(conn, role=role, decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
        cur = await conn.execute("SELECT payload->'resolved' FROM events WHERE event_id = %s", (event_id,))
        return (await cur.fetchone())[0]


async def _user_state(connect, world: World) -> dict[str, list[Any]]:  # noqa: ANN001
    """Every user-visible row the librarian could touch: item versions of the user projects (with
    validity, supersession and scope) and every link."""
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT version_id, logical_id, valid_from::text, valid_to::text, superseded_at::text,"
            " project_ids, device_scope FROM memory_versions WHERE project_id IN (%s, %s)"
            " ORDER BY version_id",
            (world.main_id, world.other_id),
        )
        versions = await cur.fetchall()
        cur = await conn.execute(
            "SELECT link_id, rel, superseded_at::text, valid_to::text FROM links ORDER BY link_id"
        )
        links = await cur.fetchall()
        await conn.rollback()
    return {"versions": versions, "links": links}


async def _err(connect, ctx: AuthContext, args: dict[str, Any]) -> ToolError:  # noqa: ANN001
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await answer(conn, ctx, args, raw=args)
        await conn.rollback()
    return ei.value


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_w2b(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_w2b(conn)
        for table in before:
            assert sorted(set(before[table]) ^ set(after[table])) == [], table
        assert after == before


# --------------------------------------------------------------------------- G-Q1
async def test_d074_observer_accept_is_a_label_then_applies_after_promotion(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    """D-074: an accept while the librarian is OBSERVER records the answer (event + status
    ``accepted_pending`` + rule) and changes NO user item, link, validity or scope. A promotion
    to assistant enqueues the batch path, which applies it with the full recheck; replay identical."""
    old, new, [(qid, kind)] = await _propose(db_dsn, connect, world, embedder)
    assert kind == "contradiction"
    async with await connect() as conn:  # the query shows the pending question (query/2)
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "production host", "token_budget": 2000},
            deps=read_deps,
        )
        await conn.commit()
    block = res["librarian"]
    assert block["pending_questions"] == 1 and block["notices"][0]["question_id"] == qid
    n, o = f"v{new.version_id}", f"v{old.version_id}"
    assert block["notices"][0]["text"] == f"contradiction: {n} vs {o}; proposed: {n} supersedes {o}"
    before = await _user_state(connect, world)
    args = _args(qid, "accept", note="Yes, we moved in September.")
    ack = await _answer(connect, world.ctx_a, args)  # the process default: observer
    assert ack["status"] == "accepted_pending" and ack["role"] == "observer"
    assert ack["applied"] == {"links": 0, "closed": [], "widened": []}
    assert ack["rule"] is not None and ack["replayed"] is False and ack["budget"]["used"] <= 2000
    assert await _user_state(connect, world) == before  # ZERO user mutations
    async with await connect() as conn:
        cur = await conn.execute("SELECT status, answer->>'decision', decided_by FROM librarian_questions")
        assert await cur.fetchall() == [("accepted_pending", "accept", world.dev_a)]
        assert await count(conn, "links") == 0
        assert await count(conn, "memory_versions", "superseded_at <> 'infinity'") == 0
        cur = await conn.execute(
            "SELECT body FROM memory_versions mv JOIN projects p ON p.project_id = mv.project_id"
            " WHERE p.slug = 'hlm-librarian' AND 'librarian-rule' = ANY(mv.tags)"
        )
        (rule,) = await cur.fetchone()
        assert rule.startswith("Owner accepted a contradiction proposal (contradicts, supersedes new)")
        assert f"Refs: v{new.version_id}, v{old.version_id}" in rule
        assert "September" not in rule  # a free-text note never enters working memory (Sol 44 #1)
        cur = await conn.execute("SELECT answer->>'note' FROM librarian_questions")
        assert await cur.fetchone() == ("Yes, we moved in September.",)
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "production host", "token_budget": 2000},
            deps=read_deps,
        )
        await conn.commit()
    assert "librarian" not in res  # nothing open any more
    again = await _answer(connect, world.ctx_a, args)  # idempotent: the stored ack
    assert again == {**ack, "replayed": True, "budget": again["budget"]}
    err = await _err(connect, world.ctx_a, _args(qid, "reject"))
    assert err.code == "E_VERSION_CONFLICT" and err.details["status"] == "accepted_pending"
    # an observer worker run changes nothing either (no job is even queued yet)
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()
    assert await _user_state(connect, world) == before
    # promotion: the set_role event releases it to the batch path
    resolved = await _promote(connect, world)
    assert [j["payload"]["op"] for j in resolved["jobs"]] == ["apply_batch"]
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("applied",)]
        cur = await conn.execute("SELECT rel FROM links ORDER BY link_id")
        assert [r[0] for r in await cur.fetchall()] == ["contradicts", "supersedes"]
        cur = await conn.execute(
            "SELECT count(*) FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'"
            " AND valid_to <> 'infinity'",
            (old.logical_id,),
        )
        assert await cur.fetchone() == (1,)  # OLD closed once, by the batch path
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_d074_observer_accept_then_promotion_rechecks_staleness(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """An accepted_pending answer whose subject changed before the promotion is superseded by the
    batch path's recheck: nothing is applied."""
    old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    ack = await _answer(connect, world.ctx_a, _args(qid, "accept"))
    assert ack["status"] == "accepted_pending"
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [item(OLD[0], OLD[1] + " (verified)", logical_id=old.logical_id, expected_version_id=old.version_id)],
    )
    await _promote(connect, world)
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions WHERE question_id = %s", (qid,))
        assert await cur.fetchone() == ("superseded",)
        assert await count(conn, "links") == 0
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_gq1_accept_applies_directly_in_assistant(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """Promoted (decision + configured assistant): memory.answer applies as the answering device."""
    old, new, [(qid, _kind)] = await _propose(db_dsn, connect, world, embedder)
    await _promote(connect, world)
    args = _args(qid, "accept", note="Yes, we moved in September.")
    ack = await _answer(connect, world.ctx_a, args, role="assistant")
    assert ack["status"] == "applied" and ack["applied"]["links"] == 2 and len(ack["applied"]["closed"]) == 1
    assert ack["role"] == "assistant"
    async with await connect() as conn:
        cur = await conn.execute("SELECT status, answer->>'decision', decided_by FROM librarian_questions")
        assert await cur.fetchall() == [("applied", "accept", world.dev_a)]
        cur = await conn.execute("SELECT rel FROM links ORDER BY link_id")
        assert [r[0] for r in await cur.fetchall()] == ["contradicts", "supersedes"]
    again = await _answer(connect, world.ctx_a, args, role="assistant")  # idempotent: the stored ack
    assert again == {**ack, "replayed": True, "budget": again["budget"]}
    assert (await _err(connect, world.ctx_a, {**args, "decision": "reject"})).code == "E_REQUEST_ID_CONFLICT"
    err = await _err(connect, world.ctx_a, _args(qid, "reject"))
    assert err.code == "E_VERSION_CONFLICT" and err.details["status"] == "applied"
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_gq1_reject_and_custom_replan(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    extra = ("Deploy region", "Production traffic is served from the Vilnius region only.")
    await write_items(connect, world.ctx_a, MAIN, [item(*extra, valid_from=D_OLD)])
    oracle = Oracle(relations={**CONTRA, (NEW[0], extra[0]): ("refines", "none", "med")})
    old, new, rows = await _propose(db_dsn, connect, world, embedder, oracle=oracle)
    by_kind = {k: q for q, k in rows}
    assert set(by_kind) == {"contradiction", "link"}
    ack = await _answer(connect, world.ctx_a, _args(by_kind["link"], "reject"))
    assert ack["status"] == "rejected" and ack["applied"] == {"links": 0, "closed": [], "widened": []}
    ack = await _answer(
        connect, world.ctx_a, _args(by_kind["contradiction"], "custom", note="Both hosts exist.")
    )
    assert ack["status"] == "answered"
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT dedupe_key, priority, payload->>'replan_of' FROM jobs"
            " WHERE dedupe_key LIKE 'librarian_replan:%'"
        )
        assert await cur.fetchall() == [
            (f"librarian_replan:{by_kind['contradiction']}", 4, by_kind["contradiction"])
        ]
        assert await count(conn, "links") == 0
    # the re-plan job runs the review again; its working memory holds the owner's rule
    llm = ScriptedLLM(default=Oracle())
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()
    await provider.aclose()
    replan = [r for r in llm.requests if "Both hosts exist." in r["messages"][1]["content"]]
    assert replan, "the owner's note reaches the re-plan job of this project"
    assert all(
        "Owner note for this re-check: Both hosts exist." in r["messages"][1]["content"] for r in replan
    )
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_gq1_expiry_is_recorded_and_replayed(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    _old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        # an event-made question whose 30 days have passed (the recorded expires_at is moved back
        # in BOTH the projection and its source event, so replay rebuilds the same row)
        await conn.execute(
            "UPDATE librarian_questions SET expires_at = now() - interval '1 second' WHERE question_id = %s",
            (qid,),
        )
        await conn.execute(
            """
            UPDATE events SET payload = jsonb_set(payload, '{resolved,questions,0,expires_at}',
                   to_jsonb(to_char((now() - interval '1 second') AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')))
             WHERE kind = 'librarian' AND payload->'resolved'->'questions'->0->>'question_id' = %s
            """,
            (qid,),
        )
        await conn.commit()
    err = await _err(connect, world.ctx_a, _args(qid, "accept"))  # past 30 days: not answerable
    assert err.code == "E_VERSION_CONFLICT" and err.details["status"] == "expired"
    async with await connect() as conn:
        async with conn.transaction():
            assert await expire_due(conn) == 1
        await conn.commit()
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("expired",)]
        cur = await conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'librarian' AND payload->'request'->>'op' = 'expire'"
        )
        assert await cur.fetchone() == (1,)
    await embed(connect, embedder)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- G-Q2
async def test_gq2_authorization_matrix(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """A cross-project question (subject in MAIN, candidate in OTHER)."""
    old, new, [(qid, kind)] = await _propose(db_dsn, connect, world, embedder, other_old=True)
    assert kind == "contradiction"
    write_main_read_other = await _device(
        connect, world, "dev-c", {world.main_id: "write", world.other_id: "read"}
    )
    write_main_only = await _device(connect, world, "dev-d", {world.main_id: "write"})
    read_main = await _device(connect, world, "dev-e", {world.main_id: "read", world.other_id: "write"})
    # write on only one of the two subject projects -> E_FORBIDDEN_PROJECT
    assert (await _err(connect, write_main_read_other, _args(qid, "accept"))).code == "E_FORBIDDEN_PROJECT"
    # an unreadable subject (OTHER, no grant) -> E_NOT_FOUND (indistinguishable from unknown)
    assert (await _err(connect, write_main_only, _args(qid, "accept"))).code == "E_NOT_FOUND"
    assert (await _err(connect, write_main_only, _args(str(uuid.uuid4()), "accept"))).code == "E_NOT_FOUND"
    # no write on the question's project -> E_FORBIDDEN_PROJECT; dev-b has no MAIN grant at all
    assert (await _err(connect, read_main, _args(qid, "accept"))).code == "E_FORBIDDEN_PROJECT"
    assert (await _err(connect, world.ctx_b, _args(qid, "accept"))).code == "E_FORBIDDEN_PROJECT"
    async with await connect() as conn:
        assert await count(conn, "links") == 0 and await count(conn, "events", "kind = 'answer'") == 0
        cur = await conn.execute("SELECT count(*) FROM device_project_grants")
        (grants_before,) = await cur.fetchone()
    await _promote(connect, world)
    ack = await _answer(connect, world.ctx_a, _args(qid, "accept"), role="assistant")  # write on both
    assert ack["status"] == "applied"
    async with await connect() as conn:  # accepting never widens grants
        cur = await conn.execute("SELECT count(*) FROM device_project_grants")
        assert await cur.fetchone() == (grants_before,)
        cur = await conn.execute("SELECT DISTINCT unnest(project_ids) FROM links ORDER BY 1")
        assert [r[0] for r in await cur.fetchall()] == [world.main_id]


async def test_gq2_widen_scope_accept_needs_write_on_both(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    lesson_o = ("Heredoc over ssh", "Never pipe a heredoc into ssh with bash -s: stdin is swallowed.")
    lesson_m = ("ssh stdin heredoc", "bash -s over ssh with a heredoc swallows stdin; do not do it.")
    (o,) = await write_items(
        connect, world.ctx_a, OTHER, [{**item(*lesson_o, valid_from=D_OLD), "kind": "lesson"}]
    )
    await write_items(connect, world.ctx_a, MAIN, [{**item(*lesson_m), "kind": "lesson"}])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=Oracle(relations={(lesson_m[0], lesson_o[0]): ("refines", "none", "high")}))
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id::text FROM librarian_questions WHERE kind = 'widen_scope'"
        )
        (qid,) = await cur.fetchone()
    only_main = await _device(connect, world, "dev-f", {world.main_id: "write", world.other_id: "read"})
    assert (await _err(connect, only_main, _args(qid, "accept"))).code == "E_FORBIDDEN_PROJECT"
    before = await _user_state(connect, world)
    ack = await _answer(connect, world.ctx_a, _args(qid, "accept"))  # observer: a label, no widening
    assert ack["status"] == "accepted_pending" and ack["applied"]["widened"] == []
    assert await _user_state(connect, world) == before
    await _promote(connect, world)  # the batch path never widens (D-058): a writer on both answers
    ack = await _answer(connect, world.ctx_a, _args(qid, "accept"), role="assistant")
    assert ack["status"] == "applied" and len(ack["applied"]["widened"]) == 1
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT project_id, project_ids, body FROM memory_versions WHERE logical_id = %s"
            " AND superseded_at = 'infinity'",
            (o.logical_id,),
        )
        ((home, pids, body),) = await cur.fetchall()
        assert home == world.other_id and sorted(pids) == sorted([world.other_id, world.main_id])
        assert body == lesson_o[1]  # the content is unchanged; only its visibility widened
    await embed(connect, embedder)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- G-Q3
async def test_gq3_stale_subject_is_superseded_without_mutation(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    old, new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:  # the candidate is revised after the proposal
        from hlmemo.core.write_service import default_deps, write

        await write(
            conn,
            world.ctx_a,
            {
                "project": MAIN,
                "request_id": str(uuid.uuid4()),
                "client": "pytest/0",
                "items": [
                    item(
                        OLD[0],
                        OLD[1] + " (verified)",
                        logical_id=old.logical_id,
                        expected_version_id=old.version_id,
                    )
                ],
            },
            deps=default_deps(),
        )
        await conn.commit()
        user_rows = "project_id IN (%s, %s)"
        before = await count(conn, "memory_versions", user_rows, (world.main_id, world.other_id))
    await _promote(connect, world)
    ack = await _answer(connect, world.ctx_a, _args(qid, "accept"), role="assistant")
    assert ack["status"] == "superseded" and ack["applied"] == {"links": 0, "closed": [], "widened": []}
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        assert await count(conn, "memory_versions", user_rows, (world.main_id, world.other_id)) == before
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("superseded",)]
    await embed(connect, embedder)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- Sol 44 regressions
async def test_sol44_expired_questions_cannot_be_approved(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import record_batch_decision

    _old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute(
            "UPDATE librarian_questions SET expires_at = now() - interval '1 second'"
            " WHERE question_id = %s RETURNING batch_id::text",
            (qid,),
        )
        (batch,) = await cur.fetchone()
        await conn.commit()
        with pytest.raises(ToolError) as ei:  # the only open question is past its 30 days
            await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.rollback()
        assert ei.value.code == "E_NOT_FOUND"


async def test_sol44_approved_widen_expires_too(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import record_batch_decision

    lesson_o = ("Heredoc over ssh", "Never pipe a heredoc into ssh with bash -s: stdin is swallowed.")
    lesson_m = ("ssh stdin heredoc", "bash -s over ssh with a heredoc swallows stdin; do not do it.")
    await write_items(connect, world.ctx_a, OTHER, [{**item(*lesson_o, valid_from=D_OLD), "kind": "lesson"}])
    await write_items(connect, world.ctx_a, MAIN, [{**item(*lesson_m), "kind": "lesson"}])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=Oracle(relations={(lesson_m[0], lesson_o[0]): ("duplicate", "none", "high")}))
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT question_id::text, batch_id::text FROM librarian_questions")
        ((qid, batch),) = await cur.fetchall()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.execute(
            "UPDATE librarian_questions SET expires_at = now() - interval '1 second' WHERE question_id = %s",
            (qid,),
        )
        await conn.commit()
    err = await _err(connect, world.ctx_a, _args(qid, "accept"))
    assert err.code == "E_VERSION_CONFLICT" and err.details["status"] == "expired"


async def test_sol44_conflicting_batch_questions(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """Two approved proposals both close the same item: the first applies, the second is
    superseded, the job completes (it used to fail at apply and roll the whole batch back)."""
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision

    s2 = ("Deploy host moved again", "Production later moved to a second Hostinger VPS in Vilnius.")
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D_OLD)])
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [item(*NEW, valid_from=D_NEW), item(*s2, valid_from=datetime(2026, 7, 1, tzinfo=UTC).isoformat())],
    )
    await embed(connect, embedder)
    rel = {**CONTRA, (s2[0], OLD[0]): ("contradicts", "new", "high")}
    llm = ScriptedLLM(default=Oracle(relations=rel))
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT DISTINCT batch_id::text FROM librarian_questions WHERE status = 'open'"
        )
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status, count(*) FROM librarian_questions GROUP BY 1 ORDER BY 1")
        assert await cur.fetchall() == [("applied", 1), ("superseded", 1)]
        cur = await conn.execute(
            "SELECT count(*) FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'"
            " AND valid_to <> 'infinity'",
            (old.logical_id,),
        )
        assert await cur.fetchone() == (1,)  # closed exactly once
        assert await count(conn, "jobs", "kind = 'librarian_write' AND status <> 'done'") == 0
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_sol44_role_decision_waits_for_an_apply_in_flight(connect, world: World) -> None:  # noqa: ANN001
    """An apply holds the role-order lock SHARED; a demotion needs it EXCLUSIVE and waits."""
    import psycopg

    from hlmemo.librarian.roles import lock_role_order, record_role_decision

    async with await connect() as apply_conn:
        await lock_role_order(apply_conn, exclusive=False)  # a worker apply in flight
        async with await connect() as conn:
            await conn.execute("SET lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                await record_role_decision(
                    conn, role="observer", decided_by=world.ctx_admin, decision="D-stop"
                )
            await conn.rollback()
        await apply_conn.rollback()
    async with await connect() as conn:  # once the apply committed, the demotion goes through
        await record_role_decision(conn, role="observer", decided_by=world.ctx_admin, decision="D-stop")
        await conn.commit()


async def test_sol44_observer_hands_approvals_back(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import record_batch_decision

    _old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    llm = ScriptedLLM(default=Oracle())
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()  # observer: role_denied
    await provider.aclose()
    async with await connect() as conn:
        assert await count(conn, "links") == 0
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("open",)]  # can be decided again after a promotion
        cur = await conn.execute("SELECT status FROM librarian_batches")
        assert await cur.fetchall() == [("ready",)]
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_sol46_handed_back_approvals_can_be_decided_again(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """After an observer hand-back the SAME owner approves again (round 1: its own answer request
    ids and apply job key) and, once promoted, the approval really applies (Sol 46 #7)."""
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision

    old, _new, _rows = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    await make_worker(lib_settings(db_dsn), provider, connect).drain()  # observer: handed back
    async with await connect() as conn:
        again = await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        assert again["accepted"] == 1
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("approved",)]
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("applied",)]
        cur = await conn.execute(
            "SELECT dedupe_key FROM jobs WHERE dedupe_key LIKE 'librarian_apply:%%' ORDER BY job_id"
        )
        keys = [r[0] for r in await cur.fetchall()]
        assert keys == [f"librarian_apply:{batch}", f"librarian_apply:{batch}:r1"]
        cur = await conn.execute(
            "SELECT count(*) FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'"
            " AND valid_to <> 'infinity'",
            (old.logical_id,),
        )
        assert await cur.fetchone() == (1,)  # the approval applied: OLD closed
        cur = await conn.execute("SELECT status FROM librarian_batches")
        assert await cur.fetchall() == [("applied",)]
    await embed(connect, embedder)
    await _replay_identical(connect)


async def test_sol46_approved_question_past_ttl_expires_at_apply(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """The TTL bounds every not-yet-applied proposal: an approved question whose expires_at passed
    before its apply job ran is ``expired`` and nothing is applied (Sol 46 #2)."""
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision

    _old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.execute(
            "UPDATE librarian_questions SET expires_at = now() - interval '1 second' WHERE question_id = %s",
            (qid,),
        )
        await conn.commit()
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("expired",)]
        assert await count(conn, "links") == 0
        assert await count(conn, "memory_versions", "superseded_at <> 'infinity'") == 0
        cur = await conn.execute(
            "SELECT payload->'resolved'->'question_status' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'apply_batch'"
        )
        assert await cur.fetchone() == ([{"question_id": qid, "status": "expired"}],)


async def test_sol47_ttl_is_checked_after_the_lock_wait(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """An approved question that expires WHILE the apply waits for its locks is ``expired``: the
    TTL is compared with the clock read after the waits (Sol 47 #2)."""
    import asyncio

    from hlmemo.auth.resolve import lock_device_access
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision

    _old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    async with await connect() as revoke:
        await lock_device_access(revoke, world.dev_a, exclusive=True)  # a revocation in flight
        async with await connect() as conn:
            await conn.execute(
                "UPDATE librarian_questions SET expires_at = clock_timestamp() + interval '1 second'"
                " WHERE question_id = %s",
                (qid,),
            )
            await conn.commit()
        applying = asyncio.create_task(worker.drain())
        await _lock_waiters(connect, 1)  # the apply waits for the device lock
        await asyncio.sleep(1.5)  # the question expires during the wait
        await revoke.rollback()
        assert await asyncio.wait_for(applying, 30) == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("expired",)]
        assert await count(conn, "links") == 0


async def test_sol48_ttl_is_rechecked_after_the_item_lock_wait(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """The LAST lock wait of an apply is the item lock: an approved question that expires while
    a request holds a subject's item lock is ``expired`` (fresh clock after that wait, Sol 48)."""
    import asyncio

    from hlmemo.librarian.roles import record_batch_decision

    old, _new, [(qid, _)] = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    await _promote(connect, world)
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    async with await connect() as request:
        await request.execute(
            "SELECT pg_advisory_xact_lock(%s::bigint)", (old.logical_id,)
        )  # a write in flight
        async with await connect() as conn:
            await conn.execute(
                "UPDATE librarian_questions SET expires_at = clock_timestamp() + interval '1 second'"
                " WHERE question_id = %s",
                (qid,),
            )
            await conn.commit()
        applying = asyncio.create_task(worker.drain())
        await _lock_waiters(connect, 1)  # the apply passed the device + question locks, waits on the item
        await asyncio.sleep(1.5)  # the question expires during the wait
        await request.rollback()
        assert await asyncio.wait_for(applying, 30) == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("expired",)]
        assert await count(conn, "links") == 0
        assert await count(conn, "memory_versions", "superseded_at <> 'infinity'") == 0
    # (no replay identity here: expires_at was moved in the projection only, as a test shortcut)


async def _lock_waiters(connect, n: int) -> None:  # noqa: ANN001
    """Wait until ``n`` advisory-lock requests of THIS database are queued."""
    import asyncio

    for _ in range(200):
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
            (k,) = await cur.fetchone()
            await conn.rollback()
        if k >= n:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {n} queued advisory locks")


async def test_sol46_apply_batch_takes_the_device_lock_before_item_locks(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """Lock order (Sol 46): a request of the proposing device is in flight (device lock held, item
    lock not yet) and a revocation is queued behind it. The apply queues behind the revocation
    WITHOUT holding any item lock, so the request gets its item lock at once and everything
    completes. With items first, the apply would hold the item and wait behind the revocation,
    which waits for the request, which waits for the item: a (soft) deadlock that stalls the
    request until the deadlock detector reorders the queue (deadlock_timeout, 1 s) — the
    request's 500 ms lock_timeout fails that order (verified by a mutant)."""
    import asyncio

    from hlmemo.auth.resolve import lock_device_access
    from hlmemo.librarian.roles import record_batch_decision, record_role_decision

    old, _new, _rows = await _propose(db_dsn, connect, world, embedder)
    async with await connect() as conn:
        cur = await conn.execute("SELECT batch_id::text FROM librarian_questions")
        (batch,) = await cur.fetchone()
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    async with await connect() as request, await connect() as revoke:
        await lock_device_access(request, world.dev_a)  # the request resolved its device
        revoking = asyncio.create_task(lock_device_access(revoke, world.dev_a, exclusive=True))
        await _lock_waiters(connect, 1)
        # let the revocation's one-shot deadlock check (deadlock_timeout 1 s) pass before a cycle
        # could exist, so only the apply's and the request's own checks could reorder the queue
        await asyncio.sleep(1.2)
        applying = asyncio.create_task(worker.drain())
        await _lock_waiters(connect, 2)  # the apply is queued behind the revocation
        # the request's item lock (the write path's key; raw so the 500 ms timeout applies, which
        # is below deadlock_timeout 1 s): free, the apply holds no item lock
        await request.execute("SET lock_timeout = '500ms'")
        await request.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (old.logical_id,))
        await request.commit()
        await asyncio.wait_for(revoking, 10)
        await revoke.rollback()  # not revoked after all
        assert await asyncio.wait_for(applying, 30) == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT status FROM librarian_questions")
        assert await cur.fetchall() == [("applied",)]
    await embed(connect, embedder)
    await _replay_identical(connect)
