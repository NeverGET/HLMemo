"""``memory.ask`` gates (D-136): scope isolation, abstention, no mutation, budget and spend guard,
the completeness pass, D-062 (detach + re-check), and the Memory Map L2 summary task.

The model is a scripted fake (``_ask_fixtures.FakeResearcher`` behind ``ScriptedLLM``): every
provider request body is recorded, so the scope tests assert on exactly what would have left the
host. The world (``_ask_fixtures.seed_world``) is written through the production write path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import memory_map as mm
from hlmemo.core import research_service as rsv
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps
from hlmemo.db import read_queries as rq_mod
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import map_summary as ms
from hlmemo.librarian.tasks import research as rs
from hlmemo.server.app import create_app
from tests.integration._ask_fixtures import (
    MAIN,
    SECRETS,
    SHARED,
    FakeResearcher,
    request_job,
    seed_world,
)
from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
from tests.integration._mcp_fixtures import ADMIN_TOKEN, call_tool_raw, mcp_rpc, trusted_device

pytestmark = pytest.mark.integration

METER = Meter()
#: tables memory.ask may write: the spend guard's ledger only
LEDGER_TABLES = {"llm_calls", "llm_budget", "llm_reservations", "llm_lineage_calls"}
SNAPSHOT_TABLES = (
    "events",
    "memory_versions",
    "chunks",
    "embeddings",
    "links",
    "jobs",
    "code_refs",
    "version_signals",
    "librarian_questions",
    "librarian_batches",
    "memory_map_summaries",
    "projects",
    "devices",
    "device_project_grants",
    *sorted(LEDGER_TABLES),
)


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_world(connect, deps.embedder)


def ask_settings(db_dsn: str, **kw: Any):  # noqa: ANN201
    base = {
        "db_dsn": db_dsn,
        "librarian_enabled": True,
        "llm_mode": "live",
        "llm_budget_disabled": True,
        "research_enabled": True,
        "map_summary_enabled": False,
        # stub profiles are priced at $1/$2 per 1M tokens: the per-question guard gets its own test
        "research_max_usd": 1.0,
    }
    return get_settings(**{**base, **kw})


def make_researcher(db_dsn: str, llm: ScriptedLLM, **settings_kw: Any) -> rs.Researcher:
    return rs.Researcher(
        ask_settings(db_dsn, **settings_kw), chain=stub_chain(fallback=False), transport=llm.transport
    )


async def ask(connect, world, deps, researcher, question: str, **args: Any) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        await conn.commit()  # a direct caller passes an IDLE connection (D-062)
        try:
            return await rsv.ask(
                conn,
                args.pop("ctx", world.ctx_reader),
                {"question": question, "project": MAIN, **args},
                deps=deps,
                researcher=researcher,
                settings=researcher.settings,
            )
        finally:
            await conn.rollback()


def handle_re(vid: int) -> re.Pattern[str]:
    return re.compile(rf"\bv{vid}(\.\d+)?\b")


def sent_text(llm: ScriptedLLM) -> str:
    return "\n".join(m["content"] for body in llm.requests for m in body["messages"])


def assert_no_secret(text: str, world) -> None:  # noqa: ANN001
    for name, marker in SECRETS.items():
        assert marker not in text, f"{name} marker leaked"
    for name, vid in world.secret_versions.items():
        assert not handle_re(vid).search(text), f"{name} handle v{vid} leaked"


async def snapshot(connect) -> dict[str, str]:  # noqa: ANN001
    out = {}
    async with await connect() as conn:
        for t in SNAPSHOT_TABLES:
            cur = await conn.execute(
                f"SELECT md5(COALESCE(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {t} t"
            )
            out[t] = (await cur.fetchone())[0]
        await conn.commit()
    return out


# --------------------------------------------------------------------------- the answer contract
async def test_ask_answers_with_verified_quotes_and_completeness_pass(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=["1.2 s"], check_adds=["1,6 s"])
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["answer"]
    assert out["meta"]["steps"] == ["plan", "answer", "check", "verify"] and out["meta"]["calls"] == 4
    assert 1 <= len(out["primary"]) <= 3 and len(out["related"]) <= 5
    d004 = world.versions["D-004"]
    assert handle_re(d004).fullmatch(out["primary"][0]["handle"])
    assert "1.2 s" in out["primary"][0]["quote"]
    assert out["primary"][0]["path"] == "docs/decisions/DECISIONS.md#D-004"
    # the completeness pass added the earlier value, with its own verified quote
    quotes = [p["quote"] for p in out["primary"]]
    assert "1,6 s" in out["answer"] or any("1,6 s" in q for q in quotes)
    assert out["meta"]["queries"][0] == "What is the current retrieval p95 target and what was it before?"
    assert out["meta"]["cost_usd"] > 0 and out["meta"]["attempts"] == 4
    # the claims: each with verbatim support, the answer built from them
    assert out["claims"] and all(1 <= len(c["support"]) <= 3 for c in out["claims"])
    assert {p["handle"] for p in out["primary"]} <= {s["handle"] for c in out["claims"] for s in c["support"]}
    assert out["budget"]["used"] <= out["budget"]["limit"] == rsv.DEFAULT_BUDGET
    assert METER.count(out) == out["budget"]["used"]
    # every request is JOB-tagged, the map rides only on plan
    jobs = [request_job(b)[0] for b in llm.requests]
    assert jobs == ["plan", "answer", "check", "verify"]
    verify = llm.requests[3]["messages"][1]["content"]
    assert "JOB: verify" in verify and '"excerpts"' not in verify  # the self-check sees only quotes
    assert "MEMORY MAP of project ask-main" in llm.requests[0]["messages"][1]["content"]
    assert "MEMORY MAP" not in llm.requests[1]["messages"][1]["content"]


# --------------------------------------------------------------------------- scope isolation
async def test_ask_scope_isolation_map_prompts_and_answer(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """An out-of-scope project, a device-scoped, a class-scoped, an ungranted co-owned and a
    policy-off item never appear in the map, in any provider request or in the answer, even when
    the model searches for their markers and asks to drill / cite their handles."""
    secret_handles = [f"v{v}.0" for v in world.secret_versions.values()] + [
        f"v{v}" for v in world.secret_versions.values()
    ]
    fake = FakeResearcher(
        facts=["1.2 s", *SECRETS.values()],
        queries=[*SECRETS.values()][:4],
        sections=secret_handles[:6],
        extra_primary=secret_handles[:3],
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(
            connect,
            world,
            deps,
            r,
            f"What do {SECRETS['device']} and {SECRETS['shared']} mean? And the p95 target?",
        )
    finally:
        await r.aclose()
    # the question itself is the caller's own text (D-097): strip it before scanning the requests
    sent = sent_text(llm).replace(json.dumps(out["meta"]["queries"][0], ensure_ascii=False)[1:-1], "")
    for q in fake.queries:  # the planner's queries echo in the refine prompt only; never content
        sent = sent.replace(q, "")
    assert_no_secret(sent, world)
    plan_map = llm.requests[0]["messages"][1]["content"]
    for title in (
        "Reader private note",
        "Work laptop note",
        "Shared note",
        "Policy-off note",
        "Other project note",
    ):
        assert title not in plan_map
    shown = {h["handle"] for h in (*out["primary"], *out["related"])}
    for vid in world.secret_versions.values():
        assert not any(handle_re(vid).fullmatch(h) for h in shown)
    visible = json.dumps({k: v for k, v in out.items() if k != "meta"}, ensure_ascii=False)
    assert_no_secret(visible, world)


async def test_ask_map_is_the_callers_view(connect, world, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        entries = await mm.load_entries(conn, view)
        await conn.commit()
    vids = {v.version_id for v in view}
    assert not vids & set(world.secret_versions.values())
    m = mm.build_map(view, entries, {}, budget_tokens=6000, project=MAIN)
    assert_no_secret(m.text, world)
    assert "[docs/decisions/]" in m.text and "DECISIONS.md (4 items)" in m.text
    assert f"v{world.versions['status']}" in m.text and m.tokens <= 6000


async def test_ask_forbidden_project_and_default_project(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    r = make_researcher(db_dsn, ScriptedLLM(default=FakeResearcher(facts=["1.2 s"])))
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "anything", project=SHARED)
        assert exc.value.code == "E_FORBIDDEN_PROJECT"
        with pytest.raises(ToolError) as exc:  # the reader reads 3 projects: it must name one
            async with await connect() as conn:
                await conn.commit()
                await rsv.ask(
                    conn, world.ctx_reader, {"question": "x"}, deps=deps, researcher=r, settings=r.settings
                )
        assert exc.value.code == "E_INVALID_ARG"
        with pytest.raises(ToolError) as exc:  # D-062: never inside a caller's open transaction
            async with await connect() as conn:  # the fixture's SET left a transaction open
                args = {"question": "x", "project": MAIN}
                await rsv.ask(conn, world.ctx_reader, args, deps=deps, researcher=r, settings=r.settings)
        assert exc.value.details["reason"] == "no_detach"
    finally:
        await r.aclose()


# --------------------------------------------------------------------------- abstention
async def test_ask_abstains_when_nothing_relevant(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=[], abstain=True)
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "Which colour is the office coffee machine?")
    finally:
        await r.aclose()
    assert out["abstained"] is True and out["answer"] == "" and out["primary"] == []
    assert out["confidence"] == "low" and out["meta"]["abstain_reason"] == "no_evidence"
    assert out["meta"]["calls"] <= rs.MAX_CALLS and "check" not in out["meta"]["steps"]
    assert out["meta"]["steps"][:3] == ["plan", "answer", "refine"]


async def test_ask_unverifiable_answer_is_an_abstention(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """A model that answers with a quote no excerpt contains (and a number no source states) never
    reaches the caller as an answer (guard)."""

    def liar(body: dict[str, Any]) -> dict[str, Any]:
        job, inp = request_job(body)
        if job in ("plan", "refine"):
            return {"queries": ["latency"], "sections": []}
        ex = (inp.get("excerpts") or [{"id": "v1.0"}])[0]
        return {
            "status": "answered",
            "answer": "The p95 target is 0.4 s.",
            "claims": [
                {
                    "text": "The p95 target is 0.4 s.",
                    "support": [{"id": ex["id"], "quote": "the p95 target is 0.4 s on the VPS"}],
                }
            ],
            "related": [],
            "confidence": "high",
        }

    llm = ScriptedLLM(default=liar)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the p95 target?")
    finally:
        await r.aclose()
    assert out["abstained"] is True and out["meta"]["abstain_reason"] == "guard" and out["primary"] == []


# --------------------------------------------------------------------------- no mutation
async def test_ask_writes_no_event_or_mutation(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    before = await snapshot(connect)
    llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"], check_adds=["1,6 s"]))
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
        assert out["abstained"] is False
        await ask(connect, world, deps, r, "What is the colour of the moon?")
    finally:
        await r.aclose()
    after = await snapshot(connect)
    changed = {t for t in SNAPSHOT_TABLES if before[t] != after[t]}
    assert changed <= LEDGER_TABLES, changed  # no event, version, access touch, job or map cache row
    assert "llm_calls" in changed  # the spend guard's ledger did record the calls


# --------------------------------------------------------------------------- budget + spend guard
async def test_ask_spend_guard_refuses_before_any_request(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"]))
    r = make_researcher(
        db_dsn,
        llm,
        llm_budget_disabled=False,
        llm_budget_hour_usd=0.0,
        llm_budget_day_usd=0.0,
        llm_budget_month_usd=0.0,
    )
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert exc.value.code == "E_UNAVAILABLE" and exc.value.details["reason"] == "budget"
    assert llm.calls == 0  # refused at the reservation: nothing was sent


async def test_ask_attempts_are_capped_per_question(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default="not json at all")
    r = make_researcher(db_dsn, llm)
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert exc.value.details["reason"] == "schema_fail"
    assert llm.calls <= rs.MAX_ATTEMPTS  # plan (2 schema attempts) + answer (2): never beyond the cap


async def test_ask_token_budget_is_exact(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=["1.2 s", "1,6 s", "6k tokens", "HLM_RESEARCH_ENABLED"])
    for budget in (400, 700, 2000):
        r = make_researcher(db_dsn, ScriptedLLM(default=fake))
        try:
            try:
                out = await ask(
                    connect, world, deps, r, "Summarise the decisions D-001 to D-004", token_budget=budget
                )
            except ToolError as exc:
                assert exc.code == "E_BUDGET_TOO_SMALL" and exc.details["min"] > budget
                continue
        finally:
            await r.aclose()
        assert out["budget"]["used"] == METER.count(out) <= budget


async def test_ask_disabled_and_busy(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    off = rs.Researcher(ask_settings(db_dsn, research_enabled=False), chain=stub_chain(fallback=False))
    with pytest.raises(ToolError) as exc:
        await ask(connect, world, deps, off, "x")
    assert exc.value.details["reason"] == "disabled"
    r = make_researcher(db_dsn, ScriptedLLM(default=FakeResearcher(facts=["1.2 s"])))
    r.in_flight = rs.MAX_IN_FLIGHT
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "x")
        assert exc.value.details["reason"] == "busy"
    finally:
        r.in_flight = 0
        await r.aclose()


# --------------------------------------------------------------------------- D-062: authority
async def test_ask_revoked_device_mid_request_is_e_auth(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=["1.2 s"])
    revoked: list[bool] = []

    async def revoke(body: dict[str, Any]) -> None:
        if not revoked and request_job(body)[0] == "plan":
            revoked.append(True)
            async with await connect() as conn:
                await conn.execute(
                    "UPDATE devices SET status = 'revoked' WHERE device_id = %s", (world.reader_id,)
                )
                await conn.commit()

    llm = ScriptedLLM(default=fake, on_request=revoke)
    r = make_researcher(db_dsn, llm)
    try:
        with pytest.raises(Exception) as exc:
            await ask(connect, world, deps, r, "What is the retrieval p95 target?")
        assert getattr(exc.value, "code", None) == "E_AUTH"
        assert [request_job(b)[0] for b in llm.requests] == ["plan"]  # nothing sent after the revoke
    finally:
        async with await connect() as conn:
            await conn.execute(
                "UPDATE devices SET status = 'trusted' WHERE device_id = %s", (world.reader_id,)
            )
            await conn.commit()
        await r.aclose()


async def test_ask_grant_lost_after_send_withholds_everything(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """Consult 79 #1: an item whose text already reached the provider becomes unreadable (its
    co-owner grant is revoked) — the next call is not sent and the question fails; its text never
    reaches the caller."""
    shared_pid = world.projects[SHARED]
    async with await connect() as conn:
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'read', 1)",
            (world.reader_id, shared_pid),
        )
        await conn.commit()
    ctx = world.ctx_reader
    from dataclasses import replace

    from hlmemo.auth.context import Role

    ctx2 = replace(ctx, grants={**ctx.grants, shared_pid: Role.READ})
    fake = FakeResearcher(facts=[SECRETS["shared"]], queries=[SECRETS["shared"], "shared note"])
    revoked: list[bool] = []

    async def revoke_after_answer(body: dict[str, Any]) -> None:
        if not revoked and request_job(body)[0] == "answer":
            revoked.append(True)
            async with await connect() as conn:
                await conn.execute(
                    "UPDATE device_project_grants SET revoked_at = now()"
                    " WHERE device_id = %s AND project_id = %s",
                    (world.reader_id, shared_pid),
                )
                await conn.commit()

    llm = ScriptedLLM(default=fake, on_request=revoke_after_answer)
    r = make_researcher(db_dsn, llm)
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "What is the shared note about?", ctx=ctx2)
        assert exc.value.details["reason"] == "authority_changed"
        jobs = [request_job(b)[0] for b in llm.requests]
        assert jobs == ["plan", "answer"], jobs  # the completeness pass was never sent
        assert SECRETS["shared"] in json.dumps(llm.requests[1], ensure_ascii=False)  # it WAS readable then
    finally:
        async with await connect() as conn:
            await conn.execute(
                "DELETE FROM device_project_grants WHERE device_id = %s AND project_id = %s",
                (world.reader_id, shared_pid),
            )
            await conn.commit()
        await r.aclose()


async def test_ask_superseded_mid_request_never_rides_into_later_prompts(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """Own review: an excerpt superseded between the answer and the completeness pass is excluded
    from every later prompt — also from the draft's claims and quotes — and never cited."""
    from hlmemo.core.write_service import default_deps, write
    from hlmemo.worker.main import drain

    body = "D-777 | 2026-09-25 | ACCEPTED | The cache TTL is 45 s for the map cache of each project."
    item = {
        "kind": "fact",
        "title": "D-777 · ACCEPTED: map cache TTL · docs/decisions/DECISIONS.md",
        "body": body,
        "source": {"system": "markdown", "path": "docs/decisions/DECISIONS.md#D-777", "sha256": "7" * 64},
    }
    async with await connect() as conn:
        ack = await write(
            conn,
            world.ctx_loader,
            {"project": MAIN, "request_id": str(uuid.uuid4()), "client": "pytest-ask/0", "items": [item]},
            deps=default_deps(),
        )
        await conn.commit()
    vid, lid = ack.versions[0].version_id, ack.versions[0].logical_id
    await drain(connect, deps.embedder)
    revised: list[bool] = []

    async def supersede_after_answer(body_: dict[str, Any]) -> None:
        if not revised and request_job(body_)[0] == "answer":
            revised.append(True)
            async with await connect() as conn:
                await write(
                    conn,
                    world.ctx_loader,
                    {
                        "project": MAIN,
                        "request_id": str(uuid.uuid4()),
                        "client": "pytest-ask/0",
                        "items": [
                            {
                                **item,
                                "logical_id": lid,
                                "expected_version_id": vid,
                                "body": body.replace("45 s", "90 s"),
                            }
                        ],
                    },
                    deps=default_deps(),
                )
                await conn.commit()

    llm = ScriptedLLM(
        default=FakeResearcher(facts=["45 s"], queries=["map cache TTL", "D-777"]),
        on_request=supersede_after_answer,
    )
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the map cache TTL (D-777)?")
    finally:
        await r.aclose()
    jobs = [request_job(b)[0] for b in llm.requests]
    assert jobs[:2] == ["plan", "answer"] and revised
    answer_at = jobs.index("answer")
    for b in llm.requests[answer_at + 1 :]:
        assert "45 s for the map cache" not in json.dumps(b, ensure_ascii=False)
    cited = [h["handle"] for h in (*out["primary"], *out["related"])]
    cited += [s["handle"] for c in out["claims"] for s in c["support"]]
    assert not any(handle_re(vid).fullmatch(h) for h in cited)
    assert "45 s" not in out["answer"]


