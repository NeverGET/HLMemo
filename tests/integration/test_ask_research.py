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
    assert out["meta"]["steps"] == ["plan", "answer", "check"] and out["meta"]["calls"] == 3
    assert 1 <= len(out["primary"]) <= 3 and len(out["related"]) <= 5
    d004 = world.versions["D-004"]
    assert handle_re(d004).fullmatch(out["primary"][0]["handle"])
    assert "1.2 s" in out["primary"][0]["quote"]
    assert out["primary"][0]["path"] == "docs/decisions/DECISIONS.md#D-004"
    # the completeness pass added the earlier value, with its own verified quote
    quotes = [p["quote"] for p in out["primary"]]
    assert "1,6 s" in out["answer"] or any("1,6 s" in q for q in quotes)
    assert out["meta"]["queries"][0] == "What is the current retrieval p95 target and what was it before?"
    assert out["meta"]["cost_usd"] > 0 and out["meta"]["attempts"] == 3
    # the claims: each with verbatim support, the answer built from them
    assert out["claims"] and all(1 <= len(c["support"]) <= 3 for c in out["claims"])
    assert {p["handle"] for p in out["primary"]} <= {s["handle"] for c in out["claims"] for s in c["support"]}
    assert out["budget"]["used"] <= out["budget"]["limit"] == rsv.DEFAULT_BUDGET
    assert METER.count(out) == out["budget"]["used"]
    # every request is JOB-tagged, the map rides only on plan
    jobs = [request_job(b)[0] for b in llm.requests]
    assert jobs == ["plan", "answer", "check"]  # at most 4 sequential steps (addendum 7)
    # D-165 meta.excerpts_shown: the excerpt ids of the last answer step (check), in prompt order
    shown_ids = [e["id"] for e in request_job(llm.requests[2])[1]["excerpts"]]
    assert out["meta"]["excerpts_shown"] == shown_ids and len(shown_ids) <= rsv.MAX_DRILL
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


