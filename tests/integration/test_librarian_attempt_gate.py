"""Sol 37 — the privacy/authority gate runs before EVERY provider attempt, working-memory rules
are rule-shaped and redacted, the lineage ceiling is atomic, and a retried job replays identically.

* revoke during the backoff after a 503 → the retry is never sent;
* revoke before the fallback profile → the fallback is never sent;
* a rule carrying a seeded secret reaches the prompt redacted; an item body pasted as a "rule" (or
  a rule another device wrote) never reaches it;
* 50 concurrent calls on one lineage → exactly 20 reach the provider;
* a job that failed once (its run_after moved by the backoff) and then succeeded rebuilds to the
  identical FULL jobs projection.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.db import auth_queries as aq
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.budget import Caps, DbBudget
from hlmemo.librarian.errors import JobCallCapExceeded
from hlmemo.librarian.ledger import DbLedger
from hlmemo.librarian.memory import memory_ctx
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.reserved import MEMORY_PROJECT, reserved_ids
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FakeClock,
    ScriptedLLM,
    chat,
    conn_ctx,
    dump_full_jobs_and_questions,
    enqueue_pair,
    lib_settings,
    make_provider,
    make_worker,
    outcomes,
    seed_reserved,
    stub_chain,
)
from tests.integration._write_fixtures import MAIN, World, dump_projections, item, seed_world, write_req

pytestmark = pytest.mark.integration
SECRET = "sk-" + "or-v1-" + "fedcba9876543210" * 4  # assembled at run time


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _pair(connect, world: World, deps, key: str) -> None:  # noqa: ANN001
    async with await connect() as conn:
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("Limit", "old limit 10", valid_from=datetime(2026, 1, 1, tzinfo=UTC).isoformat()),
                    item("Limit", "new limit 20"),
                ],
            ),
            deps=deps,
        )
        old, new = res.versions
        await enqueue_pair(
            conn,
            project_id=world.main_id,
            trigger_device_id=world.dev_a,
            subject_vid=new.version_id,
            candidate_vids=[old.version_id],
            key=key,
        )
        await conn.commit()


async def _revoke(db_dsn: str, device_id: int) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        await aq.select_device_for_update(conn, device_id)
        await aq.set_device_revoked(conn, device_id)
        await aq.revoke_all_grants(conn, device_id)
        await conn.commit()


@pytest.mark.parametrize("first_answer", [503, 400], ids=["retry-after-backoff", "fallback-profile"])
async def test_revocation_before_the_next_attempt_stops_it(
    db_dsn, connect, world, deps, first_answer
) -> None:  # noqa: ANN001
    """503 → the primary retries after a backoff; 400 → the fallback profile is tried. Either way
    the device is revoked while the first attempt is in flight: no second request is sent."""
    await _pair(connect, world, deps, f"gate-{first_answer}")
    fired: list[int] = []

    async def revoke_on_first(_body: dict) -> None:
        if not fired:
            fired.append(1)
            await _revoke(db_dsn, world.dev_a)

    llm = ScriptedLLM([first_answer], default=CONTRADICTS_B, on_request=revoke_on_first)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 1
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]


async def test_rules_are_redacted_and_rule_shaped(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    body_text = "PASTED ITEM BODY " + ("lorem ipsum dolor sit amet " * 40)  # > MAX_RULE_CHARS
    async with await connect() as conn:
        ids = await reserved_ids(conn)
        lib = memory_ctx(ids.librarian_device_id, ids.memory_project_id)
        rule = {"kind": "fact", "tags": ["librarian-rule"], "importance": 9}
        # written by the librarian but NOT via write_rule: the secret must still be redacted at load
        await write(
            conn,
            lib,
            write_req(MEMORY_PROJECT, [{**rule, "title": "r1", "body": f"Never log {SECRET}"}]),
            deps=deps,
        )
        await write(
            conn, lib, write_req(MEMORY_PROJECT, [{**rule, "title": "r2", "body": body_text}]), deps=deps
        )
        # a "rule" another device wrote (the admin bypasses grants): never trusted as working memory
        await write(
            conn,
            world.ctx_admin,
            write_req(MEMORY_PROJECT, [{**rule, "title": "r3", "body": "admin rule"}]),
            deps=deps,
        )
        await conn.commit()
    await _pair(connect, world, deps, "rules")
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    prompt = json.dumps(llm.requests, ensure_ascii=False)
    assert "Never log" in prompt and SECRET not in prompt and "REDACTED:openrouter_key" in prompt
    assert "PASTED ITEM BODY" not in prompt and "admin rule" not in prompt


async def test_lineage_ceiling_is_atomic_under_concurrency(db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=("stall", 0.05, chat(CONTRADICTS_B)))
    ctx = conn_ctx(db_dsn)
    big = Decimal(1000)
    p = Provider(
        stub_chain(fallback=False),
        budget=DbBudget(ctx, Caps(big, big, big)),
        ledger=DbLedger(ctx),
        transport=llm.transport,
        clock=FakeClock(),
        redactor=Redactor(),
        job_call_cap=20,
    )
    task = load_task("contradiction")
    user = 'JOB: contradiction\nINPUT: {"A": {"text": "a"}, "B": {"text": "b"}}'
    lineage = "11111111-2222-4333-8444-555555555555"
    results = await asyncio.gather(
        *(p.complete(task, user, lineage=lineage) for _ in range(50)), return_exceptions=True
    )
    await p.aclose()
    assert llm.calls == 20
    assert sum(isinstance(r, JobCallCapExceeded) for r in results) == 30
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        cur = await conn.execute("SELECT calls FROM llm_lineage_calls WHERE lineage = %s", (lineage,))
        assert await cur.fetchone() == (20,)


async def test_retried_job_replays_to_the_full_jobs_projection(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    await _pair(connect, world, deps, "retry")
    bad = {"contradicts": "maybe", "supersedes": "C", "reason": 1}
    llm = ScriptedLLM([bad, bad], default=CONTRADICTS_B)  # attempt 1: schema_fail -> backoff
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn), provider, connect)
    assert await worker.drain() == 1  # failed once, re-queued with run_after = now + 1 s
    await asyncio.sleep(1.2)
    assert await worker.drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts, run_after > created_at FROM jobs"
            " WHERE dedupe_key = 'librarian_write:retry'"
        )
        assert await cur.fetchone() == ("done", 2, True)
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before


# --------------------------------------------------------------------------- Sol 38 / D-062
async def test_precheck_waits_for_an_in_progress_revocation_and_sends_nothing(
    db_dsn, connect, world, deps
) -> None:  # noqa: ANN001
    """D-062: the precheck takes the apply-recheck locks (FOR SHARE); a revocation in progress
    blocks it, and once the revocation commits (before the precheck reads) no request is sent."""
    await _pair(connect, world, deps, "gate-lock")
    revoker = await psycopg.AsyncConnection.connect(db_dsn)
    await aq.select_device_for_update(revoker, world.dev_a)
    await aq.set_device_revoked(revoker, world.dev_a)
    await aq.revoke_all_grants(revoker, world.dev_a)  # not committed yet
    llm = ScriptedLLM(default=CONTRADICTS_B)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    drain = asyncio.create_task(make_worker(lib_settings(db_dsn), provider, connect).drain())
    await asyncio.sleep(0.5)
    assert not drain.done() and llm.calls == 0  # the gate is waiting on the revoker's lock
    await revoker.commit()
    await revoker.close()
    await asyncio.wait_for(drain, 30)
    await provider.aclose()
    assert llm.calls == 0
    async with await connect() as conn:
        cur = await conn.execute("SELECT count(*) FROM links")
        assert (await cur.fetchone())[0] == 0


async def test_revocation_while_the_request_is_in_flight_is_never_applied(
    db_dsn, connect, world, deps
) -> None:  # noqa: ANN001
    """D-062: an attempt in flight when the revocation commits completes, but its result is never
    applied: the apply-time recheck records authority_lost (no link, no question)."""
    await _pair(connect, world, deps, "gate-inflight")

    async def revoke(_body: dict) -> None:
        await _revoke(db_dsn, world.dev_a)

    llm = ScriptedLLM(default=CONTRADICTS_B, on_request=revoke)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 1  # the in-flight request completed
    async with await connect() as conn:
        assert await outcomes(conn) == ["authority_lost"]
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM links), (SELECT count(*) FROM librarian_questions)"
        )
        assert await cur.fetchone() == (0, 0)


async def test_rule_overlapping_an_item_body_is_refused(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    from hlmemo.core.errors import ToolError
    from hlmemo.librarian.memory import write_rule

    body = (
        "The deploy pipeline must always run database migrations before switching traffic to the new release."
    )
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(MAIN, [item("Deploy order", body)]), deps=deps)
        vid = res.versions[0].version_id
        await conn.commit()
        # 8 shared words (case and punctuation differ) with a source-project item: refused
        with pytest.raises(ToolError, match="8-word run"):
            await write_rule(
                conn,
                title="r",
                text="Rule: the Deploy pipeline must ALWAYS run database, migrations first.",
                clue_refs=[],
                dedupe="ov1",
                deps=deps,
                source_project_ids=[world.main_id],
            )
        await conn.rollback()
        # the source project is also derived from the clue references
        with pytest.raises(ToolError, match=f"v{vid}"):
            await write_rule(
                conn,
                title="r",
                text="Mind: migrations before switching traffic to the new release, always.",
                clue_refs=[f"v{vid}"],
                dedupe="ov2",
                deps=deps,
            )
        await conn.rollback()
        # 7 shared words only: a genuine rule is accepted
        ok = await write_rule(
            conn,
            title="r",
            text="Prefer: deploy pipeline must always run database migrations. Check it.",
            clue_refs=[f"v{vid}"],
            dedupe="ov3",
            deps=deps,
        )
        await conn.commit()
        assert ok > vid


async def test_budget_denials_never_consume_the_lineage_ceiling(db_dsn) -> None:  # noqa: ANN001
    """Sol 38 #5: a slot is claimed only when an HTTP attempt really starts."""
    from hlmemo.librarian.budget import NoBudget
    from hlmemo.librarian.errors import BudgetDeferred

    class DenyFirst(NoBudget):
        def __init__(self, n: int) -> None:
            self.left = n

        async def reserve(self, call_id, worst_usd, job_id):  # noqa: ANN001, ANN201
            self.left -= 1
            return self.left < 0

    llm = ScriptedLLM(default=CONTRADICTS_B)
    ctx = conn_ctx(db_dsn)
    p = Provider(
        stub_chain(fallback=False),
        budget=DenyFirst(25),
        ledger=DbLedger(ctx),
        transport=llm.transport,
        clock=FakeClock(),
        redactor=Redactor(),
        job_call_cap=20,
    )
    task = load_task("contradiction")
    user = 'JOB: contradiction\nINPUT: {"A": {"text": "a"}, "B": {"text": "b"}}'
    lineage = "22222222-3333-4444-8555-666666666666"
    for _ in range(25):
        with pytest.raises(BudgetDeferred):
            await p.complete(task, user, lineage=lineage)
    await p.complete(task, user, lineage=lineage)
    await p.aclose()
    assert llm.calls == 1
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        cur = await conn.execute("SELECT calls FROM llm_lineage_calls WHERE lineage = %s", (lineage,))
        assert await cur.fetchone() == (1,)