async def test_ask_superseded_then_policy_off_is_not_masked(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """Review 80 #1 (HIGH): a sent source that is superseded AND whose co-owner project turns
    policy.librarian off must count as an authority loss (not merely "not current"): nothing more
    is sent and the question fails."""
    from hlmemo.core.write_service import default_deps, write
    from hlmemo.worker.main import drain
    from tests.integration._ask_fixtures import OTHER

    body = "D-778 | 2026-09-25 | ACCEPTED | The shared quota is 17 slots for both projects."
    item = {
        "kind": "fact",
        "title": "D-778 · ACCEPTED: shared quota",
        "body": body,
        "project_ids": [MAIN, OTHER],
    }
    async with await connect() as conn:
        ack = await write(
            conn,
            world.ctx_loader,
            {"project": MAIN, "request_id": str(uuid.uuid4()), "client": "pytest-ask/0", "items": [item]},
            deps=default_deps(),
        )
        await conn.commit()
    vid, lid = ack.versions[0].version_id, ack.versions[0].logical_id
    await drain(connect, deps.embedder)
    done: list[bool] = []
    policy_off = json.dumps({"librarian": "off"})

    async def supersede_and_close(body_: dict[str, Any]) -> None:
        if not done and request_job(body_)[0] == "answer":
            done.append(True)
            revision = {**item, "logical_id": lid, "expected_version_id": vid, "body": body + " Revised."}
            async with await connect() as conn:
                await write(
                    conn,
                    world.ctx_loader,
                    {
                        "project": MAIN,
                        "request_id": str(uuid.uuid4()),
                        "client": "pytest-ask/0",
                        "items": [revision],
                    },
                    deps=default_deps(),
                )
                await conn.execute(
                    "UPDATE projects SET policy = policy || %s::jsonb WHERE slug = %s", (policy_off, OTHER)
                )
                await conn.commit()

    fake = FakeResearcher(facts=["17 slots"], queries=["shared quota slots"])
    llm = ScriptedLLM(default=fake, on_request=supersede_and_close)
    r = make_researcher(db_dsn, llm)
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "What is the shared quota (D-778)?")
        assert exc.value.details["reason"] == "authority_changed"
        assert [request_job(b)[0] for b in llm.requests] == ["plan", "answer"]  # nothing after
    finally:
        async with await connect() as conn:
            await conn.execute("UPDATE projects SET policy = policy - 'librarian' WHERE slug = %s", (OTHER,))
            await conn.commit()
        await r.aclose()