# --------------------------------------------------------------------------- D-156 cite mode
async def test_ask_cite_mode_writes_then_cites(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-156 V14 (HLM_RESEARCH_ANSWER_MODE=cite): plan -> write -> finish, no check call; each
    sentence is verified against its cited excerpt's full text: a sentence stating a value no source
    states is dropped, handles the model was never shown are ignored."""
    secret = [f"v{v}.0" for v in world.secret_versions.values()][:2]
    fake = FakeResearcher(
        facts=["1.2 s", "1,6 s on the VPS"],
        extra_primary=secret,
        write_extra=[{"text": "The p95 target on the dev replica is 0.4 s."}],
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite")
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["confidence"] == "medium"  # a sentence was dropped
    assert out["meta"]["steps"] == ["plan", "write"] and out["meta"]["calls"] == 2
    assert out["meta"]["answer_mode"] == "cite" and out["meta"]["attempts"] == 2
    flags = out["meta"]["flags"]
    assert (flags["dropped_sentences"], flags["dropped_literal"], flags["uncited"]) == (1, 1, 0)
    assert flags["main_dropped"] is False and flags["dropped_polarity"] == 0
    assert "0.4 s" not in out["answer"] and "1.2 s" in out["answer"] and "1,6 s" in out["answer"]
    d004 = world.versions["D-004"]
    assert handle_re(d004).fullmatch(out["primary"][0]["handle"])
    assert "1.2 s" in out["primary"][0]["quote"]
    assert out["primary"][0]["path"] == "docs/decisions/DECISIONS.md#D-004"
    # the claims are the kept sentences, the answer is exactly them, each shown with its source line
    assert out["answer"] == " ".join(c["text"] for c in out["claims"])
    assert out["claims"] and all(1 <= len(c["support"]) <= 3 for c in out["claims"])
    assert {p["handle"] for p in out["primary"]} <= {s["handle"] for c in out["claims"] for s in c["support"]}
    shown = {h["handle"] for h in (*out["primary"], *out["related"])}
    assert not shown & set(secret) and len(out["related"]) <= 5
    assert_no_secret(json.dumps({k: v for k, v in out.items() if k != "meta"}, ensure_ascii=False), world)
    assert out["budget"]["used"] <= out["budget"]["limit"] and METER.count(out) == out["budget"]["used"]
    assert [request_job(b)[0] for b in llm.requests] == ["plan", "write"]
    system = llm.requests[1]["messages"][0]["content"]
    assert 'JOB "write"' in system and 'JOB "check"' not in system  # research/v2
    assert llm.requests[1]["messages"][1]["content"].startswith("JOB: write\n")


async def test_ask_cite_mode_abstains_and_refines_with_write(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=[], abstain=True)
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite")
    try:
        out = await ask(connect, world, deps, r, "Which colour is the office coffee machine?")
    finally:
        await r.aclose()
    assert out["abstained"] is True and out["answer"] == "" and out["primary"] == [] and out["claims"] == []
    assert out["confidence"] == "low" and out["meta"]["abstain_reason"] == "no_evidence"
    assert out["meta"]["answer_mode"] == "cite" and out["meta"]["calls"] <= rs.MAX_CALLS
    steps = out["meta"]["steps"]
    assert steps[:3] == ["plan", "write", "refine"] and set(steps) <= {"plan", "write", "refine"}
    assert set(fake.jobs) <= {"plan", "write", "refine"}


async def test_ask_cite_mode_unverifiable_sentences_are_a_guarded_abstention(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    def liar(body: dict[str, Any]) -> dict[str, Any]:
        job, inp = request_job(body)
        if job in ("plan", "refine"):
            return {"queries": ["latency"], "sections": []}
        ex = (inp.get("excerpts") or [{"id": "v1.0"}])[0]
        return {
            "status": "answered",
            "sentences": [{"text": "The p95 target is 0.4 s.", "cite": [ex["id"]]}],
            "related": [],
            "confidence": "high",
        }

    llm = ScriptedLLM(default=liar)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite")
    try:
        out = await ask(connect, world, deps, r, "What is the p95 target?")
    finally:
        await r.aclose()
    assert out["abstained"] is True and out["meta"]["abstain_reason"] == "guard" and out["primary"] == []
    assert out["meta"]["flags"]["dropped_literal"] == 1 and out["meta"]["flags"]["main_dropped"] is True


# --------------------------------------------------------------------------- D-162 prose mode
async def test_ask_prose_mode_keeps_free_prose_and_drops_only_fabricated_values(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """D-162 V16 (HLM_RESEARCH_ANSWER_MODE=prose): plan -> prose -> finish, no check call. A sentence
    stating a value no shown excerpt states is dropped; the others are kept and attributed to their
    best source lines; handles the model was never shown are ignored."""
    secret = [f"v{v}.0" for v in world.secret_versions.values()][:2]
    fake = FakeResearcher(
        facts=["1.2 s", "1,6 s on the VPS"],
        extra_primary=secret,
        prose_extra=["The p95 target on the dev replica is 0.7 s."],  # D-187: not 1.6 - 1.2
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose")
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["confidence"] == "medium"  # a sentence was dropped
    assert out["meta"]["steps"] == ["plan", "prose"] and out["meta"]["calls"] == 2
    assert out["meta"]["answer_mode"] == "prose" and out["meta"]["attempts"] == 2
    flags = out["meta"]["flags"]
    assert (flags["dropped_literal"], flags["main_dropped"]) == (1, False) and "polarity_flagged" not in flags
    assert flags["attribution"] == "sources" and "attr_embed" not in flags  # D-165: the default
    assert "0.7 s" not in out["answer"] and "1.2 s" in out["answer"] and "1,6 s" in out["answer"]
    d004 = world.versions["D-004"]
    assert handle_re(d004).fullmatch(out["primary"][0]["handle"])
    assert "1.2 s" in out["primary"][0]["quote"]
    assert out["primary"][0]["path"] == "docs/decisions/DECISIONS.md#D-004"
    # the claims are the kept sentences, the answer is exactly them, each shown with its source lines
    assert out["answer"] == " ".join(c["text"] for c in out["claims"]) and len(out["claims"]) == 2
    assert all(1 <= len(c["support"]) <= rs.PROSE_ATTRIBUTE and "flags" not in c for c in out["claims"])
    assert all(s["quote"] for c in out["claims"] for s in c["support"])
    assert {p["handle"] for p in out["primary"]} <= {s["handle"] for c in out["claims"] for s in c["support"]}
    shown = {h["handle"] for h in (*out["primary"], *out["related"])}
    assert not shown & set(secret) and len(out["related"]) <= 5
    assert_no_secret(json.dumps({k: v for k, v in out.items() if k != "meta"}, ensure_ascii=False), world)
    assert out["budget"]["used"] <= out["budget"]["limit"] and METER.count(out) == out["budget"]["used"]
    assert [request_job(b)[0] for b in llm.requests] == ["plan", "prose"]
    assert llm.requests[1]["max_tokens"] == rs.JOB_MAX_TOKENS["prose"] == 3000
    system = llm.requests[1]["messages"][0]["content"]
    assert 'JOB "prose"' in system and 'JOB "write"' not in system and 'JOB "check"' not in system
    assert llm.requests[1]["messages"][1]["content"].startswith("JOB: prose\n")
    # D-184 without links: no excerpt carries a status, nothing is pulled; chunks carry context
    prose_excerpts = request_job(llm.requests[1])[1]["excerpts"]
    assert all("status" not in e for e in prose_excerpts) and all(e.get("context") for e in prose_excerpts)
    assert (flags["superseded_shown"], flags["superseders_pulled"]) == (0, 0)
    assert out["meta"]["excerpts_shown"] == [e["id"] for e in request_job(llm.requests[1])[1]["excerpts"]]
    assert_no_secret(sent_text(llm), world)


async def test_ask_prose_mode_has_no_polarity_flag(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-165: a sentence that drops the "no" of its best source line is KEPT, attributed to that line,
    without flags (the V16 audit found 6/6 polarity flags false positives); the confidence stays."""
    fake = FakeResearcher(
        facts=["1.2 s", "SQLite was rejected"], prose_extra=["SQLite has concurrent writers."]
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose")
    try:
        out = await ask(connect, world, deps, r, "What is the p95 target, and why was SQLite rejected?")
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["answer"].endswith("SQLite has concurrent writers.")
    last = out["claims"][-1]
    assert last["text"] == "SQLite has concurrent writers." and "flags" not in last
    assert handle_re(world.versions["D-001"]).fullmatch(last["support"][0]["handle"])
    assert "no concurrent writers" in last["support"][0]["quote"]
    assert all("flags" not in c for c in out["claims"])
    assert "polarity_flagged" not in out["meta"]["flags"] and out["meta"]["flags"]["dropped_literal"] == 0
    assert out["confidence"] == "high"  # nothing dropped, nothing flagged


async def test_ask_prose_mode_wide_attribution_embeds_with_the_servers_embedder(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """D-165 HLM_RESEARCH_ATTRIBUTION=wide: the server's own embedder scores the kept sentences
    against the excerpt lines, in one pass, within the budget; no extra provider call."""
    fake = FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"])
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose", research_attribution="wide")
    try:
        out = await ask(connect, world, deps, r, "What is the current retrieval p95 target?")
    finally:
        await r.aclose()
    flags = out["meta"]["flags"]
    assert out["abstained"] is False and out["meta"]["steps"] == ["plan", "prose"]
    assert flags["attribution"] == "wide" and flags["attr_embed"] in ("full", "partial")
    assert flags["attr_embedded"] > 2 and 0 <= flags["attr_embed_ms"] <= 1000 * rs.ATTR_EMBED_S + 500
    assert handle_re(world.versions["D-004"]).fullmatch(out["claims"][0]["support"][0]["handle"])


async def test_ask_prose_mode_llm_attribution_makes_one_attribute_call(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-165 HLM_RESEARCH_ATTRIBUTION=llm: ONE more call (JOB attribute, research/v3) over the kept
    sentences and the excerpts the prose saw; its ids are the support (a sentence it gives [] is
    attributed as sources); ids never shown are ignored."""
    secret = [f"v{v}.0" for v in world.secret_versions.values()][:1]
    d001 = world.versions["D-001"]
    fake = FakeResearcher(
        facts=["1.2 s", "1,6 s on the VPS"],
        prose_extra=["SQLite has concurrent writers."],
        attribute_ids={1: [f"v{d001}.0", *secret], 2: [f"v{d001}.0"], 3: []},
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose", research_attribution="llm")
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["meta"]["steps"] == ["plan", "prose", "attribute"]
    assert out["meta"]["calls"] == 3 and out["meta"]["attempts"] == 3
    flags = out["meta"]["flags"]
    assert flags["attribution"] == "llm" and flags["attr_fallback"] is False and flags["attr_llm_cited"] == 2
    job, inp = request_job(llm.requests[2])
    assert job == "attribute" and llm.requests[2]["max_tokens"] == rs.JOB_MAX_TOKENS["attribute"] == 1500
    assert [x["n"] for x in inp["sentences"]] == [1, 2, 3]
    assert [x["text"] for x in inp["sentences"]] == [c["text"] for c in out["claims"]]
    assert [e["id"] for e in inp["excerpts"]] == out["meta"]["excerpts_shown"]
    assert 'JOB "attribute"' in llm.requests[2]["messages"][0]["content"]
    supports = [[s["handle"] for s in c["support"]] for c in out["claims"]]
    assert supports[0] == [f"v{d001}.0"] and supports[1] == [f"v{d001}.0"]  # its ids, the secret ignored
    assert supports[2] and all(s["quote"] for c in out["claims"] for s in c["support"])
    assert_no_secret(json.dumps({k: v for k, v in out.items() if k != "meta"}, ensure_ascii=False), world)
    assert_no_secret(sent_text(llm), world)


async def test_ask_prose_mode_llm_attribution_falls_back_to_sources(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-165: a failed JOB attribute (the provider refuses it) never fails the answer: it is
    attributed as ``sources`` (meta.flags.attr_fallback)."""
    fake = FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"])

    def model(body: dict[str, Any]) -> Any:
        return 400 if request_job(body)[0] == "attribute" else fake(body)

    llm = ScriptedLLM(default=model)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose", research_attribution="llm")
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["meta"]["steps"] == ["plan", "prose"]  # not counted
    assert "attribute" in [request_job(b)[0] for b in llm.requests]
    flags = out["meta"]["flags"]
    assert flags["attribution"] == "sources" and flags["attr_fallback"] is True
    d004 = world.versions["D-004"]
    assert handle_re(d004).fullmatch(out["primary"][0]["handle"])


async def test_ask_prose_mode_expand_appends_checked_sentences(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-170 HLM_RESEARCH_EXPAND: after the answered prose, ONE JOB expand (the kept sentences
    numbered, the same excerpts); its sentences are appended and pass the same literal check (a value
    no excerpt states is dropped), then attributed like the others."""
    fake = FakeResearcher(
        facts=["1.2 s"],
        expand_add=[
            "It was 1,6 s on the VPS before D-004.",
            "The owner decided it after the R3 release.",
            "On staging the target is 0.3 s.",
        ],
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose", research_expand=True)
    try:
        out = await ask(
            connect, world, deps, r, "What is the retrieval p95 target, what was it before and who decided?"
        )
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["meta"]["steps"] == ["plan", "prose", "expand"]
    assert out["meta"]["calls"] == 3 and out["meta"]["attempts"] == 3
    flags = out["meta"]["flags"]
    assert (flags["expand_added"], flags["expand_dropped"]) == (2, 1)
    assert (
        flags["expand_skipped"] is False and flags["expand_failed"] is False and flags["dropped_literal"] == 1
    )
    assert out["answer"].endswith(
        "It was 1,6 s on the VPS before D-004. The owner decided it after the R3 release."
    )
    assert "0.3 s" not in out["answer"] and len(out["claims"]) == 3
    assert all(c["support"] and all(s["quote"] for s in c["support"]) for c in out["claims"])
    job, inp = request_job(llm.requests[2])
    assert job == "expand" and llm.requests[2]["max_tokens"] == rs.JOB_MAX_TOKENS["expand"] == 1500
    assert [x["n"] for x in inp["answer"]] == [1] and inp["answer"][0]["text"] == out["claims"][0]["text"]
    assert [e["id"] for e in inp["excerpts"]] == out["meta"]["excerpts_shown"]
    assert inp["excerpts"] == request_job(llm.requests[1])[1]["excerpts"]  # what the prose saw
    assert 'JOB "expand"' in llm.requests[2]["messages"][0]["content"]
    assert_no_secret(sent_text(llm), world)


async def test_ask_prose_mode_writer_profile_writes_prose_and_expand(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-171 HLM_RESEARCH_WRITER_PROFILE (the built-in ``openrouter-glm5`` profile file, behind the
    mock transport: nothing leaves the host): the JOBs prose and expand go to the writer profile with
    ITS request options; plan and attribute stay on the task profile; each call is priced by its own
    profile; meta.writer_profile names the writer. D-178: that profile has no JSON mode, so its
    JOBs are the plain-text prose_text / expand_text, parsed into the same objects."""
    fake = FakeResearcher(facts=["1.2 s"], expand_add=["It was 1,6 s on the VPS before D-004."])
    llm = ScriptedLLM(default=fake)
    r = make_researcher(
        db_dsn,
        llm,
        research_answer_mode="prose",
        research_expand=True,
        research_attribution="llm",
        research_writer_profile="openrouter-glm5",
    )
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target and what was it before?")
    finally:
        await r.aclose()
    assert out["abstained"] is False and out["meta"]["writer_profile"] == "openrouter-glm5"
    assert out["meta"]["steps"] == ["plan", "prose", "expand", "attribute"]
    # D-172: a fast writer is used normally
    assert (
        out["meta"]["flags"]["writer_used"] == "openrouter-glm5"
        and out["meta"]["flags"]["writer_timeout"] is False
    )
    sent = [(request_job(b)[0], b["model"], host) for b, host in zip(llm.requests, llm.hosts, strict=True)]
    assert sent == [
        ("plan", "stub/stub-primary", "stub-primary.invalid"),
        ("prose_text", "z-ai/glm-5", "openrouter.ai"),
        ("expand_text", "z-ai/glm-5", "openrouter.ai"),
        ("attribute", "stub/stub-primary", "stub-primary.invalid"),
    ]
    prose = llm.requests[1]
    assert prose["provider"] == {
        "data_collection": "deny",
        "order": ["Z.AI", "Novita"],
        "allow_fallbacks": False,
    }
    assert "response_format" not in prose and "temperature" not in prose and prose["max_tokens"] == 3000
    assert prose["reasoning"] == {"enabled": False}
    assert 'JOB "prose_text"' in prose["messages"][0]["content"]  # the same research/v3 system prompt
    # the text JOB carries the same INPUT as the JSON JOB would (the question and the excerpts)
    assert [e["id"] for e in request_job(prose)[1]["excerpts"]] == out["meta"]["excerpts_shown"]
    # every call priced by ITS profile: 100 in / 20 out tokens each (stub $1/$2, glm-5 $1.00/$3.20 per M)
    stub, glm5 = 100 * 1.0 + 20 * 2.0, 100 * 1.00 + 20 * 3.20
    assert out["meta"]["cost_usd"] == round((2 * stub + 2 * glm5) / 1_000_000, 6)  # 0.000608 (not 0.00056)
    # the text answer, parsed: its sentences kept and attributed like a JSON answer's
    assert "The retrieval p95 target is now 1.2 s" in out["claims"][0]["text"] and len(out["claims"]) == 2
    assert all(c["support"] for c in out["claims"])
    assert "1,6 s" in out["answer"] and out["meta"]["flags"]["expand_added"] == 1
    assert_no_secret(sent_text(llm), world)
    # the default: no writer profile, the task profile writes and is reported
    llm2 = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"]))
    r2 = make_researcher(db_dsn, llm2, research_answer_mode="prose")
    try:
        out2 = await ask(connect, world, deps, r2, "What is the retrieval p95 target?")
    finally:
        await r2.aclose()
    assert out2["meta"]["writer_profile"] == "stub-primary"
    assert {b["model"] for b in llm2.requests} == {"stub/stub-primary"}
    assert (
        out2["meta"]["flags"]["writer_used"] == "stub-primary"
        and out2["meta"]["flags"]["writer_timeout"] is False
    )


async def test_ask_prose_mode_slow_writer_times_out_and_the_task_writes(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-172 HLM_RESEARCH_WRITER_TIMEOUT_S: a writer attempt that stalls past its timeout is cut
    there (not at the call's deadline), charged its worst case, and the task profile writes the
    answer; meta.flags.writer_timeout / writer_used say so."""
    import time

    fake = FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"])

    def model(body: dict[str, Any]) -> Any:
        return ("stall", 5.0, fake(body)) if body["model"] == "z-ai/glm-5" else fake(body)

    llm = ScriptedLLM(default=model)
    r = make_researcher(
        db_dsn,
        llm,
        research_answer_mode="prose",
        research_writer_profile="openrouter-glm5",
        research_writer_timeout_s=0.5,
    )
    t0 = time.perf_counter()
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    assert time.perf_counter() - t0 < 4.0  # the 5 s stall was cut at the writer's 0.5 s
    assert (
        out["abstained"] is False and "1.2 s" in out["answer"] and out["meta"]["steps"] == ["plan", "prose"]
    )
    flags = out["meta"]["flags"]
    assert flags["writer_timeout"] is True and flags["writer_used"] == "stub-primary"
    # D-173: the cut is tail latency, not a breaker failure of the writer
    writer_breaker = r.provider.breaker("openrouter-glm5")
    assert writer_breaker.state == "closed" and writer_breaker.failures == 0
    assert out["meta"]["writer_profile"] == "openrouter-glm5"  # configured; the task wrote this time
    assert [(request_job(b)[0], b["model"]) for b in llm.requests] == [
        ("plan", "stub/stub-primary"),
        ("prose_text", "z-ai/glm-5"),  # D-178: the text JOB on the writer without JSON mode
        ("prose", "stub/stub-primary"),  # the task fallback: the JSON JOB
    ]
    assert out["meta"]["attempts"] == 3  # plan, the cut writer attempt, the task's prose
    # the spend guard is disabled in these settings, so no worst case is reserved and the cut attempt
    # costs 0 here (its worst-case charge with the guard on: test_d172_writer_past_its_timeout_...)
    assert out["meta"]["cost_usd"] == round(2 * (100 * 1.0 + 20 * 2.0) / 1_000_000, 6)


async def test_ask_prose_mode_abstains_and_refines_with_prose(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    fake = FakeResearcher(facts=[], abstain=True)
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose")
    try:
        out = await ask(connect, world, deps, r, "Which colour is the office coffee machine?")
    finally:
        await r.aclose()
    assert out["abstained"] is True and out["answer"] == "" and out["primary"] == [] and out["claims"] == []
    assert out["confidence"] == "low" and out["meta"]["abstain_reason"] == "no_evidence"
    assert out["meta"]["answer_mode"] == "prose" and out["meta"]["calls"] <= rs.MAX_CALLS_NO_SELECT
    steps = out["meta"]["steps"]
    assert steps[:3] == ["plan", "prose", "refine"] and set(steps) <= {"plan", "prose", "refine"}
    assert set(fake.jobs) <= {"plan", "prose", "refine"} and len(out["related"]) <= 3
    assert out["meta"]["flags"]["dropped_literal"] == 0


# --------------------------------------------------------------------------- D-159 select, then write
async def test_ask_cite_mode_selects_then_writes(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-159 (HLM_RESEARCH_SELECT): plan -> select -> write. The select sees the excerpts exactly as
    the write would; the write sees, cites and is verified against ONLY the selected ones, in the
    select's order; the retrieved excerpts it left out follow in ``related`` (drillable)."""
    fake = FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"])
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite", research_select=True)
    try:
        out = await ask(
            connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
        )
    finally:
        await r.aclose()
    jobs = [request_job(b) for b in llm.requests]
    assert [j for j, _ in jobs] == ["plan", "select", "write"]
    assert out["meta"]["steps"] == ["plan", "select", "write"] and out["meta"]["calls"] == 3
    assert out["meta"]["attempts"] == 3 and llm.requests[1]["max_tokens"] == rs.JOB_MAX_TOKENS["select"]
    assert llm.requests[1]["messages"][1]["content"].startswith("JOB: select\n")
    offered = jobs[1][1]["excerpts"]
    written = jobs[2][1]["excerpts"]
    selected = [e["id"] for e in written]
    assert handle_re(world.versions["D-004"]).fullmatch(selected[0])
    assert handle_re(world.versions["D-001"]).fullmatch(selected[1]) and len(selected) == 2
    by_id = {e["id"]: e for e in offered}
    assert all(by_id[e["id"]] == e for e in written) and len(offered) > len(written)  # same excerpts
    flags = out["meta"]["flags"]
    assert flags["selected"] == 2 and flags["select_fallback"] is False
    assert out["abstained"] is False and "1.2 s" in out["answer"] and "1,6 s" in out["answer"]
    assert {s["handle"] for c in out["claims"] for s in c["support"]} <= set(selected)
    assert {p["handle"] for p in out["primary"]} <= set(selected)
    related = [h["handle"] for h in out["related"]]
    unselected = [e["id"] for e in offered if e["id"] not in selected]
    assert len(related) == min(rs.MAX_RELATED, len(unselected)) >= 1
    assert related == unselected[: len(related)]  # the retrieved excerpts the select left out
    assert_no_secret(sent_text(llm), world)
    assert out["budget"]["used"] <= out["budget"]["limit"]


@pytest.mark.parametrize("picked", [[], ["v999999.0"]], ids=["empty", "unknown"])
async def test_ask_cite_mode_select_fallback_writes_over_every_excerpt(
    connect, world, deps, db_dsn, picked: list[str]
) -> None:  # noqa: ANN001
    """D-159: a select that picks nothing (or only ids it was never shown) never makes the question
    abstain: the write sees every excerpt (``select_fallback``) and answers."""
    fake = FakeResearcher(facts=["1.2 s"], select_ids=picked)
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite", research_select=True)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    jobs = [request_job(b) for b in llm.requests]
    assert [j for j, _ in jobs] == ["plan", "select", "write"]
    assert jobs[2][1]["excerpts"] == jobs[1][1]["excerpts"]  # the write saw everything retrieved
    flags = out["meta"]["flags"]
    assert flags["select_fallback"] is True and flags["selected"] == 0
    assert out["abstained"] is False and "1.2 s" in out["answer"]
    assert handle_re(world.versions["D-004"]).fullmatch(out["primary"][0]["handle"])


async def test_ask_cite_mode_select_runs_again_after_a_refinement(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-159: the first write abstains -> refine -> one more retrieval -> the select runs again over
    the WIDENED excerpt set, and the second write sees only what it picked (6 calls at most)."""
    fake = FakeResearcher(facts=["14 daily dumps"], queries=["retrieval latency target"])
    writes = []

    def two_rounds(body: dict[str, Any]) -> dict[str, Any]:
        job, _inp = request_job(body)
        if job == "refine":
            return {"queries": ["nightly backup script daily dumps"], "sections": []}
        if job == "write" and not writes:
            writes.append(1)
            return {"status": "insufficient_evidence", "sentences": [], "related": [], "confidence": "low"}
        return fake(body)

    llm = ScriptedLLM(default=two_rounds)
    r = make_researcher(db_dsn, llm, research_answer_mode="cite", research_select=True)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target on the VPS?")
    finally:
        await r.aclose()
    jobs = [request_job(b) for b in llm.requests]
    assert [j for j, _ in jobs] == ["plan", "select", "write", "refine", "select", "write"]
    assert out["meta"]["calls"] == rs.MAX_CALLS == 6
    first, second = [e["id"] for e in jobs[1][1]["excerpts"]], [e["id"] for e in jobs[4][1]["excerpts"]]
    assert second[: len(first)] == first and len(second) > len(first)  # the widened set
    runbook = world.versions["runbook"]
    assert [e["id"] for e in jobs[5][1]["excerpts"]] == [h for h in second if handle_re(runbook).fullmatch(h)]
    assert out["abstained"] is False and "14 daily dumps" in out["answer"]
    assert out["meta"]["flags"]["selected"] == 1 and out["meta"]["flags"]["select_fallback"] is False


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
    assert not out["abstained"] and [j for j, _ in seen] == ["plan", "answer", "check"]
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
    assert seen == {(job, rs.JOB_MAX_TOKENS[job]) for job in ("plan", "answer", "check")}


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


class _HitsRun(rsv._Run):
    """A ``_Run`` whose internal ``memory.query`` hits are scripted per query (the DB does the rest)."""

    hits: dict[str, list[str]]

    async def _search(self, c, fresh, query):  # noqa: ANN001, ANN201
        return [{"clue": h} for h in self.hits[query]]


async def test_ask_item_fusion_drills_the_best_chunk_of_a_long_document_first(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """D-165: the per-query hit lists are fused per ITEM (one vote per item per query). A long
    document whose chunks each rank #1 in a different list (each chunk one or two votes: every chunk
    fuses below the items that are #2..#n in every list) is the top item, and its best in-document
    chunk (none of its hit chunks) takes the FIRST drill slot, no longer only a free one after every
    ranked hit; the rest keeps the chunk-fused order (``drill_order``, ≤ 12 excerpts)."""
    status = world.versions["status"]
    async with await connect() as conn:
        spans = await rq_mod.chunk_spans(conn, status)
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        await conn.commit()
    targets = {f"v{status}.{sp.ordinal}" for sp in spans if "trigram fix" in sp.text}
    hit_chunks = [f"v{status}.{sp.ordinal}" for sp in spans if f"v{status}.{sp.ordinal}" not in targets]
    others = [f"v{v.version_id}.0" for v in view if v.version_id != status]
    assert targets and len(hit_chunks) >= 3 and len(others) >= 4
    queries = [f"status query {i}" for i in range(len(hit_chunks))]
    r = make_researcher(db_dsn, ScriptedLLM(default={}))
    async with await connect() as conn:
        await conn.commit()
        run = _HitsRun(
            conn=conn,
            ctx=world.ctx_reader,
            researcher=r,
            deps=deps,
            settings=r.settings,
            question="Is the trigram fix for G-L3 parked?",
            slug=MAIN,
            project_id=world.projects[MAIN],
            end=0.0,
            reconnect=None,
            view={v.version_id: v for v in view},
        )
        # every list: one chunk of the document at #1 (a different one per query), then every other item
        run.hits = {q: [h, *others] for q, h in zip(queries, hit_chunks, strict=True)}
        first = [[{"clue": h} for h in [hit_chunks[0], *others]]]  # the question's own search
        lists = [*first, *([{"clue": x} for x in run.hits[q]] for q in queries)]
        items, fused = rsv.rrf_items(lists), rsv.rrf(lists)
        assert items[0] == hit_chunks[0] and set(items[1:]) == set(others)  # ONE item, the top one
        assert all(fused.index(h) > fused.index(o) for h in hit_chunks for o in others)  # split by chunk
        excerpts, got = await run.retrieve(queries, [], first, set())
        await conn.rollback()
    await r.aclose()
    handles = [e.handle for e in excerpts]
    assert len(got) == 1 + len(queries) and len(handles) == len(set(handles)) <= rsv.MAX_DRILL
    # the document's best in-document chunk (the trigram fix) is drilled FIRST
    assert handles[0] in targets and "trigram fix" in excerpts[0].text
    # then the other top items' own hits and the chunk-fused rest (the hit chunks ±1 of it collapse)
    assert handles == rsv.drill_order([], [handles[0], *items[1 : rsv.DOC_TOP]], fused, set())
    assert handles[1 : rsv.DOC_TOP] == items[1 : rsv.DOC_TOP] and set(others) <= set(handles)


# --------------------------------------------------------------------------- D-184 temporal layer
async def _supersedes(connect, src_vid: int, dst_vid: int, scope: str = "whole", quote: str = "") -> int:  # noqa: ANN001
    """A live ``supersedes`` link (test data only: memory.ask READS links, it never writes them)."""
    props = {"by": "explicit", "scope": scope, "quote": quote, "marker": "test"}
    async with await connect() as conn:
        cur = await conn.execute(
            """
            INSERT INTO links (project_id, project_ids, src_logical_id, dst_logical_id, rel, props,
                               valid_from, recorded_at, source_event_id)
            SELECT s.project_id, s.project_ids, s.logical_id, t.logical_id, 'supersedes', %s::jsonb,
                   s.valid_from, s.recorded_at, s.source_event_id
              FROM memory_versions s CROSS JOIN memory_versions t
             WHERE s.version_id = %s AND t.version_id = %s
            RETURNING link_id
            """,
            (json.dumps(props), src_vid, dst_vid),
        )
        (link_id,) = await cur.fetchone()
        await conn.commit()
    return int(link_id)


async def _drop_links(connect, ids: list[int]) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await conn.execute("DELETE FROM links WHERE link_id = ANY(%s)", (ids,))
        await conn.commit()


TRIGRAM = "The trigram fix for G-L3 is parked until the research loop needs it."


async def _temporal_retrieve(connect, world, deps, db_dsn, hits: list[str]) -> tuple[list[rs.Excerpt], dict]:  # noqa: ANN001
    """One prose-mode retrieval over scripted hits (every list = ``hits``)."""
    async with await connect() as conn:
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        await conn.commit()
    r = make_researcher(db_dsn, ScriptedLLM(default={}), research_answer_mode="prose")
    try:
        async with await connect() as conn:
            await conn.commit()
            run = _HitsRun(
                conn=conn,
                ctx=world.ctx_reader,
                researcher=r,
                deps=deps,
                settings=r.settings,
                question="Is the trigram fix for G-L3 parked, and what is the Memory Map budget?",
                slug=MAIN,
                project_id=world.projects[MAIN],
                end=0.0,
                reconnect=None,
                view={v.version_id: v for v in view},
            )
            run.hits = {"q": hits}
            excerpts, _lists = await run.retrieve(["q"], [], [[{"clue": h} for h in hits]], set())
            await conn.rollback()
    finally:
        await r.aclose()
    return excerpts, run.flags


async def test_ask_temporal_status_whole_and_part_links_and_the_superseder_pull_in(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """D-184: a WHOLE-scope link marks every excerpt of its target item; a PART-scope link only the
    excerpt whose text its quote overlaps. A superseder not shown is pulled in (its best chunk for
    the question), at most SUPERSEDER_EXTRA beyond the cap; each chunk excerpt carries its context."""
    v = world.versions
    status_vid, runbook, d002, d003, d004 = v["status"], v["runbook"], v["D-002"], v["D-003"], v["D-004"]
    async with await connect() as conn:
        spans = await rq_mod.chunk_spans(conn, status_vid)
        await conn.commit()
    trigram_chunks = {f"v{status_vid}.{sp.ordinal}" for sp in spans if "trigram fix" in sp.text}
    first_status = f"v{status_vid}.0"
    links = [
        await _supersedes(connect, d004, d003, "whole", "D-004 replaces the budget rule"),
        await _supersedes(connect, runbook, status_vid, "part", TRIGRAM),
    ]
    try:
        hits = [f"v{d003}.0", first_status, *sorted(trigram_chunks), f"v{d002}.0"]
        excerpts, flags = await _temporal_retrieve(connect, world, deps, db_dsn, hits)
    finally:
        await _drop_links(connect, links)
    by = {e.handle: e for e in excerpts}
    # whole: the D-003 excerpt is superseded by D-004, with the link's quote
    assert by[f"v{d003}.0"].status == (
        f"superseded by v{d004} (docs/decisions/DECISIONS.md#D-004): «D-004 replaces the budget rule»"
    )
    assert by[f"v{d003}.0"].status_vid == d004
    # part: only the STATUS excerpt holding the quoted statement
    part = [e for e in excerpts if e.version_id == status_vid and e.status]
    assert part and all("trigram fix" in e.text for e in part)
    assert part[0].status.startswith(
        f"superseded in part by v{runbook} (deploy/RUNBOOK.md): «The trigram fix"
    )
    assert by[f"v{d002}.0"].status == ""  # no link: current, no field
    # D-188: STATUS (> 3 chunks) shows its best chunk for the question, not its first hit chunk
    assert (
        first_status not in by
        and {e.handle for e in excerpts if e.version_id == status_vid} <= trigram_chunks
    )
    # neither superseder was shown: both pulled in after the ranked excerpts, themselves current;
    # the pulled D-004 row mentions D-001, whose row then follows as a cross-reference (D-188)
    pulled = [e for e in excerpts if e.version_id in (d004, runbook)]
    assert len(pulled) == 2 and all(not e.status for e in pulled) and flags["superseders_pulled"] == 2
    assert excerpts[-1].version_id == world.versions["D-001"] and flags["xref_pulled"] == 1
    # the read-time context labels (a decision row; a heading path)
    assert by[f"v{d003}.0"].context == "DECISIONS.md › row D-003 (2026-09-12)"
    assert all(e.context.startswith("STATUS.md › STATUS") for e in excerpts if e.version_id == status_vid)


async def test_ask_temporal_pull_in_replaces_current_excerpts_when_the_budget_binds(
    connect, world, deps, db_dsn, monkeypatch
) -> None:  # noqa: ANN001
    v = world.versions
    d001, d002, d003, d004 = v["D-001"], v["D-002"], v["D-003"], v["D-004"]
    links = [await _supersedes(connect, d004, d001, "whole", "")]
    try:
        hits = [f"v{d001}.0", f"v{d002}.0", f"v{d003}.0"]
        base, _flags = await _temporal_retrieve(connect, world, deps, db_dsn, hits)  # budget free
        assert [e.version_id for e in base] == [d001, d002, d003, d004]
        # a budget one character short of all four: the pulled one replaces the lowest-ranked
        # CURRENT excerpt (D-003); the superseded one stays (the loop drops until the set fits)
        monkeypatch.setattr(rsv, "EXCERPT_BUDGET_CHARS", sum(len(e.text) for e in base) - 1)
        tight, flags = await _temporal_retrieve(connect, world, deps, db_dsn, hits)
    finally:
        await _drop_links(connect, links)
    assert [e.version_id for e in tight] == [d001, d002, d004] and flags["superseders_pulled"] == 1
    assert tight[0].status == f"superseded by v{d004} (docs/decisions/DECISIONS.md#D-004)"


async def test_ask_prose_mode_writer_sees_status_and_context(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-184 end to end (the mock provider): the prose JOB's excerpts carry ``context`` and, for the
    part-superseded D-001, ``status``; the superseder is already shown (nothing pulled); the claims
    mode's excerpts are unchanged (no context, no status)."""
    d001, d004 = world.versions["D-001"], world.versions["D-004"]
    quote = "The retrieval p95 target is 1,6 s on the VPS."
    links = [await _supersedes(connect, d004, d001, "part", quote)]
    try:
        llm = ScriptedLLM(default=FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"]))
        r = make_researcher(db_dsn, llm, research_answer_mode="prose")
        try:
            out = await ask(
                connect, world, deps, r, "What is the current retrieval p95 target and what was it before?"
            )
        finally:
            await r.aclose()
        llm2 = ScriptedLLM(default=FakeResearcher(facts=["1.2 s"]))
        r2 = make_researcher(db_dsn, llm2)  # the claims mode
        try:
            await ask(
                connect, world, deps, r2, "What is the current retrieval p95 target and what was it before?"
            )
        finally:
            await r2.aclose()
    finally:
        await _drop_links(connect, links)
    shown = {e["id"]: e for e in request_job(llm.requests[1])[1]["excerpts"]}
    old = next(e for h, e in shown.items() if handle_re(d001).fullmatch(h))
    new = next(e for h, e in shown.items() if handle_re(d004).fullmatch(h))
    assert old["status"] == f"superseded in part by v{d004} (docs/decisions/DECISIONS.md#D-004): «{quote}»"
    assert old["context"] == "DECISIONS.md › row D-001 (2026-09-01)" and "status" not in new
    assert new["context"] == "DECISIONS.md › row D-004 (2026-09-20)"
    assert list(old) == ["id", "title", "date", "context", "status", "text"]
    flags = out["meta"]["flags"]
    assert flags["superseded_shown"] == 1 and flags["superseders_pulled"] == 0 and out["abstained"] is False
    for body in llm2.requests[1:]:
        for e in request_job(body)[1].get("excerpts", []):
            assert "status" not in e and "context" not in e


# --------------------------------------------------------------------------- D-188 retrieval
async def test_ask_xref_to_a_d_id_pulls_its_row(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-188: the shown D-004 row mentions D-001, whose row is not shown: its row chunk is pulled in
    (prose mode); a mentioned D-id whose row is shown is not."""
    d001, d004 = world.versions["D-001"], world.versions["D-004"]
    excerpts, flags = await _temporal_retrieve(connect, world, deps, db_dsn, [f"v{d004}.0"])
    assert [e.version_id for e in excerpts] == [d004, d001] and flags["xref_pulled"] == 1
    assert excerpts[1].handle == f"v{d001}.0" and excerpts[1].text.startswith("D-001 | 2026-09-01")
    assert excerpts[1].context == "DECISIONS.md › row D-001 (2026-09-01)"
    shown_both, flags2 = await _temporal_retrieve(connect, world, deps, db_dsn, [f"v{d004}.0", f"v{d001}.0"])
    assert [e.version_id for e in shown_both] == [d004, d001] and flags2.get("xref_pulled", 0) == 0


async def test_ask_xref_to_a_path_pulls_its_best_chunk_and_the_cap_holds(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """D-188: a repo path pulls that item at its best chunk for the question; of five references at
    most XREF_EXTRA are drilled, ranked by mention count plus the question words their chunk shares."""
    v = world.versions
    async with await connect() as conn:
        view = await mm.load_view(conn, world.ctx_reader, world.projects[MAIN])
        spans = await rq_mod.chunk_spans(conn, v["status"])
        await conn.commit()
    trigram = {f"v{v['status']}.{sp.ordinal}" for sp in spans if "trigram fix" in sp.text}
    mentions = rs.Excerpt(
        f"v{v['D-004']}.0", v["D-004"], "T", "docs/decisions/DECISIONS.md#D-004", "2026-09-26",
        "See docs/status/STATUS.md and deploy/RUNBOOK.md; the rules are D-001, D-002 and D-003"
        " (see D-002, D-002).",  # D-002: 3 mentions; STATUS: 1 + 3 question words in its best chunk
    )  # fmt: skip
    r = make_researcher(db_dsn, ScriptedLLM(default={}), research_answer_mode="prose")
    try:
        async with await connect() as conn:
            await conn.commit()
            run = _HitsRun(
                conn=conn,
                ctx=world.ctx_reader,
                researcher=r,
                deps=deps,
                settings=r.settings,
                question="Is the trigram fix for G-L3 parked?",
                slug=MAIN,
                project_id=world.projects[MAIN],
                end=0.0,
                reconnect=None,
                view={x.version_id: x for x in view},
            )
            texts = [run.question]
            focus = rs.content_words(run.question)
            pulled = await run._xrefs(conn, [mentions], texts, set(), {v["D-004"]}, focus)
            await conn.rollback()
    finally:
        await r.aclose()
    assert len(pulled) == rsv.XREF_EXTRA == 3
    # STATUS (1 mention, but its best chunk shares the question's words) first, D-002 (3 mentions),
    # then D-001 (first of the single-mention rows); D-003 and RUNBOOK are left out by the cap
    assert [e.version_id for e in pulled] == [v["status"], v["D-002"], v["D-001"]]
    assert pulled[0].handle in trigram and "trigram fix" in pulled[0].text


async def test_ask_long_item_shows_its_best_chunk_centred(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-188: a top item of more than LONG_ITEM_CHUNKS chunks whose first hit is not its best
    chunk for the question shows only its best chunk (prose mode), clipped around the match."""
    status = world.versions["status"]
    async with await connect() as conn:
        spans = await rq_mod.chunk_spans(conn, status)
        await conn.commit()
    trigram = {f"v{status}.{sp.ordinal}" for sp in spans if "trigram fix" in sp.text}
    far = next(
        f"v{status}.{sp.ordinal}"
        for sp in spans
        if all(abs(sp.ordinal - int(t.split(".")[1])) > 1 for t in trigram)
    )
    assert len(spans) > rsv.LONG_ITEM_CHUNKS
    excerpts, _flags = await _temporal_retrieve(connect, world, deps, db_dsn, [far])
    handles = [e.handle for e in excerpts]
    assert far not in handles and set(handles) <= trigram and len(handles) == 1
    assert "The trigram fix for G-L3 is parked" in excerpts[0].text


# --------------------------------------------------------------------------- D-189 trace recorder
TRACE_QUESTION = "What is the current retrieval p95 target and what was it before?"


async def _traced_ask(connect, world, deps, db_dsn, trace_dir):  # noqa: ANN001, ANN202
    """One prose ask (expand + llm attribution, a part link) through the same mock provider."""
    fake = FakeResearcher(
        facts=["1.2 s", "1,6 s on the VPS"],
        expand_add=["The owner decided it after the R3 release.", "On staging it is 0.3 s."],
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(
        db_dsn,
        llm,
        research_answer_mode="prose",
        research_expand=True,
        research_attribution="llm",
        research_trace_dir=str(trace_dir) if trace_dir is not None else None,
    )
    try:
        out = await ask(connect, world, deps, r, TRACE_QUESTION)
    finally:
        await r.aclose()
    return out, llm


def _without_latency(out: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(out))
    out["meta"].pop("latency_ms")
    return out


async def test_ask_trace_changes_nothing_and_records_every_section(
    connect, world, deps, db_dsn, tmp_path
) -> None:  # noqa: ANN001
    """D-189: with HLM_RESEARCH_TRACE_DIR the response and every provider request are IDENTICAL to a
    run without it (the same mock provider); one trace file holds every section."""
    from hlmemo.core import research_trace as rt

    d001, d004 = world.versions["D-001"], world.versions["D-004"]
    links = [await _supersedes(connect, d004, d001, "part", "The retrieval p95 target is 1,6 s on the VPS.")]
    try:
        plain, llm_plain = await _traced_ask(connect, world, deps, db_dsn, None)
        traced, llm_traced = await _traced_ask(connect, world, deps, db_dsn, tmp_path / "traces")
    finally:
        await _drop_links(connect, links)
    assert _without_latency(traced) == _without_latency(plain)  # the same answer, byte for byte
    assert llm_traced.requests == llm_plain.requests  # the same prompts, request bodies and order
    (path,) = list((tmp_path / "traces").glob("*.json"))
    assert path.name.endswith(f"-{rt.sha256(TRACE_QUESTION)[:8]}.json")
    t = json.loads(path.read_text())
    assert list(t) == list(rt.SECTIONS)
    # 1. the request; 2. the map as the planner got it
    assert t["request"]["question"] == TRACE_QUESTION and t["request"]["answer_mode"] == "prose"
    assert t["request"]["attribution"] == "llm" and t["request"]["writer_profile"] == "stub-primary"
    assert t["request"]["finished_at"] and t["request"]["elapsed_ms"] >= 0
    assert t["map"]["tokens"] > 0 and t["map"]["text"] in llm_plain.requests[0]["messages"][1]["content"]
    # 3. the plan call: the system sha, the full user message, the raw output, the parsed plan
    (plan,) = t["plan"]
    assert (
        plan["job"] == "plan" and plan["user"].startswith("JOB: plan\n") and len(plan["system_sha256"]) == 64
    )
    assert plan["outputs"][0]["content"] and plan["outputs"][0]["outcome"] == "ok" and "queries" in plan
    assert plan["attempts"] == [
        {**plan["attempts"][0], "attempt": 1, "profile": "stub-primary", "outcome": "ok"}
    ]
    assert plan["model"] == "stub/stub-primary" and plan["cost_usd"] and plan["usage"]["prompt_tokens"] == 100
    # 4. retrieval: every query's hits with component ranks; fusion; drill order; drops; statuses
    queries = t["retrieval"]["queries"]
    assert queries[0]["query"] == TRACE_QUESTION and queries[0]["phase"] == "question"
    hit = queries[0]["hits"][0]
    assert {"rank", "handle", "version_id", "title", "path", "score", "lexical_rank", "vector_rank"} <= set(
        hit
    )
    assert queries[0]["candidates"]["vector"] >= 1
    (phase,) = t["retrieval"]["phases"]
    assert phase["chunks"] and phase["items"] and phase["drill_order"] and "dropped" in phase
    assert all(len(c["ranks"]) == len(queries) for c in phase["chunks"])
    assert any(e["status"] for e in phase["excerpts"])  # the part-superseded D-001
    # 5. the excerpts exactly as the writer saw them
    shown = request_job(llm_plain.requests[1])[1]["excerpts"]
    assert t["excerpts"] == shown
    # 6. the write: the exact message, the raw output, the parsed answer
    (write,) = t["write"]
    assert write["job"] == "prose" and write["user"] == llm_plain.requests[1]["messages"][1]["content"]
    assert write["parsed"]["status"] == "answered" and write["outputs"][0]["content"]
    assert t["expand"][0]["added"] == [
        "The owner decided it after the R3 release.",
        "On staging it is 0.3 s.",
    ]
    # 7. validation: every sentence, its literals, where they were found, the verdict
    units = t["validation"]["units"]
    staging = next(u for u in units if "staging" in u.get("text", ""))
    assert staging["verdict"] == "dropped" and staging["reason"] == "literal"
    assert any(lit["found_in"] for u in units for lit in u.get("literals", []))
    assert t["validation"]["drop_reasons"]["literal"] == 1 and "recheck" in t["validation"]
    # 8. attribution: the call, the cites, the final support pairs
    assert t["attribution"]["strategy"] == "llm" and t["attribution"]["calls"][0]["job"] == "attribute"
    assert t["attribution"]["support"] and t["attribution"]["calls"][0]["cites"]
    # 9. the response, and every call recorded
    assert t["response"] == json.loads(json.dumps(traced))
    assert (
        [c["job"] for c in t["calls"]] == traced["meta"]["steps"] == ["plan", "prose", "expand", "attribute"]
    )
    assert "OPENROUTER_API_KEY" not in path.read_text() and "test-key-not-secret" not in path.read_text()


async def test_ask_trace_write_failure_never_fails_the_request(
    connect, world, deps, db_dsn, tmp_path, caplog
) -> None:  # noqa: ANN001
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the trace directory should be")
    out, _llm = await _traced_ask(connect, world, deps, db_dsn, blocker)
    assert out["abstained"] is False and "1.2 s" in out["answer"]
    assert "trace not written" in caplog.text and blocker.read_text().startswith("a file")


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


async def test_ask_check_call_repairs_flagged_claims(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """Addendum 7: the copy-through / attribution repair rides on the completeness call (no extra
    call): the draft claim that names a subject its quotes do not state is sent with a fix note, and
    the final answer carries the repaired claim."""
    fake = FakeResearcher(facts=["1.2 s"], answer_prefix="As reported by Cemal, ")
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert out["meta"]["steps"] == ["plan", "answer", "check"] and out["meta"]["flags"]["rewrites_asked"] >= 1
    check = llm.requests[2]["messages"][1]["content"]
    assert '"fix"' in check and "do not name: Cemal" in check
    assert out["claims"] and not any("Cemal" in c["text"] for c in out["claims"])


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


# --------------------------------------------------------------------------- D-193 (5b) rerank
RERANK_Q = "What is the current retrieval p95 target and what was it before?"


def _is_rerank(body: dict[str, Any]) -> bool:
    from hlmemo.librarian.prompts import load_task

    return body["messages"][0]["content"] == load_task(rs.RERANK_TASK).system


def _rerank_input(body: dict[str, Any]) -> dict[str, Any]:
    return json.loads(body["messages"][1]["content"])


async def _rerank_ask(connect, world, deps, db_dsn, rerank_answer, **kw: Any):  # noqa: ANN001, ANN202
    """One prose ask; ``rerank_answer(body)`` answers the rerank request (None: no rerank expected)."""
    fake = FakeResearcher(facts=["1.2 s", "1,6 s on the VPS"])

    def model(body: dict[str, Any]) -> Any:
        return rerank_answer(body) if _is_rerank(body) else fake(body)

    llm = ScriptedLLM(default=model)
    r = make_researcher(db_dsn, llm, research_answer_mode="prose", **kw)
    try:
        out = await ask(connect, world, deps, r, RERANK_Q)
    finally:
        await r.aclose()
    return out, llm, r


async def test_ask_rerank_off_is_the_k4_order_and_on_reorders_the_drill(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-193 (5b): HLM_RESEARCH_RERANK off (default): no rerank request, the K4 drill order, no flag.
    On (a mock provider): ONE rerank call after the plan sees the question and the K4 candidates as
    [handle, title, first 300 characters]; its offered handles are drilled first (the rest follows in
    the K4 order), a handle it invents is ignored; steps, flags and the cost show the call."""
    base, llm0, r0 = await _rerank_ask(connect, world, deps, db_dsn, lambda body: pytest.fail("no rerank"))
    assert not r0.rerank and not any(_is_rerank(b) for b in llm0.requests)
    assert base["meta"]["steps"] == ["plan", "prose"] and "rerank" not in base["meta"]["flags"]
    k4 = base["meta"]["excerpts_shown"]
    assert len(k4) >= 3

    seen: dict[str, Any] = {}

    def reverse(body: dict[str, Any]) -> dict[str, Any]:
        seen.update(_rerank_input(body))
        handles = [c[0] for c in seen["candidates"]]
        return {"order": ["v999999.0", *reversed(handles)]}  # an invented handle first

    out, llm, r = await _rerank_ask(connect, world, deps, db_dsn, reverse, research_rerank="llm")
    assert r.rerank and r.max_calls == rs.MAX_CALLS_NO_SELECT + 1
    assert out["meta"]["steps"] == ["plan", "rerank", "prose"] and out["meta"]["flags"]["rerank"] == "ok"
    reranks = [b for b in llm.requests if _is_rerank(b)]
    assert len(reranks) == 1 and llm.requests.index(reranks[0]) == 1  # right after the plan
    body = reranks[0]
    assert body["model"] == "stub/stub-primary" and body["max_tokens"] == 1200
    # the measured input: the question and [handle, title, text <= 300 characters], K4 order, <= 30
    assert seen["question"] == RERANK_Q and 2 <= len(seen["candidates"]) <= rs.RERANK_CANDIDATES
    assert all(len(c) == 3 and len(c[2]) <= rs.RERANK_TEXT_CHARS for c in seen["candidates"])
    cands = [c[0] for c in seen["candidates"]]
    assert [h for h in cands if h in k4][:3] == [h for h in k4 if h in cands][:3]  # offered in K4 order
    # the reranked (offered, reversed) handles lead the drill; the invented one is ignored
    kept = list(reversed(cands))[: rs.RERANK_KEEP]
    shown = out["meta"]["excerpts_shown"]
    assert shown != k4 and shown[0] == kept[0] and "v999999.0" not in shown
    assert out["abstained"] is False and "1.2 s" in out["answer"]
    # one more priced call (the stub: 100 in / 20 out tokens per call)
    assert out["meta"]["cost_usd"] == round(3 * (100 * 1.0 + 20 * 2.0) / 1_000_000, 6)


async def test_ask_rerank_timeout_and_invalid_output_keep_the_k4_order(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-193 (5b): a rerank that stalls past HLM_RESEARCH_RERANK_TIMEOUT_S is cut there (a D-173 cut,
    not a breaker failure) and the K4 order is drilled; so is an answer with no offered handle, and a
    schema failure. The answer is written either way."""
    import time

    base, _llm0, _r0 = await _rerank_ask(connect, world, deps, db_dsn, lambda body: pytest.fail("no rerank"))
    k4 = base["meta"]["excerpts_shown"]

    t0 = time.perf_counter()
    out, llm, r = await _rerank_ask(
        connect,
        world,
        deps,
        db_dsn,
        lambda body: ("stall", 5.0, {"order": [c[0] for c in _rerank_input(body)["candidates"]][::-1]}),
        research_rerank="llm",
        research_rerank_timeout_s=0.5,
    )
    assert time.perf_counter() - t0 < 4.0  # the 5 s stall was cut at the rerank's 0.5 s
    assert out["meta"]["flags"]["rerank"] == "timeout" and out["meta"]["excerpts_shown"] == k4
    assert out["abstained"] is False and "1.2 s" in out["answer"]
    breaker = r.provider.breaker("stub-primary")
    assert breaker.state == "closed" and breaker.failures == 0  # tail latency, not an outage

    for answer, why in (
        ({"order": ["v999999.0", "not-a-handle"]}, "invalid"),  # no offered handle
        ({"ranking": []}, "schema_fail"),  # the task's shape: "order" missing (retried once)
    ):
        out, llm, _r = await _rerank_ask(
            connect, world, deps, db_dsn, lambda body, a=answer: a, research_rerank="llm"
        )
        assert out["meta"]["flags"]["rerank"] == why, why
        assert out["meta"]["excerpts_shown"] == k4 and out["abstained"] is False


async def test_ask_rerank_is_traced_under_its_section(connect, world, deps, db_dsn, tmp_path) -> None:  # noqa: ANN001
    """D-193 (5b): the trace's ``rerank`` section holds the call (its exact input, attempts and raw
    output), the candidates offered and the handles kept; the retrieval phase notes the result."""
    from hlmemo.core import research_trace as rt

    def first_two(body: dict[str, Any]) -> dict[str, Any]:
        return {"order": [c[0] for c in _rerank_input(body)["candidates"]][1:3]}

    out, llm, _r = await _rerank_ask(
        connect, world, deps, db_dsn, first_two, research_rerank="llm", research_trace_dir=str(tmp_path)
    )
    (path,) = list(tmp_path.glob("*.json"))
    t = json.loads(path.read_text())
    assert list(t) == list(rt.SECTIONS) and "rerank" in rt.SECTIONS
    (rec,) = t["rerank"]
    body = next(b for b in llm.requests if _is_rerank(b))
    assert rec["job"] == "rerank" and rec["user"] == body["messages"][1]["content"]
    assert rec["output"]["order"] == rec["kept"] and rec["fallback"] is None and rec["phase"] == "plan"
    assert rec["candidates"] == [c[0] for c in _rerank_input(body)["candidates"]]
    assert rec["attempts"][0]["outcome"] == "ok" and rec["outputs"][0]["content"]
    (phase,) = t["retrieval"]["phases"]
    assert phase["rerank"] == {"kept": rec["kept"], "fallback": None}
    assert phase["drill_order"][:2] == rec["kept"]
    assert [c["job"] for c in t["calls"]] == out["meta"]["steps"] == ["plan", "rerank", "prose"]