async def test_deferred_job_replays_its_run_after(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    """Sol 38 #6 as amended by Sol 56 #4: a job handed back for a SYSTEMIC reason (a budget refusal:
    no attempt consumed) changes only its job row — status queued, attempts unchanged, run_after
    and last_error as scheduling hints — and writes NO event; a rebuild restores it as queued with
    the same attempts (its run_after/last_error hints are not event-recorded)."""
    await _pair(connect, world, deps, "deferred")
    llm = ScriptedLLM(default=CONTRADICTS_B)
    zero = Decimal(0)
    provider = make_provider(db_dsn, llm, caps=Caps(zero, zero, zero))
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    assert llm.calls == 0
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts, last_error, run_after > created_at + interval '30 seconds' FROM jobs"
            " WHERE dedupe_key = 'librarian_write:deferred'"
        )
        assert await cur.fetchone() == ("queued", 0, "E_BUDGET_DEFERRED", True)
        cur = await conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'librarian' AND payload->'request'->>'op' = 'defer'"
        )
        assert (await cur.fetchone())[0] == 0  # a systemic hand-back is not an event
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before
        cur = await conn.execute(
            "SELECT status, attempts FROM jobs WHERE dedupe_key = 'librarian_write:deferred'"
        )
        assert await cur.fetchone() == ("queued", 0)
    # D-086 §2: more hand-backs write nothing either; the completion is the ONE terminal event
    for _ in range(2):
        async with await connect() as conn:
            await conn.execute(
                "UPDATE jobs SET run_after = now() WHERE dedupe_key = 'librarian_write:deferred'"
            )
            await conn.commit()
        provider = make_provider(db_dsn, llm, caps=Caps(zero, zero, zero))
        await make_worker(lib_settings(db_dsn), provider, connect).drain()
        await provider.aclose()
    async with await connect() as conn:
        await conn.execute("UPDATE jobs SET run_after = now() WHERE dedupe_key = 'librarian_write:deferred'")
        await conn.commit()
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    assert await make_worker(lib_settings(db_dsn), provider, connect).drain() == 1
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT request_id, payload->'resolved'->>'outcome' FROM events WHERE kind = 'librarian'"
            " AND (payload->'request'->>'job_key' = 'librarian_write:deferred'"
            "      OR payload->'resolved'->'done'->>'dedupe_key' = 'librarian_write:deferred')"
        )
        from hlmemo.librarian.events import NS_LIBRARIAN

        [(rid, outcome)] = await cur.fetchall()
        assert rid == uuid.uuid5(NS_LIBRARIAN, "job:librarian_write:deferred") and outcome == "proposed"
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before