async def test_ask_no_transaction_is_open_during_any_provider_call(
    connect, world, deps, db_dsn, monkeypatch
) -> None:  # noqa: ANN001
    """Review 80 #2 (HIGH) / D-062: while ANY provider request is in flight, no connection of this
    database is inside a transaction (no lock, no device FOR SHARE held across an LLM call). Each
    internal search is slowed INSIDE its transaction, so an overlap could not hide in timing."""
    from hlmemo.core import read_service

    original = read_service.query_parts

    async def slow_query_parts(*a: Any, **kw: Any) -> Any:
        out = await original(*a, **kw)
        await asyncio.sleep(0.3)
        return out

    monkeypatch.setattr(read_service, "query_parts", slow_query_parts)
    seen: list[tuple[str, list[Any]]] = []

    async def probe(body_: dict[str, Any]) -> None:
        async with await connect() as conn:
            cur = await conn.execute(
                "SELECT state, left(query, 60) FROM pg_stat_activity WHERE datname = current_database()"
                " AND pid <> pg_backend_pid() AND state LIKE %s",
                ("idle in transaction%",),
            )
            seen.append((request_job(body_)[0], await cur.fetchall()))
            await conn.rollback()

    llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"], check_adds=["1,6 s"]), on_request=probe)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target and what was it?")
    finally:
        await r.aclose()
    assert not out["abstained"] and [j for j, _ in seen] == ["plan", "answer", "check", "verify"]
    assert all(rows == [] for _j, rows in seen), seen


