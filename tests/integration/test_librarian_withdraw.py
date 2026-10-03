"""``ops librarian withdraw`` and the promotion guard (D-244 follow-up).

* withdraw: open / approved / accepted_pending questions of ONE project become ``withdrawn`` through
  ONE ``librarian`` event (device 1, the ops client, op ``withdraw``); no user item, link, validity or
  scope changes; replay rebuilds exactly the same rows;
* a withdrawn question is never applied: not by a queued ``apply_batch`` job (from a batch decision or
  a promotion), not by the sweeper, not by ``release_pending``, not by a later promotion, not by
  ``memory.answer``; the expiry leaves it alone;
* every refusal (unknown id, another project, applied, other status, running apply, a link that does
  not join the subjects, a non-operator, no reason, malformed ids) writes NOTHING;
* withdraw waits for the role-order lock (a promotion in flight) and for the question rows (an apply
  in flight);
* the guard: a promotion that would release pending questions is refused with the counts per project
  unless ``release_pending`` is that exact count; a demotion never; ``role set --dry-run`` records
  nothing.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from hlmemo.core.errors import ToolError
from hlmemo.librarian.questions import expire_due
from hlmemo.librarian.roles import (
    lock_role_order,
    promotion_release,
    record_batch_decision,
    record_role_decision,
)
from hlmemo.librarian.withdraw import withdraw
from hlmemo.ops.librarian import _ops_ctx
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
)
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, embed, write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, count, item, seed_world
from tests.integration.test_w2c_questions import (
    CONTRA,
    D_OLD,
    NEW,
    _answer,
    _args,
    _err,
    _propose,
    _replay_identical,
    _user_state,
)

pytestmark = pytest.mark.integration

EXTRA = ("Deploy region", "Production traffic is served from the Vilnius region only.")
TWO = Oracle(relations={**CONTRA, (NEW[0], EXTRA[0]): ("refines", "none", "med")})
REASON = "D-244: not a real contradiction (verified)"


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _two_questions(db_dsn, connect, world: World, embedder) -> tuple[Any, Any, dict[str, str]]:  # noqa: ANN001
    """OLD, EXTRA, NEW in MAIN reviewed as observer: a contradiction and a link (one batch)."""
    await write_items(connect, world.ctx_a, MAIN, [item(*EXTRA, valid_from=D_OLD)])
    old, new, rows = await _propose(db_dsn, connect, world, embedder, oracle=TWO)
    by_kind = {k: q for q, k in rows}
    assert set(by_kind) == {"contradiction", "link"}
    return old, new, by_kind


async def _withdraw(connect, ids: list[str], **kw: Any) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        res = await withdraw(
            conn,
            project=kw.pop("project", MAIN),
            question_ids=ids,
            reason=kw.pop("reason", REASON),
            by=kw.pop("by", _ops_ctx("cemal")),
            **kw,
        )
        await conn.commit()
    return res


async def _refused(connect, ids: list[str], **kw: Any) -> ToolError:  # noqa: ANN001
    """A withdraw that must be refused; returns the error. The caller checks nothing was written."""
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await withdraw(
                conn,
                project=kw.pop("project", MAIN),
                question_ids=ids,
                reason=kw.pop("reason", REASON),
                by=kw.pop("by", _ops_ctx()),
                **kw,
            )
        await conn.rollback()
    return ei.value


async def _snapshot(connect) -> tuple[Any, ...]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT question_id::text, status, decided_by, answer::text FROM librarian_questions ORDER BY 1"
        )
        rows = await cur.fetchall()
        n = await count(conn, "events")
        await conn.rollback()
    return n, rows


async def _statuses(connect) -> dict[str, str]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT question_id::text, status FROM librarian_questions")
        out = dict(await cur.fetchall())
        await conn.rollback()
    return out


# --------------------------------------------------------------------------- the event + replay
async def test_withdraw_is_one_evented_status_change_and_replays(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    _old, _new, q = await _two_questions(db_dsn, connect, world, embedder)
    ack = await _answer(connect, world.ctx_a, _args(q["contradiction"], "accept"))
    assert ack["status"] == "accepted_pending"
    before_state, before = await _user_state(connect, world), await _snapshot(connect)
    ids = sorted(q.values())

    plan = await _withdraw(connect, ids, dry_run=True)  # every check, nothing recorded
    assert plan["dry_run"] and plan["event_id"] is None and plan["question_ids"] == ids
    assert plan["by_status"] == {"accepted_pending": 1, "open": 1}
    assert await _snapshot(connect) == before

    res = await _withdraw(connect, ids, reason=f"{REASON}  (api_key=sk-or-v1-{'a' * 40})")
    assert res["event_id"] is not None and res["withdrawn"] == 2 and not res["dry_run"]
    assert "sk-or-v1" not in res["reason"]  # the reason is redacted before it is recorded
    async with await connect() as conn:
        assert await count(conn, "events") == before[0] + 1  # ONE event
        cur = await conn.execute(
            "SELECT kind, device_id, client, project_id, payload FROM events WHERE event_id = %s",
            (res["event_id"],),
        )
        kind, device, client, project_id, payload = await cur.fetchone()
        assert (kind, device, project_id) == ("librarian", 1, world.main_id)
        assert client.startswith("hlm-ops/") and client.endswith("(owner:cemal)")
        req, resolved = payload["request"], payload["resolved"]
        assert req["op"] == "withdraw" and req["question_ids"] == ids and req["reason"] == res["reason"]
        assert resolved["mutations"] == []
        changes = {c["question_id"]: c for c in resolved["question_status"]}
        assert {c["status"] for c in changes.values()} == {"withdrawn"}
        accepted = changes[q["contradiction"]]["answer"]
        assert accepted["decision"] == "withdraw" and accepted["prior_status"] == "accepted_pending"
        assert accepted["prior"]["decision"] == "accept" and accepted["prior"]["by"] == world.dev_a
        assert "prior" not in changes[q["link"]]["answer"]  # never answered
        cur = await conn.execute(
            "SELECT status, decided_by, answer->>'decision' FROM librarian_questions ORDER BY 1"
        )
        assert await cur.fetchall() == [("withdrawn", 1, "withdraw")] * 2
        await conn.rollback()
    assert await _user_state(connect, world) == before_state  # no item, link, validity or scope
    await embed(connect, embedder)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- never applied
async def test_withdrawn_is_never_applied_by_any_path(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    """accepted_pending (memory.answer) + approved (batch decision) → a promotion queues their apply
    jobs → withdraw → the promoted worker runs those jobs, the sweeper, a release_pending job and a
    second promotion: NOTHING is applied; memory.answer refuses; the expiry leaves them; replay."""
    from hlmemo.librarian.jobs import enqueue
    from hlmemo.librarian.tasks.release_pending import release_job

    old, new, q = await _two_questions(db_dsn, connect, world, embedder)
    assert (await _answer(connect, world.ctx_a, _args(q["contradiction"], "accept")))["status"] == (
        "accepted_pending"
    )
    async with await connect() as conn:
        cur = await conn.execute("SELECT DISTINCT batch_id::text FROM librarian_questions")
        [(batch,)] = await cur.fetchall()
        # the promotion releases the accepted_pending answer (its own apply job) ...
        event_id = await record_role_decision(
            conn, role="assistant", decided_by=world.ctx_admin, decision="D-t", release_pending=1
        )
        await conn.commit()
        # ... and the owner approves the open link afterwards (the batch decision queues another)
        assert (await record_batch_decision(conn, batch_id=batch, approver=world.ctx_a, decision="accept"))[
            "accepted"
        ] == 1
        await conn.commit()
        cur = await conn.execute(
            "SELECT dedupe_key FROM jobs WHERE payload->>'op' = 'apply_batch' AND status = 'queued'"
            " ORDER BY 1"
        )
        assert [r[0] for r in await cur.fetchall()] == [
            f"librarian_apply:{batch}",
            f"librarian_apply:{batch}:promo{event_id}",
        ]
    assert await _statuses(connect) == {q["contradiction"]: "accepted_pending", q["link"]: "approved"}
    before = await _user_state(connect, world)

    await _withdraw(connect, sorted(q.values()))
    provider = make_provider(db_dsn, ScriptedLLM(default=Oracle()), budget_disabled=True)
    worker = make_worker(lib_settings(db_dsn, librarian_role="assistant"), provider, connect)
    await worker.drain()  # both apply jobs run ...
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'resolved'->>'outcome', payload->'request'->'approved' FROM events"
            " WHERE kind = 'librarian' AND payload->'request'->>'op' = 'apply_batch' ORDER BY event_id"
        )
        assert await cur.fetchall() == [("not_approved", [])] * 2  # ... and find nothing to apply
    assert await worker.maybe_release(force=True) == 0  # the sweeper
    async with await connect() as conn:  # a project-scope revision's release_pending job
        spec = release_job(10**9, world.main_id, [old.logical_id, new.logical_id])
        await enqueue(conn, project_id=world.main_id, trigger_device_id=world.dev_a, specs=[spec])
        await conn.commit()
    await worker.drain()
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->'released' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'release_pending'"
        )
        assert await cur.fetchall() == [([],)]
        # a second promotion: nothing pending, so no --release-pending is needed and no job is queued
        assert await promotion_release(conn, "assistant", None) == {}
        event_id = await record_role_decision(
            conn, role="assistant", decided_by=world.ctx_admin, decision="D-t2"
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT payload->'resolved' ? 'jobs' FROM events WHERE event_id = %s", (event_id,)
        )
        assert (await cur.fetchone())[0] is False
    await worker.drain()
    await provider.aclose()
    for qid in q.values():  # memory.answer (open only) refuses
        err = await _err(connect, world.ctx_a, _args(qid, "accept"))
        assert err.code == "E_VERSION_CONFLICT" and err.details["status"] == "withdrawn"
    async with await connect() as conn:  # the expiry skips terminal rows
        await conn.execute("UPDATE librarian_questions SET expires_at = now() - interval '1 day'")
        assert await expire_due(conn) == 0
        await conn.rollback()
    assert await _statuses(connect) == dict.fromkeys(q.values(), "withdrawn")
    assert await _user_state(connect, world) == before
    async with await connect() as conn:
        assert await count(conn, "links") == 0
    await embed(connect, embedder)
    await _replay_identical(connect)


# --------------------------------------------------------------------------- refusals
async def _row(conn: psycopg.AsyncConnection, project_id: int, status: str, batch: str | None = None) -> str:
    """A question row (refusal fixtures: only the columns withdraw reads matter)."""
    cur = await conn.execute(
        "INSERT INTO events (project_id, device_id, client, request_id, kind, payload, payload_sha256,"
        " occurred_at) VALUES (%s, 1, 't', gen_random_uuid(), 'librarian', '{}', 'x', now())"
        " RETURNING event_id",
        (project_id,),
    )
    (event_id,) = await cur.fetchone()
    qid = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO librarian_questions (question_id, job_key, batch_id, project_id, project_ids, kind,"
        " subject_clues, subject_version_ids, proposal, status, created_at, source_event_id, expires_at)"
        " VALUES (%s, 'k', %s, %s, ARRAY[%s]::bigint[], 'link', '{}', '{}', '{}', %s, now(), %s,"
        " now() + interval '30 days')",
        (qid, batch or str(uuid.uuid4()), project_id, project_id, status, event_id),
    )
    return qid


async def test_every_refusal_writes_nothing(connect, world: World) -> None:  # noqa: ANN001
    from hlmemo.librarian.roles import apply_job

    async with await connect() as conn:
        ok = await _row(conn, world.main_id, "accepted_pending")
        other = await _row(conn, world.other_id, "accepted_pending")
        applied = await _row(conn, world.main_id, "applied")
        rejected = await _row(conn, world.main_id, "rejected")
        batch = str(uuid.uuid4())
        busy = await _row(conn, world.main_id, "approved", batch)
        spec = apply_job(batch, world.main_id, {}, ":inflight")
        await conn.execute(  # an apply job of that batch, leased and running elsewhere
            "INSERT INTO jobs (kind, dedupe_key, payload, status, lease_token, lease_until, priority)"
            " VALUES ('librarian_write', %s, %s, 'running', gen_random_uuid(), now() + interval '1 hour', 4)",
            (spec["dedupe_key"], Jsonb(spec["payload"])),
        )
        await conn.commit()
    unknown = str(uuid.uuid4())
    before = await _snapshot(connect)

    err = await _refused(connect, [ok, other, applied, rejected, busy, unknown])
    assert err.code == "E_VERSION_CONFLICT" and "nothing was written" in err.message
    assert err.details["refused"] == {
        "other_project": [other],
        "applied": [applied],
        "not_pending": {"rejected": [rejected]},
        "running_apply": [busy],
        "unknown": [unknown],
    }
    assert await _snapshot(connect) == before  # the valid id was NOT withdrawn either
    for ids in ([other], [applied], [rejected], [busy], [unknown]):  # each alone, too
        assert (await _refused(connect, ids)).code == "E_VERSION_CONFLICT"
    err = await _refused(connect, [ok, "not-a-uuid"])
    assert err.code == "E_INVALID_ARG" and err.details["malformed"] == ["not-a-uuid"]
    assert (await _refused(connect, [" ", ""])).code == "E_INVALID_ARG"  # no ids
    assert (await _refused(connect, [ok], reason="  ")).code == "E_INVALID_ARG"
    assert (await _refused(connect, [ok], by=world.ctx_a)).code == "E_FORBIDDEN"  # operator only
    assert (await _refused(connect, [ok], project="no-such")).code == "E_NOT_FOUND"
    assert (await _refused(connect, [other], project=OTHER, resolved_by_link=10**9)).details["refused"] == {
        "link_not_live": [other]
    }
    assert await _snapshot(connect) == before

    await _withdraw(connect, [ok, ok.upper()])  # duplicates collapse; the valid one alone goes through
    err = await _refused(connect, [ok])  # a second withdraw of the same question
    assert err.details["refused"] == {"not_pending": {"withdrawn": [ok]}}


async def test_resolved_by_link_must_join_the_subjects(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    old, new, q = await _two_questions(db_dsn, connect, world, embedder)
    # a live link between the contradiction's subjects (NEW revised with it) and one that is not
    (rev,) = await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                *NEW,
                logical_id=new.logical_id,
                expected_version_id=new.version_id,
                links=[{"rel": "relates_to", "target": old.logical_id}],
            )
        ],
    )
    unrelated = item("Office plants", "The office plants are watered on Mondays.")
    (other,) = await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [{**unrelated, "links": [{"rel": "relates_to", "target": old.logical_id}]}],
    )
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT src_logical_id, link_id FROM links WHERE superseded_at = 'infinity'"
            " AND rel = 'relates_to' ORDER BY link_id"
        )
        links = dict(await cur.fetchall())
        await conn.rollback()
    good, bad = links[new.logical_id], links[other.logical_id]
    before = await _snapshot(connect)
    err = await _refused(connect, [q["contradiction"]], resolved_by_link=bad)
    assert err.details["refused"] == {"link_mismatch": [q["contradiction"]]}
    err = await _refused(connect, sorted(q.values()), resolved_by_link=good)  # the link joins only one
    assert err.details["refused"] == {"link_mismatch": [q["link"]]}
    assert await _snapshot(connect) == before
    res = await _withdraw(connect, [q["contradiction"]], resolved_by_link=good)
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->'resolved_by_link' FROM events WHERE event_id = %s",
            (res["event_id"],),
        )
        assert await cur.fetchone() == (good,)
        cur = await conn.execute(
            "SELECT (answer->>'resolved_by_link')::bigint FROM librarian_questions WHERE question_id = %s",
            (q["contradiction"],),
        )
        assert await cur.fetchone() == (good,)
        await conn.rollback()
    assert rev.version_id != new.version_id  # the revision itself was never a reason to refuse


# --------------------------------------------------------------------------- locks
async def test_withdraw_waits_for_a_promotion_and_for_an_apply_in_flight(connect, world: World) -> None:  # noqa: ANN001
    async with await connect() as conn:
        qid = await _row(conn, world.main_id, "accepted_pending")
        await conn.commit()
    before = await _snapshot(connect)
    for hold in ("promotion", "apply"):
        async with await connect() as holder:
            if hold == "promotion":
                await lock_role_order(holder, exclusive=True)  # a role decision in flight
            else:  # an apply_batch / memory.answer holding the question row
                await holder.execute(
                    "SELECT 1 FROM librarian_questions WHERE question_id = %s FOR UPDATE", (qid,)
                )
            async with await connect() as conn:
                await conn.execute("SET lock_timeout = '300ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    await withdraw(conn, project=MAIN, question_ids=[qid], reason=REASON, by=_ops_ctx())
                await conn.rollback()
            await holder.rollback()
        assert await _snapshot(connect) == before
    await _withdraw(connect, [qid])  # once they committed, it goes through
    assert (await _statuses(connect))[qid] == "withdrawn"


async def test_withdraw_judges_the_row_an_apply_committed_while_it_waited(connect, world: World) -> None:  # noqa: ANN001
    """The checks run on the LOCKED row: an apply that holds the question and commits ``applied``
    while withdraw waits is seen (refused), never overwritten with ``withdrawn``."""
    import asyncio

    async with await connect() as conn:
        qid = await _row(conn, world.main_id, "accepted_pending")
        await conn.commit()
    async with await connect() as holder:  # an apply in flight: the row is locked, then applied
        await holder.execute("SELECT 1 FROM librarian_questions WHERE question_id = %s FOR UPDATE", (qid,))
        pending = asyncio.create_task(_refused(connect, [qid]))
        await asyncio.sleep(0.3)
        assert not pending.done()  # withdraw waits for the row
        await holder.execute(
            "UPDATE librarian_questions SET status = 'applied' WHERE question_id = %s", (qid,)
        )
        await holder.commit()
    assert (await pending).details["refused"] == {"applied": [qid]}
    assert (await _statuses(connect))[qid] == "applied"


# --------------------------------------------------------------------------- the promotion guard
async def test_promotion_guard_counts_per_project_and_needs_the_exact_count(connect, world: World) -> None:  # noqa: ANN001
    async with await connect() as conn:
        pending = [await _row(conn, world.main_id, "accepted_pending") for _ in range(2)]
        await _row(conn, world.other_id, "approved")  # applied by its batch decision's queued job
        await _row(conn, world.main_id, "open")  # an open question is not released
        stale = await _row(conn, world.main_id, "accepted_pending")
        await conn.execute(  # an expired one is not released either (the apply expires it)
            "UPDATE librarian_questions SET expires_at = now() - interval '1 second' WHERE question_id = %s",
            (stale,),
        )
        widen = await _row(conn, world.main_id, "accepted_pending")
        await conn.execute(
            "UPDATE librarian_questions SET kind = 'widen_scope' WHERE question_id = %s", (widen,)
        )
        await conn.commit()
        assert await promotion_release(conn, "assistant", None) == {
            world.main_id: {"accepted_pending": 2, "approved": 0},
            world.other_id: {"accepted_pending": 0, "approved": 1},
        }
        assert await promotion_release(conn, "observer", None) == {}  # a demotion releases nothing
        await conn.rollback()

    async def decide(role: str, **kw: Any) -> int:
        async with await connect() as conn:
            event_id = await record_role_decision(
                conn, role=role, decided_by=world.ctx_admin, decision="D-g", **kw
            )
            await conn.commit()
        return event_id

    async def refused(role: str, **kw: Any) -> ToolError:
        async with await connect() as conn:
            with pytest.raises(ToolError) as ei:
                await record_role_decision(conn, role=role, decided_by=world.ctx_admin, decision="D-g", **kw)
            await conn.rollback()
        return ei.value

    before = await _snapshot(connect)
    for role in ("assistant", "autonomous"):
        err = await refused(role)
        assert err.code == "E_VERSION_CONFLICT" and "withdraw or verify them first" in err.message
        assert (
            f"{MAIN}: accepted_pending 2, approved 0" in err.message and "--release-pending 3" in err.message
        )
        assert err.details["would_release"] == {
            "total": 3,
            "by_project": {
                MAIN: {"accepted_pending": 2, "approved": 0},
                OTHER: {"accepted_pending": 0, "approved": 1},
            },
        }
        for wrong in (0, 2, 4):
            assert "does not match" in (await refused(role, release_pending=wrong)).message
    assert await _snapshot(connect) == before  # no set_role event
    # a per-project decision while the deployment is observer releases nothing: no guard
    await decide("assistant", project_id=world.main_id)
    await decide("observer")  # a demotion is never guarded ...
    assert "does not match" in (await refused("observer", release_pending=1)).message  # ... N must be 0
    # withdrawing MAIN's pending questions leaves only OTHER's approved one
    await _withdraw(connect, pending)
    assert (await refused("assistant")).details["would_release"]["by_project"] == {
        OTHER: {"accepted_pending": 0, "approved": 1}
    }
    event_id = await decide("assistant", release_pending=1)  # the conscious, counted act
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT payload->'request'->>'release_pending' FROM events WHERE event_id = %s", (event_id,)
        )
        assert await cur.fetchone() == ("1",)
        await conn.rollback()


# --------------------------------------------------------------------------- the ops CLI
def _ops(db_dsn: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HLM_DB_DSN": db_dsn, "HLM_API_PORT": "9"}
    if os.environ.get("PYTHONPATH"):  # a checkout run (worktree): the subprocess imports the same code
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return subprocess.run(
        [sys.executable, "-m", "hlmemo.ops", "librarian", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _details(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(proc.stderr.splitlines()[-1])["details"]


async def test_ops_withdraw_and_role_set_guard(db_dsn, connect, world: World, tmp_path) -> None:  # noqa: ANN001
    async with await connect() as conn:
        a = await _row(conn, world.main_id, "accepted_pending")
        b = await _row(conn, world.main_id, "accepted_pending")
        foreign = await _row(conn, world.other_id, "accepted_pending")
        await conn.commit()
    before = await _snapshot(connect)

    # role set: refused with the counts; --dry-run shows them; a wrong count is refused; nothing recorded
    proc = _ops(db_dsn, "role", "set", "assistant", "--decision", "D-x")
    assert proc.returncode == 1 and "withdraw or verify them first" in proc.stderr
    assert _details(proc)["would_release"]["total"] == 3
    proc = _ops(db_dsn, "role", "set", "assistant", "--decision", "D-x", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["dry_run"] and out["needs"] == "--release-pending 3" and out["apply_jobs"] == 3  # 3 batches
    assert out["would_release"]["by_project"] == {
        MAIN: {"accepted_pending": 2, "approved": 0},
        OTHER: {"accepted_pending": 1, "approved": 0},
    }
    assert (
        _ops(db_dsn, "role", "set", "assistant", "--decision", "D-x", "--release-pending", "2").returncode
        == 1
    )
    assert await _snapshot(connect) == before

    # withdraw: dry-run from a file (comments and blanks skipped), then the real run from stdin
    ids = tmp_path / "ids.txt"
    ids.write_text(f"# D-244 no-conflicts\n{a}\n\n{b}\n")
    proc = _ops(
        db_dsn, "withdraw", "--project", MAIN, "--ids-file", str(ids), "--reason", REASON, "--dry-run"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith(f"DRY-RUN: would withdraw 2 question(s) of {MAIN} (accepted_pending 2)")
    assert await _snapshot(connect) == before
    proc = _ops(db_dsn, "withdraw", "--project", MAIN, "--question-ids", f"{a},{foreign}", "--reason", REASON)
    assert proc.returncode == 1 and "error E_VERSION_CONFLICT" in proc.stderr
    assert _details(proc)["refused"] == {"other_project": [foreign]}
    assert (
        _ops(db_dsn, "withdraw", "--project", MAIN, "--question-ids", "x", "--reason", REASON).returncode == 2
    )
    assert (
        _ops(db_dsn, "withdraw", "--project", MAIN, "--ids-file", "/no/such", "--reason", "r").returncode == 2
    )
    assert await _snapshot(connect) == before
    proc = _ops(
        db_dsn,
        "withdraw",
        "--project",
        MAIN,
        "--ids-file",
        "-",
        "--reason",
        REASON,
        "--json",
        "--owner",
        "cemal",
        stdin=ids.read_text(),
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["withdrawn"] == 2 and res["event_id"] and res["question_ids"] == sorted([a, b])
    assert (await _statuses(connect))[a] == "withdrawn"

    # the guard now counts only OTHER's question; the exact count lets the promotion through
    proc = _ops(db_dsn, "role", "set", "assistant", "--decision", "D-x", "--release-pending", "1")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["release_pending"] == 1
    proc = _ops(db_dsn, "audit", "--project", MAIN, "--status", "withdrawn", "--json")
    assert sorted(p["question_id"] for p in json.loads(proc.stdout)["proposals"]) == sorted([a, b])