async def test_one_terminal_event_per_job_and_compact_backoffs(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
    """Sol 56 #4 (D-062 "one event per job"): a job that fails on every attempt records one compact
    non-terminal event per consumed attempt (back-off, at most MAX_ATTEMPTS - 1) and EXACTLY ONE
    terminal event (outcome failed) under the job's own request id — the id its completion event
    would carry — so no job can record two terminal events. Rebuild identical."""
    from hlmemo.librarian.events import NS_LIBRARIAN
    from hlmemo.librarian.jobs import enqueue, job_spec
    from hlmemo.worker.lease import MAX_ATTEMPTS

    class Boom:  # a job-specific failure on every attempt (not a systemic hand-back)
        op = "boom"

        async def plan(self, w, job):  # noqa: ANN001, ANN202
            raise RuntimeError("job-specific failure")

    async with await connect() as conn:
        spec = job_spec(kind="librarian_write", dedupe_key="librarian_write:doomed", payload={"op": "boom"})
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()
    provider = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn), provider, connect, handlers={"boom": Boom()})
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            async with await connect() as conn:  # skip the back-off wait
                await conn.execute(
                    "UPDATE jobs SET run_after = now() WHERE dedupe_key = 'librarian_write:doomed'"
                )
                await conn.commit()
        await worker.drain()
        if attempt == 0:  # review 60: a consumed back-off is event-recorded: compared on RAW fields
            async with await connect() as conn:
                cur = await conn.execute(
                    "SELECT status, attempts, last_error FROM jobs"
                    " WHERE dedupe_key = 'librarian_write:doomed'"
                )
                assert await cur.fetchone() == ("queued", 1, "E_RuntimeError")
                before = await dump_full_jobs_and_questions(conn)
                row = next(r for r in before["jobs"] if "librarian_write:doomed" in r)
                assert "E_RuntimeError" in row  # not masked: run_after/last_error are compared
                await rebuild_projections(conn)
                await conn.commit()
                assert await dump_full_jobs_and_questions(conn) == before
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT status, attempts FROM jobs WHERE dedupe_key = 'librarian_write:doomed'"
        )
        assert await cur.fetchone() == ("failed", MAX_ATTEMPTS)
        cur = await conn.execute(
            "SELECT request_id, payload->'resolved'->>'outcome' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'job_key' = 'librarian_write:doomed' ORDER BY event_id"
        )
        rows = await cur.fetchall()
        assert [o for _r, o in rows] == ["deferred"] * (MAX_ATTEMPTS - 1) + ["failed"]
        assert rows[-1][0] == uuid.uuid5(NS_LIBRARIAN, "job:librarian_write:doomed")  # the terminal id
        before = {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)}
        await rebuild_projections(conn)
        await conn.commit()
        assert {**await dump_projections(conn), **await dump_full_jobs_and_questions(conn)} == before


