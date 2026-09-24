"""e2e 2026-09-24 quick fixes against a real database (docs/status/E2E-PROD-REPORT.md §6/§7):

* #2 a project with ``policy.librarian_cross_project = exclude`` (a disposable/test project) is
  isolated in both directions: the e2e scenario (ONE device with grants on the real project A and
  the test project T) yields no proposal about A derived from T and none about T derived from A,
  while T still reviews itself; risk_check never crosses the boundary either; the policy is set by
  ``python -m hlmemo.ops project policy set``;
* #6 the risk judge sees the matching part of a long lesson (the rule past char 2,400);
* #1 + §7.1 re-importing an auto-memory directory imported by the R2 importer turns the mis-typed
  feedback files into lessons (a kind change is a revision) and splits a multi-rule file.

The provider is a scripted oracle: these tests prove the deterministic machinery, not the model.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.core import risk_service as rs
from hlmemo.core.budget import Meter
from hlmemo.core.write_service import default_deps
from hlmemo.importers import automemory
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.librarian import risk_judge as rj
from hlmemo.librarian.reserved import GLOBAL_PROJECT
from hlmemo.ops import cli as ops_cli
from hlmemo.ops import service as ops
from tests.integration._import_fixtures import FIXTURE, Caller, make_device, make_project, rows
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
    stub_chain,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, embed, parse_input, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, item, seed_world

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
KEY = "librarian_cross_project"


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _policy(connect, slug: str, value: str) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await ops.project_policy_set(conn, slug, KEY, value)
        await conn.commit()
    return out


async def _drain(db_dsn, connect, oracle: Oracle) -> ScriptedLLM:  # noqa: ANN001
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="observer"), provider, connect)
    await worker.drain()
    await provider.aclose()
    return llm


# --------------------------------------------------------------------------- #2 librarian
# A = MAIN (the real project), T = OTHER (the disposable test import); dev-a holds write on both
A_OLD = ("Default librarian model", "The default librarian model is deepseek flash for every job.")
T_NEW = ("Default librarian model changed", "Since June the default librarian model is luna for every job.")
T_OLD = ("Import batch size", "The importer writes 50 items per batch in every run.")
A_NEW = ("Import batch size changed", "Since June the importer writes 20 items per batch in every run.")
T_SELF_OLD = ("T cache TTL", "The test cache TTL is 60 seconds for every endpoint.")
T_SELF_NEW = ("T cache TTL changed", "Since June the test cache TTL is 300 seconds for every endpoint.")
CONTRA = ("contradicts", "new", "high")
RELATIONS = {
    (T_NEW[0], A_OLD[0]): CONTRA,  # T -> A: the e2e leak (close A's item from a T document)
    (A_NEW[0], T_OLD[0]): CONTRA,  # A -> T: the vice-versa direction
    (T_SELF_NEW[0], T_SELF_OLD[0]): CONTRA,  # inside T: must still work
}


@pytest.mark.parametrize("policy", ["include", "exclude"])
async def test_test_project_is_isolated_in_both_directions(
    db_dsn, connect, world: World, embedder, policy: str
) -> None:  # noqa: ANN001
    set_out = await _policy(connect, OTHER, policy)
    assert set_out["policy"][KEY] == policy and set_out["previous"] is None
    (a_old,) = await write_items(connect, world.ctx_a, MAIN, [item(*A_OLD, valid_from=D_OLD)])
    (t_old,) = await write_items(connect, world.ctx_a, OTHER, [item(*T_OLD, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_SELF_OLD, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_NEW, valid_from=D_NEW)])
    await write_items(connect, world.ctx_a, MAIN, [item(*A_NEW, valid_from=D_NEW)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_SELF_NEW, valid_from=D_NEW)])
    await embed(connect, embedder)
    llm = await _drain(db_dsn, connect, Oracle(relations=RELATIONS))

    qs = await rows(connect, "SELECT kind, project_ids, proposal FROM librarian_questions ORDER BY 1")
    mixed = [(k, p) for k, _pids, p in qs if {world.main_id, world.other_id} <= set(_pids)]
    closes = {
        a["logical_id"] for _k, _pids, p in qs for a in p.get("actions", []) if a["op"] == "version_close"
    }
    cands = [
        c
        for (req,) in await rows(
            connect,
            "SELECT payload->'request' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review'",
        )
        for c in req.get("candidates") or []
    ]
    within_t = [p for k, pids, p in qs if k == "contradiction" and sorted(set(pids)) == [world.other_id]]
    assert len(within_t) == 1, qs  # T still reviews itself: T_SELF_NEW supersedes T_SELF_OLD
    if policy == "include":  # the control: without the flag the e2e leak is reproducible
        assert len(mixed) == 2 and a_old.logical_id in closes and t_old.logical_id in closes, qs
        assert any(c["cross"] for c in cands)
        return
    assert mixed == [], mixed  # no proposal about A derived from T, none about T from A
    assert a_old.logical_id not in closes and t_old.logical_id not in closes
    assert not any(c["cross"] for c in cands), cands
    excluded = await rows(
        connect,
        "SELECT DISTINCT payload->'request'->'cross_project_excluded' FROM events WHERE kind = 'librarian'"
        " AND payload->'request'->>'op' = 'write_review'",
    )
    assert excluded == [([world.other_id],)]
    for req in llm.requests:  # neither side's content ever reached the other side's prompt
        task, inp = parse_input(req)
        if task != "relate":
            continue
        shown = {inp["new"]["title"], *(e["title"] for e in inp["existing"])}
        assert not ({A_OLD[0], A_NEW[0]} & shown and {T_OLD[0], T_NEW[0], T_SELF_OLD[0]} & shown), shown


# --------------------------------------------------------------------------- #2 risk_check
async def test_risk_check_never_crosses_an_excluded_project(connect, world: World, read_deps) -> None:  # noqa: ANN001, F811
    deps = default_deps()
    rule_a = (
        "Never skip the backup before a migration",
        "Take a database backup snapshot before a migration.",
    )
    rule_t = ("Backup before migrating", "Always take a database backup snapshot before running a migration.")
    await write_items(connect, world.ctx_a, MAIN, [{**item(*rule_a), "kind": "lesson"}], deps=deps)
    await write_items(connect, world.ctx_a, OTHER, [{**item(*rule_t), "kind": "lesson"}], deps=deps)
    await embed(connect, read_deps.embedder)
    task = "run the database migration now without a backup snapshot"

    async def seen(home: str) -> set[str]:
        async with await connect() as conn:
            pid = world.main_id if home == MAIN else world.other_id
            cands, _ = await rs.candidates(conn, world.ctx_a, pid, task, read_deps)
            await conn.commit()
        return {c.project for c in cands}

    assert await seen(MAIN) == {MAIN, OTHER} and await seen(OTHER) == {MAIN, OTHER}  # default: both
    await _policy(connect, OTHER, "exclude")
    assert await seen(MAIN) == {MAIN}  # T's lessons are no candidates for A
    assert await seen(OTHER) == {OTHER}  # T's own check works, on T's lessons only
    await _policy(connect, OTHER, "include")
    assert await seen(MAIN) == {MAIN, OTHER}


# --------------------------------------------------------------------------- #2 ops
async def test_project_policy_set_is_validated_audited_and_on_the_cli(connect, world: World) -> None:  # noqa: ANN001
    args = ops_cli.build_parser().parse_args(["project", "policy", "set", OTHER, KEY, "exclude"])
    async with await connect() as conn:
        assert await ops_cli._dispatch(conn, args, None) == 0
        await conn.commit()
    async with await connect() as conn:
        assert (await ops.project_policy(conn, OTHER))["policy"] == {KEY: "exclude"}
        again = await ops.project_policy_set(conn, OTHER, KEY, "exclude")
        assert again["changed"] is False and again["previous"] == "exclude"
        for bad in (("librarian", "off"), ("reserved", "false"), (KEY, "maybe")):
            with pytest.raises(HlmError) as err:
                await ops.project_policy_set(conn, OTHER, *bad)
            assert err.value.code == "E_INVALID_ARG", bad
        with pytest.raises(HlmError) as err:
            await ops.project_policy_set(conn, GLOBAL_PROJECT, KEY, "exclude")
        assert err.value.code == "E_FORBIDDEN"
        with pytest.raises(HlmError) as err:
            await ops.project_policy_set(conn, "no-such-project", KEY, "exclude")
        assert err.value.code == "E_NOT_FOUND"
        await conn.rollback()
    with pytest.raises(SystemExit):  # argparse refuses an unknown key before any DB work
        ops_cli.build_parser().parse_args(["project", "policy", "set", OTHER, "librarian", "off"])
    events = await rows(
        connect,
        "SELECT project_id, device_id, schema_version, payload->'request' FROM events"
        " WHERE kind = 'librarian' AND payload->'request'->>'op' = 'set_project_policy'",
    )
    assert len(events) == 1  # the refused and the rolled-back attempts left nothing
    pid, did, sv, req = events[0]
    assert (pid, did, sv) == (world.other_id, 1, 2)
    assert (req["key"], req["value"], req["previous"]) == (KEY, "exclude", None)


# --------------------------------------------------------------------------- #6 judge window
FILLER = [
    f"Paragraph {i}: rotate the log files weekly, archive dashboards monthly and keep the on-call "
    f"rota in the shared calendar so that handovers stay predictable for the team ({i})."
    for i in range(16)
]
RULE = (
    "Never run alembic downgrade on the production database: the 0007 downgrade drops the "
    "source_key index and every import afterwards duplicates items."
)


async def test_risk_judge_sees_the_matching_part_of_a_long_lesson(
    db_dsn, connect, world: World, read_deps
) -> None:  # noqa: ANN001, F811
    title = "Operations handbook lessons"
    body = "\n\n".join([f"# {title}", *FILLER[:14], RULE, *FILLER[14:]]) + "\n"
    assert body.index(RULE) > 2400 and RULE not in rj.lesson_text(title, body)  # the pre-fix view
    await write_items(
        connect, world.ctx_a, MAIN, [{**item(title, body), "kind": "lesson"}], deps=default_deps()
    )
    await embed(connect, read_deps.embedder)
    llm = ScriptedLLM(default={"verdict": "none", "matches": []})
    settings = get_settings(db_dsn=db_dsn, librarian_enabled=True, llm_mode="live", llm_budget_disabled=True)
    judge = rj.RiskJudge(settings, chain=stub_chain(fallback=False), transport=llm.transport)
    task = "run alembic downgrade on the production database to undo the 0007 migration"
    try:
        async with await connect() as conn:
            await conn.commit()  # idle: the judge never runs inside a caller's transaction (D-062)
            out = await rs.risk_check(
                conn,
                world.ctx_a,
                {"project": MAIN, "task": task, "token_budget": 3000},
                deps=read_deps,
                judge=judge,
            )
            await conn.commit()
    finally:
        await judge.aclose()
    assert out["judged"] is True, out
    (req,) = llm.requests
    shown = json.loads(req["messages"][1]["content"].split("INPUT: ", 1)[1])["lessons"]
    text = next(les["text"] for les in shown if les["text"].startswith(title))
    assert RULE in text and FILLER[0] not in text
    assert len(text) <= rj.LESSON_TEXT_CHARS + 2


# --------------------------------------------------------------------------- #1 re-import
async def test_reimport_turns_misparsed_feedback_into_split_lessons(connect, monkeypatch) -> None:  # noqa: ANN001
    """The R2 importer read ``type`` at the top level only: nested feedback files became facts and
    the multi-rule file one item. Re-importing with the fix revises the single-rule file into a
    lesson (a kind change alone is a revision), writes one lesson per rule and closes the old
    whole-file item (nothing deleted)."""
    pid, _ = await make_project(connect, "am")
    ctx = await make_device(connect, "am-importer", {pid: "write"})
    call = Caller(connect, ctx)
    src = [FIXTURE / "automemory"]
    meter = Meter()

    def r2_memory_type(meta: dict[str, Any]) -> str:
        value = meta.get("type")
        return value.strip().lower() if isinstance(value, str) else ""

    monkeypatch.setattr(automemory, "memory_type", r2_memory_type)
    first = await import_async(
        call,
        source="automemory",
        parsed=parse_source("automemory", src, tz=UTC),
        project="am",
        dry_run=False,
        meter=meter,
    )
    assert first["counts"]["new"] == 7 and not first["writes"]["failed"]
    monkeypatch.undo()
    second = await import_async(
        call,
        source="automemory",
        parsed=parse_source("automemory", src, tz=UTC),
        project="am",
        dry_run=False,
        meter=meter,
    )
    counts = second["counts"]
    assert (counts["new"], counts["changed"], counts["unchanged"], counts["closed"]) == (5, 1, 5, 1), counts
    assert [r["key"] for r in second["replaced_by_split"]] == ["automemory:feedback_deploy_footguns.md"]
    assert not second["writes"]["failed"] and second["writes"]["closed"] == 1
    current = await rows(
        connect,
        "SELECT kind, source->>'path' FROM memory_versions WHERE project_id = %s AND source IS NOT NULL"
        " AND superseded_at = 'infinity' AND valid_to = 'infinity' ORDER BY 2",
        (pid,),
    )
    lessons_now = sorted(p for k, p in current if k == "lesson")
    assert lessons_now[:5] == sorted(p for p in lessons_now if p.startswith("feedback_deploy_footguns.md#"))
    assert "feedback_review_style.md" in lessons_now and "feedback_testing.md" in lessons_now
    assert len(lessons_now) == 7 and "feedback_deploy_footguns.md" not in {p for _k, p in current}
    third = await import_async(
        call,
        source="automemory",
        parsed=parse_source("automemory", src, tz=UTC),
        project="am",
        dry_run=False,
        meter=meter,
    )
    assert third["writes"]["written"] == 0 and third["counts"]["unchanged"] == 11  # idempotent


# --------------------------------------------------------------------------- Sol 54 #1 multi-project
M_AT = ("Deploy host", "Production runs on the Hetzner CX33 host in Falkenstein for every service.")
A_SUB = ("Deploy host moved", "Since June production runs on the Hostinger KVM 2 host for every service.")
T_SUB = ("Deploy host moved again", "Since July production runs on a Vilnius VPS for every service.")
A_OLD2 = ("Backup window", "The nightly backup runs at 02:00 on the database host every day.")
AT_SUB = (
    "Backup window moved",
    "Since June the nightly backup runs at 04:00 on the database host every day.",
)
T_OLD2 = ("Backup window test", "The test backup runs at 02:00 on the test database host every day.")
MULTI = {
    (A_SUB[0], M_AT[0]): CONTRA,  # A subject vs a multi-project [A, T] item
    (T_SUB[0], M_AT[0]): CONTRA,  # T subject vs the [A, T] item
    (AT_SUB[0], A_OLD2[0]): CONTRA,  # an [A, T] subject vs a pure A item
    (AT_SUB[0], T_OLD2[0]): CONTRA,  # ... and vs a pure T item
}


@pytest.mark.parametrize("policy", ["include", "exclude"])
async def test_multi_project_items_never_cross_an_excluded_project(
    db_dsn, connect, world: World, embedder, policy: str
) -> None:  # noqa: ANN001
    """Sol 54 #1: an item touching T (here [A, T]) is related with nothing outside T, and a
    subject spanning T and A gets no candidate; ``include`` is the control."""
    await _policy(connect, OTHER, policy)
    both = [MAIN, OTHER]
    (m,) = await write_items(connect, world.ctx_a, MAIN, [item(*M_AT, valid_from=D_OLD, project_ids=both)])
    (a2,) = await write_items(connect, world.ctx_a, MAIN, [item(*A_OLD2, valid_from=D_OLD)])
    (t2,) = await write_items(connect, world.ctx_a, OTHER, [item(*T_OLD2, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, MAIN, [item(*A_SUB, valid_from=D_NEW)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_SUB, valid_from=D_NEW)])
    await write_items(connect, world.ctx_a, MAIN, [item(*AT_SUB, valid_from=D_NEW, project_ids=both)])
    await embed(connect, embedder)
    await _drain(db_dsn, connect, Oracle(relations=MULTI))
    qs = await rows(connect, "SELECT kind, project_ids FROM librarian_questions")
    closes = {
        a["logical_id"]
        for (p,) in await rows(connect, "SELECT proposal FROM librarian_questions")
        for a in p.get("actions", [])
        if a["op"] == "version_close"
    }
    if policy == "include":
        assert len(qs) == 4 and {m.logical_id, a2.logical_id, t2.logical_id} <= closes, qs
        return
    assert qs == [] and closes == set()


async def test_risk_check_multi_project_lesson_both_directions(connect, world: World, read_deps) -> None:  # noqa: ANN001, F811
    deps = default_deps()
    at = ("Backup before migrating", "Always take a database backup snapshot before running a migration.")
    t_only = ("Snapshot first", "Take a database snapshot before any migration on the test stack.")
    await write_items(
        connect, world.ctx_a, MAIN, [{**item(*at, project_ids=[MAIN, OTHER]), "kind": "lesson"}], deps=deps
    )
    await write_items(connect, world.ctx_a, OTHER, [{**item(*t_only), "kind": "lesson"}], deps=deps)
    await embed(connect, read_deps.embedder)
    task = "run the database migration now without a backup snapshot"

    async def titles(home_pid: int) -> set[str]:
        async with await connect() as conn:
            cands, _ = await rs.candidates(conn, world.ctx_a, home_pid, task, read_deps)
            await conn.commit()
        return {c.title for c in cands}

    assert await titles(world.main_id) == {at[0], t_only[0]}  # default: everything readable
    await _policy(connect, OTHER, "exclude")
    assert await titles(world.main_id) == set()  # the [A, T] lesson touches T: not for A
    assert await titles(world.other_id) == {t_only[0]}  # T sees only lessons lying entirely in T


# --------------------------------------------------------------------------- Sol 54 #2 apply time
async def _cross_question(db_dsn, connect, world: World, embedder, relation: tuple[str, str, str]) -> str:  # noqa: ANN001
    """Under ``include``: A_OLD in A, T_NEW in T, reviewed as observer → one cross-project question."""
    await _policy(connect, OTHER, "include")
    await write_items(connect, world.ctx_a, MAIN, [item(*A_OLD, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_NEW, valid_from=D_NEW)])
    await embed(connect, embedder)
    await _drain(db_dsn, connect, Oracle(relations={(T_NEW[0], A_OLD[0]): relation}))
    ((qid,),) = await rows(connect, "SELECT question_id::text FROM librarian_questions WHERE status = 'open'")
    return qid


async def _answer(connect, world: World, qid: str, role: str) -> dict[str, Any]:  # noqa: ANN001
    from hlmemo.librarian.questions import answer

    args = {"project": OTHER, "request_id": str(uuid.uuid4()), "question_id": qid, "decision": "accept"}
    async with await connect() as conn:
        res = await answer(conn, world.ctx_a, args, raw=args, configured_role=role)
        await conn.commit()
    return res


async def _user_rows(connect) -> list[Any]:  # noqa: ANN001
    return await rows(
        connect,
        "SELECT version_id, valid_to::text, superseded_at::text, project_ids FROM memory_versions"
        " WHERE kind <> 'project_card' AND 'librarian-rule' <> ALL(tags) ORDER BY 1",
    ) + await rows(connect, "SELECT link_id FROM links")


async def _promote(connect, world: World) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import record_role_decision

    async with await connect() as conn:
        await record_role_decision(conn, role="assistant", decided_by=world.ctx_admin, decision="D-test")
        await conn.commit()


async def _status(connect) -> list[Any]:  # noqa: ANN001
    return await rows(connect, "SELECT status, answer->>'reason' FROM librarian_questions")


async def test_policy_flip_blocks_an_approved_batch(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import record_batch_decision

    await _cross_question(db_dsn, connect, world, embedder, CONTRA)
    before = await _user_rows(connect)
    (batch,) = (await rows(connect, "SELECT DISTINCT batch_id::text FROM librarian_questions"))[0]
    async with await connect() as conn:
        await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept")
        await conn.commit()
    await _policy(connect, OTHER, "exclude")  # after the approval, before the apply
    await _promote(connect, world)
    llm = ScriptedLLM(default={})
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    assert await _user_rows(connect) == before  # nothing applied: no link, no close
    assert [s for s, _r in await _status(connect)] == ["authority_lost"]
    reasons = await rows(
        connect,
        "SELECT c->>'reason' FROM events, jsonb_array_elements(payload->'resolved'->'question_status') c"
        " WHERE kind = 'librarian' AND payload->'request'->>'op' = 'apply_batch'",
    )
    assert reasons == [("policy_excluded",)]


async def test_policy_flip_blocks_a_direct_memory_answer(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    qid = await _cross_question(db_dsn, connect, world, embedder, CONTRA)
    before = await _user_rows(connect)
    await _policy(connect, OTHER, "exclude")
    await _promote(connect, world)
    ack = await _answer(connect, world, qid, "assistant")  # a direct accept (assistant)
    assert ack["status"] == "authority_lost" and ack["applied"] == {"links": 0, "closed": [], "widened": []}
    assert await _status(connect) == [("authority_lost", "policy_excluded")]
    assert await _user_rows(connect) == before


async def test_policy_flip_blocks_an_accepted_pending_answer_at_promotion(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    qid = await _cross_question(db_dsn, connect, world, embedder, CONTRA)
    before = await _user_rows(connect)
    assert (await _answer(connect, world, qid, "observer"))["status"] == "accepted_pending"  # a label
    await _policy(connect, OTHER, "exclude")
    await _promote(connect, world)  # enqueues the apply of the accepted_pending answer
    provider = make_provider(db_dsn, ScriptedLLM(default={}), budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect).drain()
    await provider.aclose()
    assert [s for s, _r in await _status(connect)] == ["authority_lost"]
    assert await _user_rows(connect) == before


async def test_policy_flip_blocks_an_approved_widen_scope(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    dup = ("duplicate", "none", "high")
    qid = await _cross_question(db_dsn, connect, world, embedder, dup)
    assert await rows(connect, "SELECT kind FROM librarian_questions") == [("widen_scope",)]
    before = await _user_rows(connect)
    await _policy(connect, OTHER, "exclude")
    await _promote(connect, world)
    ack = await _answer(connect, world, qid, "assistant")
    assert ack["status"] == "authority_lost" and ack["applied"]["widened"] == []
    assert await _user_rows(connect) == before  # the item's project_ids did not grow


async def test_policy_flip_between_plan_and_apply_raises_no_question(
    db_dsn, connect, world: World, embedder, monkeypatch
) -> None:  # noqa: ANN001
    """The plan still paired A and T (as if the policy had been ``include`` while planning); the
    apply transaction reads the policy NOW and raises no question for it."""
    from hlmemo.librarian import candidates as cands

    await _policy(connect, OTHER, "exclude")
    monkeypatch.setattr(cands, "isolated_scope", lambda subject, allowed, excluded: list(allowed))
    await write_items(connect, world.ctx_a, MAIN, [item(*A_OLD, valid_from=D_OLD)])
    await write_items(connect, world.ctx_a, OTHER, [item(*T_NEW, valid_from=D_NEW)])
    await embed(connect, embedder)
    await _drain(db_dsn, connect, Oracle(relations={(T_NEW[0], A_OLD[0]): CONTRA}))
    assert await rows(connect, "SELECT kind FROM librarian_questions") == []
    blocked = await rows(
        connect,
        "SELECT payload->'request'->'policy_excluded' FROM events WHERE kind = 'librarian'"
        " AND payload->'request' ? 'policy_excluded'",
    )
    assert len(blocked) == 1 and blocked[0][0]  # the planned pair, recorded as policy_excluded


# --------------------------------------------------------------------------- Sol 54 #5 replay
async def test_project_policy_is_rebuilt_by_replay(connect, world: World) -> None:  # noqa: ANN001
    from hlmemo.db.replay import rebuild_projections

    await _policy(connect, OTHER, "exclude")
    await _policy(connect, OTHER, "include")
    await _policy(connect, MAIN, "exclude")
    async with await connect() as conn:
        await conn.execute(
            'UPDATE projects SET policy = policy || \'{"librarian_cross_project": "exclude"}\''
            " WHERE project_id = %s",
            (world.other_id,),
        )  # drift the table away from the events
        await conn.execute(
            "UPDATE projects SET policy = policy - 'librarian_cross_project' WHERE project_id = %s",
            (world.main_id,),
        )
        await rebuild_projections(conn)
        await conn.commit()
    got = dict(await rows(connect, "SELECT slug, policy->>'librarian_cross_project' FROM projects"))
    assert (got[MAIN], got[OTHER]) == ("exclude", "include")
    assert got[GLOBAL_PROJECT] is None


# --------------------------------------------------------------------------- Sol 54 #3 small scope
async def test_small_scope_reimport_replaces_the_split_file(connect, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Re-importing ONE multi-rule file imported as a single fact: its bare item is
    ``replaced_by_split`` and closed in the same import, although it is 100% of the in-scope items
    (the mass-close guard would have kept it open next to the new lessons: duplicates)."""
    import shutil

    pid, _ = await make_project(connect, "am1")
    ctx = await make_device(connect, "am1-importer", {pid: "write"})
    call = Caller(connect, ctx)
    d = tmp_path / "memory"
    d.mkdir()
    shutil.copy(FIXTURE / "automemory" / "feedback_deploy_footguns.md", d)
    meter = Meter()
    monkeypatch.setattr(automemory, "memory_type", lambda meta: str(meta.get("type") or "").lower())
    first = await import_async(
        call,
        source="automemory",
        parsed=parse_source("automemory", [d], tz=UTC),
        project="am1",
        dry_run=False,
        meter=meter,
    )
    assert first["counts"]["new"] == 1 and [i["kind"] for i in first["items"]] == ["fact"]
    monkeypatch.undo()
    parsed = parse_source("automemory", [d], tz=UTC)
    dry = await import_async(
        call, source="automemory", parsed=parsed, project="am1", dry_run=True, meter=meter
    )
    assert dry["replaced_by_split"] == [
        {"key": "automemory:feedback_deploy_footguns.md", "by": sorted(r.key for r in parsed.records)}
    ]
    assert dry["closed"] == ["automemory:feedback_deploy_footguns.md"] and dry["missing"] == []
    rep = await import_async(
        call, source="automemory", parsed=parsed, project="am1", dry_run=False, meter=meter
    )
    assert rep["counts"]["new"] == 5 and rep["writes"]["closed"] == 1 and not rep["writes"]["failed"]
    kept = await rows(
        connect,
        "SELECT source->>'path' FROM memory_versions WHERE project_id = %s AND source IS NOT NULL"
        " AND superseded_at = 'infinity' AND valid_to = 'infinity' ORDER BY 1",
        (pid,),
    )
    assert [p for (p,) in kept] == sorted(r.path for r in parsed.records)  # no duplicate left open
    kept_missing = await import_async(
        call, source="automemory", parsed=parsed, project="am1", dry_run=False, meter=meter, close=False
    )
    assert kept_missing["writes"]["written"] == 0  # idempotent