# --------------------------------------------------------------------------- addendum 5 (safety)
def cheap_researcher(db_dsn: str, llm: Any, **settings_kw: Any) -> rs.Researcher:
    """A researcher on a stub profile priced like a real one ($0.1 / 1M in and out)."""
    from tests.integration._librarian_fixtures import stub_profile

    return rs.Researcher(
        ask_settings(db_dsn, **settings_kw),
        chain=[stub_profile("cheap", price_in="0.1", price_out="0.1")],
        transport=llm.transport if hasattr(llm, "transport") else llm,
    )


async def test_ask_every_call_carries_its_job_max_tokens(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"], check_adds=["1,6 s"]))
    r = make_researcher(db_dsn, llm)
    try:
        await ask(connect, world, deps, r, "What is the retrieval p95 target and what was it?")
    finally:
        await r.aclose()
    seen = {(request_job(b)[0], b["max_tokens"]) for b in llm.requests}
    assert seen == {(job, rs.JOB_MAX_TOKENS[job]) for job in ("plan", "answer", "check", "verify")}


async def test_ask_per_question_budget_stops_with_a_partial_answer(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """An answer call that costs almost the whole per-question budget: the completeness pass and
    the self-check are skipped (their worst case no longer fits), the draft answer is returned and
    meta.flags.budget_stop says why."""
    from tests.integration._librarian_fixtures import chat

    fake = FakeResearcher(facts=["1.2 s"], check_adds=["1,6 s"])

    def route(body: dict[str, Any]) -> Any:
        out = fake(body)
        return chat(out, cost=0.0095) if request_job(body)[0] == "answer" else out

    llm = ScriptedLLM(default=route)
    r = cheap_researcher(db_dsn, llm, research_max_usd=0.01)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert out["meta"]["steps"] == ["plan", "answer"] and out["meta"]["flags"]["budget_stop"] is True
    assert out["abstained"] is False and out["answer"] and out["primary"]  # the partial answer
    assert out["meta"]["cost_usd"] <= 0.01


async def test_ask_runaway_output_is_capped_and_stops_the_question(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """A provider that bills a runaway answer (65,536 output tokens): nothing more is sent after
    it and the question ends with what it has; a plan so large that the answer's worst case no
    longer fits ends in an abstention flagged budget_stop."""
    from tests.integration._librarian_fixtures import chat

    fake = FakeResearcher(facts=["1.2 s"])

    def runaway(body: dict[str, Any]) -> Any:
        out = fake(body)
        if request_job(body)[0] == "answer":
            return chat(out, prompt_tokens=4000, completion_tokens=65536)  # priced from the profile
        return out

    llm = ScriptedLLM(default=runaway)
    r = cheap_researcher(db_dsn, llm, research_max_usd=0.005, research_max_tokens=60_000)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert [request_job(b)[0] for b in llm.requests] == ["plan", "answer"]
    assert all(b["max_tokens"] == rs.JOB_MAX_TOKENS[request_job(b)[0]] for b in llm.requests)
    assert out["meta"]["flags"]["budget_stop"] is True and not out["abstained"]

    def huge_plan(body: dict[str, Any]) -> Any:
        return chat(fake(body), prompt_tokens=90_000, completion_tokens=800)

    llm2 = ScriptedLLM(default=huge_plan)
    r2 = cheap_researcher(db_dsn, llm2, research_max_tokens=60_000)
    try:
        out2 = await ask(connect, world, deps, r2, "What is the retrieval p95 target?")
    finally:
        await r2.aclose()
    assert [request_job(b)[0] for b in llm2.requests] == ["plan"]
    assert out2["abstained"] and out2["meta"]["abstain_reason"] == "budget"


async def test_ask_never_answering_provider_hits_the_question_deadline(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """A provider that never answers (a stream that never ends): every attempt is cut at its share
    of the per-question deadline; the whole question ends within HLM_RESEARCH_TIMEOUT_S."""
    import time as _time

    from tests.integration._librarian_fixtures import timeout_honouring

    seen: list[tuple[str, float]] = []
    transport = timeout_honouring(lambda host, body: "stall", seen)
    r = cheap_researcher(db_dsn, transport, research_timeout_s=4.0)
    t0 = _time.perf_counter()
    try:
        with pytest.raises(ToolError) as exc:
            await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    elapsed = _time.perf_counter() - t0
    assert exc.value.code == "E_UNAVAILABLE" and exc.value.details["reason"] == "timeout"
    assert elapsed < 4.0 + 1.5 and seen  # bounded by the deadline, the requests were attempted


async def test_ask_doc_level_drill_reads_the_best_chunk_of_a_top_document(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """Addendum 6 #3: for a top-ranked multi-chunk document, the chunk that best matches the
    question is drilled too, even when the search hit another chunk of it."""
    status = world.versions["status"]
    async with await connect() as conn:
        spans = await rq_mod.chunk_spans(conn, status)
        await conn.commit()
    targets = {sp.ordinal for sp in spans if "trigram fix" in sp.text}  # chunks overlap
    other = next(sp.ordinal for sp in spans if all(abs(sp.ordinal - t) > 1 for t in targets))
    r = make_researcher(db_dsn, ScriptedLLM(default={}))
    run = rsv._Run(
        conn=None,  # type: ignore[arg-type]
        ctx=world.ctx_reader,
        researcher=r,
        deps=deps,
        settings=r.settings,
        question="Is the trigram fix for G-L3 parked?",
        slug=MAIN,
        project_id=world.projects[MAIN],
        end=0.0,
        reconnect=None,
    )
    async with await connect() as conn:
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        run.view = {v.version_id: v for v in view}
        best = await run._doc_best(conn, [f"v{status}.{other}"], [run.question])
        await conn.commit()
    await r.aclose()
    assert len(best) == 1 and best[0] in {f"v{status}.{t}" for t in targets}


# --------------------------------------------------------------------------- over MCP (detach)
@contextlib.asynccontextmanager
async def ask_app(db_dsn: str, llm: ScriptedLLM, **kw: Any) -> AsyncIterator[httpx.AsyncClient]:
    settings = ask_settings(db_dsn, admin_token=ADMIN_TOKEN, registration_secret=None, **kw)
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        app.state.researcher = rs.Researcher(
            settings, chain=stub_chain(fallback=False), transport=llm.transport
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_ask_over_mcp_outlives_the_request_db_deadline(world, db_dsn) -> None:  # noqa: ANN001
    """Through the real middleware: the handler detaches after the map phase (the request connection
    is released) and extends the request deadline (``detach(hold_s)``), so a loop longer than
    ``request_db_timeout_s`` answers; tools/list advertises memory.ask while it is enabled."""
    fake = FakeResearcher(facts=["1.2 s"])

    def slow(body: dict[str, Any]) -> Any:
        return ("stall", 0.8, fake(body))

    llm = ScriptedLLM(default=slow)
    async with ask_app(db_dsn, llm, request_db_timeout_s=1.5) as client:
        _did, token = await trusted_device(client, "ask-mcp", grants=[{"project": MAIN, "role": "read"}])
        listed = (await mcp_rpc(client, token, "tools/list")).json()["result"]["tools"]
        assert "memory.ask" in {t["name"] for t in listed}
        res = await call_tool_raw(
            client, token, "memory.ask", {"question": "What is the retrieval p95 target?"}
        )
        assert not res.get("isError"), res
        out = json.loads(res["content"][0]["text"])
    assert out["project"] == MAIN  # the device's only project is the default
    assert out["abstained"] is False and out["meta"]["latency_ms"] > 1500


async def test_ask_without_the_hold_hits_the_request_deadline(world, db_dsn, monkeypatch) -> None:  # noqa: ANN001
    """Negative control of the test above: with the hold disabled, the same slow loop is cut by the
    request deadline (so the extension is what makes a long memory.ask possible)."""
    from hlmemo.server import middleware

    monkeypatch.setattr(middleware, "DETACHED_HOLD_MAX_S", 0.0)
    fake = FakeResearcher(facts=["1.2 s"])
    llm = ScriptedLLM(default=lambda body: ("stall", 0.8, fake(body)))
    async with ask_app(db_dsn, llm, request_db_timeout_s=1.5) as client:
        _did, token = await trusted_device(client, "ask-nohold", grants=[{"project": MAIN, "role": "read"}])
        r = await mcp_rpc(
            client, token, "tools/call", {"name": "memory.ask", "arguments": {"question": "p95 target?"}}
        )
        assert r.status_code == 503 and r.json()["code"] == "E_UNAVAILABLE"


async def test_ask_not_listed_when_disabled(world, db_dsn) -> None:  # noqa: ANN001
    async with ask_app(db_dsn, ScriptedLLM(default={}), research_enabled=False) as client:
        _did, token = await trusted_device(client, "ask-off-dev", grants=[{"project": MAIN, "role": "read"}])
        listed = (await mcp_rpc(client, token, "tools/list")).json()["result"]["tools"]
        assert "memory.ask" not in {t["name"] for t in listed}
        res = await call_tool_raw(client, token, "memory.ask", {"question": "x", "project": MAIN})
        assert res["isError"] and json.loads(res["content"][0]["text"])["details"]["reason"] == "disabled"


# --------------------------------------------------------------------------- L2 summaries
def summary_provider(llm: ScriptedLLM) -> Provider:
    return Provider(
        stub_chain(fallback=False),
        mode="live",
        budget=MemoryBudget(Decimal("1")),
        ledger=MemoryLedger(),
        transport=llm.transport,
        timeout_s=5.0,
    )


async def test_map_summaries_are_cached_debounced_and_scoped(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    def summarize(body: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
        titles = "; ".join(i["title"] for i in payload["items"])
        return {"summary": f"Source {payload['source']} holds: {titles}"[:300]}

    llm = ScriptedLLM(default=summarize)
    settings = ask_settings(db_dsn, map_summary_debounce_s=60.0, map_summary_per_cycle=50)

    async def conn_factory():  # noqa: ANN202
        return await connect()

    summ = ms.MapSummarizer(settings, provider=summary_provider(llm), connect=conn_factory)
    try:
        assert await summ.cycle(now=1000.0) == 0  # first sight of every digest: debounced
        assert llm.calls == 0
        written = await summ.cycle(now=1100.0)
        assert written >= 3 and llm.calls == written
        assert await summ.cycle(now=1200.0) == 0  # nothing changed: no scan, no call
    finally:
        await summ.provider.aclose()
    sent = sent_text(llm)
    # device-, class-scoped and policy-off items are never members (never sent)
    for name in ("device", "work", "off"):
        assert SECRETS[name] not in sent
    async with await connect() as conn:
        rows = await (
            await conn.execute("SELECT project_id, source_key, member_ids FROM memory_map_summaries")
        ).fetchall()
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        summaries = await mm.load_summaries(conn, world.projects[MAIN], {v.version_id for v in view})
        entries = await mm.load_entries(conn, view)
        await conn.commit()
    members = {int(m) for r in rows for m in r[2]}
    for name in ("device", "work", "off"):
        assert world.secret_versions[name] not in members
    # the co-owned item IS summarised (its projects are on), but the reader cannot read ask-shared:
    # the cluster's summary is not shown to it
    assert world.secret_versions["shared"] in members
    assert all(world.secret_versions["shared"] not in s[0] for s in summaries.values())
    m = mm.build_map(view, entries, summaries, budget_tokens=6000, project=MAIN)
    assert m.summaries >= 2 and " ~ Source DECISIONS.md holds" in m.text
    assert_no_secret(m.text, world)
    assert "Shared note" not in m.text
    async with await connect() as conn:  # a rebuildable cache: deleting it loses nothing
        await conn.execute("DELETE FROM memory_map_summaries")
        await conn.commit()


async def test_map_summary_refresh_after_change_only(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default={"summary": "A runbook with backup and restore commands."})
    settings = ask_settings(db_dsn, map_summary_debounce_s=0.0, map_summary_per_cycle=50)

    async def conn_factory():  # noqa: ANN202
        return await connect()

    summ = ms.MapSummarizer(settings, provider=summary_provider(llm), connect=conn_factory)
    try:
        first = await summ.cycle()
        assert first >= 3
        n = llm.calls
        summ._last_marker = None  # force a scan: digests unchanged -> nothing is due
        assert await summ.cycle() == 0 and llm.calls == n
        async with await connect() as conn:  # an unrelated source changes -> only its row refreshes
            from hlmemo.core.write_service import default_deps, write

            await write(
                conn,
                world.ctx_loader,
                {
                    "project": MAIN,
                    "request_id": "8c1f5a8e-6f4e-4a70-9d7c-3b0f3f6b2a11",
                    "client": "pytest-ask/0",
                    "items": [
                        {
                            "kind": "doc_chunk",
                            "title": "NOTES · docs/notes/NOTES.md",
                            "body": "A new notes file.",
                            "source": {
                                "system": "markdown",
                                "path": "docs/notes/NOTES.md",
                                "sha256": "0" * 64,
                            },
                        }
                    ],
                },
                deps=default_deps(),
            )
            await conn.commit()
        assert await summ.cycle() == 1 and llm.calls == n + 1
    finally:
        await summ.provider.aclose()
        async with await connect() as conn:
            await conn.execute("DELETE FROM memory_map_summaries")
            await conn.commit()


async def test_ask_self_check_narrows_or_drops_partly_supported_claims(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """The self-check (gate v1 finding b): a claim its quotes support only in part is narrowed to
    what the quotes state, one they do not state is dropped, and the answer is rewritten to the rest."""
    fake = FakeResearcher(
        facts=["1.2 s", "SQLite", "6k tokens"],
        verdicts={1: ("none", ""), 2: ("partial", "The Memory Map budget is about 6k tokens.")},
        verify_answer="The p95 target is 1.2 s. The map budget is about 6k tokens.",
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What are the p95 target, the store and the map budget?")
    finally:
        await r.aclose()
    texts = [c["text"] for c in out["claims"]]
    assert not any("SQLite" in t for t in texts)  # judged not entailed: dropped
    assert "The Memory Map budget is about 6k tokens." in texts  # narrowed to its quote
    assert out["answer"] == "The p95 target is 1.2 s. The map budget is about 6k tokens."
    assert out["confidence"] != "high"  # the self-check changed something


async def test_librarian_worker_runs_map_summaries_in_the_background(connect, world, db_dsn) -> None:  # noqa: ANN001
    from hlmemo.librarian.worker import LibrarianWorker

    llm = ScriptedLLM(default={"summary": "A source of the synthetic project."})

    async def conn_factory():  # noqa: ANN202
        return await connect()

    settings = ask_settings(
        db_dsn, map_summary_enabled=True, map_summary_debounce_s=0.0, map_summary_every_s=3600.0
    )
    worker = LibrarianWorker(settings, provider=summary_provider(llm), connect=conn_factory)
    try:
        assert worker.maybe_map_summaries() is True
        assert worker.maybe_map_summaries() is False  # one cycle at a time, then every_s
        written = await worker._map_task
        assert written >= 3 and llm.calls == written
    finally:
        await worker.stop_map_summaries()
        await worker.provider.aclose()
        async with await connect() as conn:
            await conn.execute("DELETE FROM memory_map_summaries")
            await conn.commit()
    off = LibrarianWorker(
        ask_settings(db_dsn, map_summary_enabled=True, research_enabled=False),
        provider=summary_provider(llm),
        connect=conn_factory,
    )
    assert off.map_summarizer is None and off.maybe_map_summaries() is False  # no memory.ask, no spend
    await off.provider.aclose()


async def test_ask_concurrent_questions_share_nothing(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"]))
    r = make_researcher(db_dsn, llm)
    try:
        outs = await asyncio.gather(*(ask(connect, world, deps, r, f"p95 target? ({i})") for i in range(3)))
    finally:
        await r.aclose()
    assert all(not o["abstained"] for o in outs) and r.in_flight == 0