# --------------------------------------------------------------------------- review 61: replay oracle
async def _replay_dumps(conn: psycopg.AsyncConnection) -> dict[str, dict[str, list[str]]]:
    """The three replay dumps that compare the jobs projection."""
    from tests.integration._w2b_fixtures import dump_w2b

    return {
        "projections": await dump_projections(conn),
        "full": await dump_full_jobs_and_questions(conn),
        "w2b": await dump_w2b(conn),
    }


async def _enqueue_one(connect, world, key: str, op: str) -> None:  # noqa: ANN001
    from hlmemo.librarian.jobs import enqueue, job_spec

    async with await connect() as conn:
        spec = job_spec(kind="librarian_write", dedupe_key=f"librarian_write:{key}", payload={"op": op})
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()


async def test_review61_backoff_then_handback_passes_replay(db_dsn, connect, world) -> None:  # noqa: ANN001
    """Review 61: a job-specific failure (a back-off: attempt consumed, recorded in a ``defer``
    event) followed by a SYSTEMIC hand-back (job row only, no event). Live ends queued with the
    hand-back's hints; replay restores the recorded back-off state. The pair is compared without
    the two scheduling hints only, so the rebuild passes every replay dump; a wrong attempts count
    in that same pair is still flagged."""
    from hlmemo.librarian.errors import NotReady

    class Flaky:  # attempt 1: a job-specific failure; attempt 2: inputs not ready (systemic)
        op = "flaky"
        calls = 0

        async def plan(self, w, job):  # noqa: ANN001, ANN202
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("job-specific failure")
            raise NotReady("embeddings in flight", retry_after_s=60)

    await _enqueue_one(connect, world, "flaky", "flaky")
    provider = make_provider(db_dsn, ScriptedLLM(), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn), provider, connect, handlers={"flaky": Flaky()})
    await worker.drain()
    async with await connect() as conn:  # skip the back-off wait (overwritten by the hand-back)
        await conn.execute("UPDATE jobs SET run_after = now() WHERE dedupe_key = 'librarian_write:flaky'")
        await conn.commit()
    await worker.drain()
    await provider.aclose()
    assert Flaky.calls == 2
    select = "SELECT status, attempts, last_error FROM jobs WHERE dedupe_key = 'librarian_write:flaky'"
    async with await connect() as conn:
        cur = await conn.execute(select)
        assert await cur.fetchone() == ("queued", 1, "E_NOT_READY")  # the live hand-back
        live = await _replay_dumps(conn)
        await rebuild_projections(conn)
        await conn.commit()
        cur = await conn.execute(select)
        assert await cur.fetchone() == ("queued", 1, "E_RuntimeError")  # the recorded back-off
        replayed = await _replay_dumps(conn)
        for name in live:
            assert replayed[name] == live[name], name
            assert live[name] == replayed[name], name
            assert sorted(set(live[name]["jobs"]) ^ set(replayed[name]["jobs"])) == [], name
        await conn.execute("UPDATE jobs SET attempts = 0 WHERE dedupe_key = 'librarian_write:flaky'")
        await conn.commit()
        wrong = await _replay_dumps(conn)
        for name in ("full", "w2b"):  # (dump_projections never compared attempts)
            assert wrong[name]["jobs"] != live[name]["jobs"], name


async def test_review61_a_wrongly_replayed_run_after_of_a_pristine_queued_job_is_flagged(
    db_dsn, connect, world
) -> None:  # noqa: ANN001
    """Review 61: a pristine queued job (never run, ``last_error`` NULL on both sides) is not a
    hand-back: its ``run_after`` is the recording event's, so a replay that restores another one
    is a divergence every replay dump flags (the old mask hid it). A correct rebuild is equal."""
    await _enqueue_one(connect, world, "pristine", "boom")
    async with await connect() as conn:
        live = await _replay_dumps(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await _replay_dumps(conn) == live  # NULL↔NULL, compared raw: identical
        await conn.execute(  # a replay bug: the event-recorded run_after is not restored
            "UPDATE jobs SET run_after = run_after + interval '1 hour'"
            " WHERE dedupe_key = 'librarian_write:pristine'"
        )
        await conn.commit()
        wrong = await _replay_dumps(conn)
    for name in live:
        assert wrong[name]["jobs"] != live[name]["jobs"], name
        assert wrong[name] != live[name] and live[name] != wrong[name], name
        assert sorted(set(live[name]["jobs"]) ^ set(wrong[name]["jobs"])) != [], name
