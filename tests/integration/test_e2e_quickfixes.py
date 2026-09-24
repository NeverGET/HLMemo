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
