"""D-272: the shadow pipeline runs next to the live write_review and only adds an audit block.

Scripted Oracle (no vendor): the live proposals, questions and links are the same with the shadow on
or off; the shadow calls use their own profile and relate/v3; a broken shadow configuration is
recorded and the job still completes."""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.ops.librarian import shadow_report
from tests.integration._librarian_fixtures import (
    FALLBACK,
    PRIMARY,
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
    timeout_honouring,
)
from tests.integration._read_fixtures import embedder  # noqa: F401 - fixture by import
from tests.integration._w2b_fixtures import Oracle, embed, parse_input, write_items
from tests.integration._write_fixtures import MAIN, World, item, seed_world

pytestmark = pytest.mark.integration

D1 = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D3 = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
OLD = ("Cache setup", "The API cache TTL is 60 seconds.\n- The cache is stored in Redis 7 on the api host.")
NEW = ("Cache TTL changed", "Since June the API cache TTL is 300 seconds.")
REL = {(NEW[0], OLD[0]): ("contradicts", "new", "high")}


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _run(
    db_dsn,  # noqa: ANN001
    connect,  # noqa: ANN001
    embedder,  # noqa: ANN001
    world: World,
    stall: str | None = None,
    **kw: Any,
) -> tuple[ScriptedLLM, list[Any], Any]:
    await write_items(connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D1)])
    await write_items(connect, world.ctx_a, MAIN, [item(*NEW, valid_from=D3)])
    await embed(connect, embedder)
    oracle = Oracle(relations=REL, scope={(NEW[0], OLD[0]): "part"})
    llm = ScriptedLLM(default=oracle)
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    if stall is not None:  # this profile's host never answers (a real socket read timeout)
        provider.transport = timeout_honouring(
            lambda host, body: "stall" if host == f"{stall}.invalid" else oracle(body)
        )
    await make_worker(lib_settings(db_dsn, **kw), provider, connect).drain()
    await provider.aclose()
    async with await connect() as conn:
        cur = await conn.execute("SELECT kind, status, proposal FROM librarian_questions ORDER BY created_at")
        questions = [(k, s, p["actions"]) for k, s, p in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT payload->'request'->'shadow' FROM events WHERE kind = 'librarian'"
            " AND payload->'request'->>'op' = 'write_review' ORDER BY event_id"
        )
        shadows = [r[0] for r in await cur.fetchall()]
    return llm, questions, shadows


def _models(llm: ScriptedLLM, task: str) -> list[str]:
    return [b["model"] for b in llm.requests if parse_input(b)[0] == task]


async def test_shadow_off_adds_nothing(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    llm, questions, shadows = await _run(db_dsn, connect, embedder, world)
    assert [k for k, _s, _a in questions] == ["contradiction"]
    assert all(s is None for s in shadows)
    assert set(_models(llm, "relate")) == {f"stub/{PRIMARY}"}


async def test_shadow_runs_beside_the_live_pipeline_and_only_audits(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    llm, questions, shadows = await _run(
        db_dsn,
        connect,
        embedder,
        world,
        librarian_shadow_profile=FALLBACK,
        librarian_shadow_verify_profile=PRIMARY,
    )
    # the live result: exactly one contradiction question, as with the shadow off
    assert [k for k, _s, _a in questions] == ["contradiction"]
    assert [a["rel"] for a in questions[0][2]] == ["contradicts", "supersedes"]
    # the shadow called its own profile with relate/v3; its verifier is the named profile
    relate_v3 = [
        b for b in llm.requests if "HISTORY is never contradicted" in str(b["messages"][0]["content"])
    ]
    assert relate_v3 and {b["model"] for b in relate_v3} == {f"stub/{FALLBACK}"}
    [block] = [s for s in shadows if s and s.get("proposals")]
    assert block["profile"] == FALLBACK and block["verify_profile"] == PRIMARY
    assert block["relate_version"] == "v3" and "error" not in block
    [prop] = block["proposals"]
    assert (prop["kind"], prop["relation"], prop["supersedes"], prop["scope"]) == (
        "contradiction",
        "contradicts",
        "new",
        "part",
    )
    assert prop["agreed"] is True and prop["auto"] is False
    assert {c["profile"] for c in block["calls"]} == {FALLBACK, PRIMARY}
    async with await connect() as conn:  # nothing of the shadow became a question, link or close
        cur = await conn.execute("SELECT count(*) FROM librarian_questions")
        assert (await cur.fetchone())[0] == 1
        rep = await shadow_report(conn, None, None)
    # the ops report pairs the live and the shadow supersession of the same two items
    assert rep["live_supersessions"] == rep["shadow_supersessions"] == len(rep["both"]) == 1
    assert rep["live_only"] == rep["shadow_only"] == [] and rep["shadow_errors"] == {}
    assert rep["spend"][FALLBACK]["calls"] >= 1


async def test_a_stalled_shadow_model_ends_within_its_wall_and_the_live_result_stands(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    t0 = time.monotonic()
    _llm, questions, shadows = await _run(
        db_dsn,
        connect,
        embedder,
        world,
        stall=FALLBACK,
        librarian_shadow_profile=FALLBACK,
        librarian_shadow_verify_profile=PRIMARY,
        librarian_shadow_wall_s=5.0,
        librarian_verifier="self",  # the live verifier stays on the primary (prod: disjoint profiles)
    )
    took = time.monotonic() - t0
    assert [k for k, _s, _a in questions] == ["contradiction"]
    errs = [s for s in shadows if s]
    assert errs and all(s.get("error") for s in errs), errs
    # one bounded attempt per job within the 5 s wall (2 jobs), never 5 retries of a read timeout
    assert took < 40, took


async def test_a_broken_shadow_profile_is_recorded_and_the_job_completes(
    db_dsn, connect, world: World, embedder
) -> None:  # noqa: ANN001
    llm, questions, shadows = await _run(
        db_dsn, connect, embedder, world, librarian_shadow_profile="no-such-profile-d272"
    )
    assert [k for k, _s, _a in questions] == ["contradiction"]
    errs = [s for s in shadows if s]
    assert errs and all(s["profile"] == "no-such-profile-d272" and s["error"] for s in errs)


async def test_the_shadow_stops_at_its_day_cap(db_dsn, connect, world: World, embedder) -> None:  # noqa: ANN001
    llm, questions, shadows = await _run(
        db_dsn, connect, embedder, world, librarian_shadow_profile=FALLBACK, librarian_shadow_day_usd=0.0
    )
    assert [k for k, _s, _a in questions] == ["contradiction"]
    blocks = [s for s in shadows if s]
    assert blocks and all(s == {"profile": FALLBACK, "skipped": "day_cap"} for s in blocks)
    assert not [
        b for b in llm.requests if "HISTORY is never contradicted" in str(b["messages"][0]["content"])
    ]