# --------------------------------------------------------------------------- Sol 55 #1 content loss
async def test_failed_section_write_keeps_the_old_item_open(connect, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """A replacement section whose write fails: the old whole-file item is NOT closed (no content
    loss); the report says which section is missing; the next clean run closes it."""
    import shutil

    from hlmemo.cli.mcp_client import ToolCallError
    from hlmemo.importers.cli import human_summary

    pid, _ = await make_project(connect, "am2")
    ctx = await make_device(connect, "am2-importer", {pid: "write"})
    d = tmp_path / "memory"
    d.mkdir()
    shutil.copy(FIXTURE / "automemory" / "feedback_deploy_footguns.md", d)
    meter = Meter()
    call = Caller(connect, ctx)
    monkeypatch.setattr(automemory, "memory_type", lambda meta: str(meta.get("type") or "").lower())
    await import_async(
        call,
        source="automemory",
        parsed=parse_source("automemory", [d], tz=UTC),
        project="am2",
        dry_run=False,
        meter=meter,
    )
    monkeypatch.undo()

    class Failing(Caller):
        async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            if tool == "memory.write" and args["items"][0].get("source", {}).get("path", "").endswith(
                "#rule-2"
            ):
                raise ToolCallError("E_UNAVAILABLE", "injected failure", retryable=True)
            return await super().__call__(tool, args)

    parsed = parse_source("automemory", [d], tz=UTC)
    rep = await import_async(
        call=Failing(connect, ctx),
        source="automemory",
        parsed=parsed,
        project="am2",
        dry_run=False,
        meter=meter,
    )
    w = rep["writes"]
    assert [f["key"] for f in w["failed"]] == ["automemory:feedback_deploy_footguns.md#rule-2"]
    assert w["closed"] == 0 and w["kept_open"] == [
        {
            "key": "automemory:feedback_deploy_footguns.md",
            "reason": "replacement-incomplete",
            "absent": ["automemory:feedback_deploy_footguns.md#rule-2"],
        }
    ]
    assert (
        "kept open (replacement-incomplete; not stored: automemory:feedback_deploy_footguns.md#rule-2)"
        in (human_summary(rep))
    )
    open_bare = (
        "SELECT count(*) FROM memory_versions WHERE project_id = %s AND source->>'path' = %s"
        " AND valid_to = 'infinity' AND superseded_at = 'infinity'"
    )
    assert (await rows(connect, open_bare, (pid, "feedback_deploy_footguns.md")))[0][0] == 1
    again = await import_async(
        call, source="automemory", parsed=parsed, project="am2", dry_run=False, meter=meter
    )
    assert (
        again["writes"]["closed"] == 1
        and again["writes"]["kept_open"] == []
        and not again["writes"]["failed"]
    )
    assert (await rows(connect, open_bare, (pid, "feedback_deploy_footguns.md")))[0][0] == 0


# --------------------------------------------------------------------------- Sol 55 #2 answer race
async def test_answer_sees_a_concurrent_revision_adding_an_excluded_project(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    """A widen_scope A→B was proposed; while the owner answers, a revision adds the excluded T to
    the B item. The answer takes the item lock BEFORE the policy recheck, waits for the revision
    and refuses (authority_lost, policy_excluded); nothing is widened."""
    import asyncio

    from hlmemo.core.write_service import write
    from hlmemo.librarian.questions import answer

    b_pid, _ = await make_project(connect, "g6-third")
    dev = await make_device(
        connect, "dev-abt", {world.main_id: "write", world.other_id: "write", b_pid: "write"}
    )
    await _policy(connect, OTHER, "exclude")
    lesson_b = ("Never pipe an ssh heredoc", "Never use bash -s with an ssh heredoc: stdin is swallowed.")
    lesson_a = ("ssh heredoc stdin", "Using bash -s over ssh with a heredoc swallows stdin; avoid it.")
    (c,) = await write_items(
        connect, dev, "g6-third", [{**item(*lesson_b, valid_from=D_OLD), "kind": "lesson"}]
    )
    await write_items(connect, dev, MAIN, [{**item(*lesson_a), "kind": "lesson"}])
    await embed(connect, embedder)
    await _drain(
        db_dsn, connect, Oracle(relations={(lesson_a[0], lesson_b[0]): ("duplicate", "none", "high")})
    )
    ((qid, kind),) = await rows(connect, "SELECT question_id::text, kind FROM librarian_questions")
    assert kind == "widen_scope"
    await _promote(connect, world)

    reviser = await connect()
    await reviser.execute("SELECT 1")  # an open transaction: the revision's item lock is held
    await write(
        reviser,
        dev,
        {
            "project": "g6-third",
            "request_id": str(uuid.uuid4()),
            "client": "pytest/0",
            "items": [
                {
                    **item(*lesson_b, valid_from=D_OLD, project_ids=["g6-third", OTHER]),
                    "kind": "lesson",
                    "logical_id": c.logical_id,
                    "expected_version_id": c.version_id,
                }
            ],
        },
    )
    args = {"project": MAIN, "request_id": str(uuid.uuid4()), "question_id": qid, "decision": "accept"}

    async def owner() -> dict[str, Any]:
        async with await connect() as conn:
            res = await answer(conn, dev, args, raw=args, configured_role="assistant")
            await conn.commit()
        return res

    task = asyncio.create_task(owner())
    await asyncio.sleep(0.7)
    assert not task.done()  # waiting for the revision's item lock
    await reviser.commit()
    await reviser.close()
    ack = await asyncio.wait_for(task, 10)
    assert ack["status"] == "authority_lost" and ack["applied"]["widened"] == [], ack
    assert await rows(connect, "SELECT status, answer->>'reason' FROM librarian_questions") == [
        ("authority_lost", "policy_excluded")
    ]
    heads = await rows(
        connect,
        "SELECT project_ids FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'",
        (c.logical_id,),
    )
    assert [sorted(p) for (p,) in heads] == [sorted([b_pid, world.other_id])]  # MAIN never added


# --------------------------------------------------------------------------- Sol 55 #4 legacy replay
async def test_legacy_policy_event_replays(connect, world: World) -> None:  # noqa: ANN001
    """A policy change recorded in the pre-``resolved.project_policy`` shape is rebuilt by replay."""
    from hlmemo.db.replay import rebuild_projections
    from hlmemo.librarian.events import insert_system_event

    async with await connect() as conn:
        cur = await conn.execute("SELECT clock_timestamp()")
        (at,) = await cur.fetchone()
        await insert_system_event(
            conn,
            kind="librarian",
            project_id=world.other_id,
            device_id=1,
            client="hlm-ops/0.0.1",
            request_id=uuid.uuid4(),
            request={
                "actor": "hlm-ops/0.0.1",
                "op": "set_project_policy",
                "key": KEY,
                "value": "exclude",
                "previous": None,
            },
            resolved={"recorded_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")},
            at=at,
        )
        await conn.commit()
        assert (await ops.project_policy(conn, OTHER))["policy"] == {}  # the old code set only the row
        await rebuild_projections(conn)
        await conn.commit()
        assert (await ops.project_policy(conn, OTHER))["policy"] == {KEY: "exclude"}
