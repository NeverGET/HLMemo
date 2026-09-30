"""Dual review 79 (round 1) regressions for ``memory.ask`` (D-136), each built from the reviewer's
exact scenario and failing on 9bceb59:

* T1 — D-083 isolation: projects A (``ask-main``) and T (``ask-iso``, ``librarian_cross_project =
  exclude``), the caller reads both, an item ``[A, T]`` holds ``T-ONLY-SECRET``: ``memory.ask(A)``
  never puts it in the map, a prompt or a map summary (asserted on the recorded provider payloads).
* T2 — ``password = "SuperSecret123456"`` in the question and in an item body never reaches a
  recorded payload (the research and the map_summary paths): values are redacted BEFORE the JSON
  serialisation.
* T4 — a schema-invalid first attempt plus a paid retry never exceed the per-question cap; a failed
  map summary on an idle database is retried after its back-off.
* T5 — an R3 env on this image leaves memory.ask off and spends nothing.
* T6 — a claim (or quote) without the source's "not" is never answered.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from hlmemo.auth.context import Role
from hlmemo.config import get_settings
from hlmemo.core import memory_map as mm
from hlmemo.core import research_service as rsv
from hlmemo.core.read_service import default_read_deps
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian import privacy
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import map_summary as ms
from hlmemo.librarian.tasks import research as rs
from hlmemo.server.app import create_app
from hlmemo.server.tools import advertised_tools
from hlmemo.worker.main import drain
from tests.integration._ask_fixtures import (
    CLIENT,
    MAIN,
    FakeResearcher,
    ctx_of,
    request_job,
    seed_world,
)
from tests.integration._librarian_fixtures import ScriptedLLM, chat, stub_chain, stub_profile
from tests.integration._mcp_fixtures import ADMIN_TOKEN, call_tool_raw, mcp_rpc, trusted_device

pytestmark = pytest.mark.integration

ISO = "ask-iso"
T_SECRET = "T-ONLY-SECRET-q8v"
PASSWORD = "SuperSecret123456"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    """The ask fixture world plus: project ``ask-iso`` (excluded from cross-project librarian work,
    read by the reader) with an item co-owned by ask-main and ask-iso, an ask-main item holding a
    password assignment, and one stating that the release gate is NOT enabled by default."""
    w = await seed_world(connect, deps.embedder)
    async with await connect() as conn:
        cur = await conn.execute(
            "INSERT INTO projects (slug, name, policy) VALUES (%s, 'Ask iso',"
            ' \'{"librarian_cross_project": "exclude"}\') RETURNING project_id',
            (ISO,),
        )
        iso = int((await cur.fetchone())[0])
        w.projects[ISO] = iso
        for did, role in ((w.ctx_loader.device_id, "write"), (w.reader_id, "read")):
            await conn.execute(
                "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
                " VALUES (%s, %s, %s, 1)",
                (did, iso, role),
            )
        await conn.commit()
        loader = ctx_of(w.ctx_loader.device_id, {pid: Role.WRITE for pid in w.projects.values()}, "work")
        w.ctx_reader = ctx_of(w.reader_id, {**w.ctx_reader.grants, iso: Role.READ})
        wdeps = default_deps()

        async def put(project: str, name: str, item: dict[str, Any]) -> None:
            ack = await write(
                conn,
                loader,
                {"project": project, "request_id": str(uuid.uuid4()), "client": CLIENT, "items": [item]},
                deps=wdeps,
            )
            await conn.commit()
            w.versions[name] = ack.versions[0].version_id

        def item(title: str, body: str, path: str, **kw: Any) -> dict[str, Any]:
            return {
                "kind": "doc_chunk",
                "title": title,
                "body": body,
                "source": {"system": "markdown", "path": path, "sha256": uuid.uuid4().hex * 2},
                **kw,
            }

        await put(
            MAIN,
            "iso",
            item(
                "ISOLATED · docs/iso/ISOLATED.md",
                f"{T_SECRET} belongs to the isolated test project."
                " The retrieval p95 target is discussed there.",
                "docs/iso/ISOLATED.md",
                project_ids=[MAIN, ISO],
            ),
        )
        await put(
            MAIN,
            "creds",
            item(
                "CREDENTIALS · docs/ops/CREDENTIALS.md",
                f'The staging database password = "{PASSWORD}" is rotated monthly by the operator.',
                "docs/ops/CREDENTIALS.md",
            ),
        )
        await put(
            MAIN,
            "gates",
            item(
                "GATES · docs/gates/GATES.md",
                "## Release gate\n\nThe release gate is **not** enabled by default. It runs weekly.",
                "docs/gates/GATES.md",
            ),
        )
    await drain(connect, deps.embedder)
    return w


def ask_settings(db_dsn: str, **kw: Any):  # noqa: ANN201
    base = {
        "db_dsn": db_dsn,
        "librarian_enabled": True,
        "llm_mode": "live",
        "llm_budget_disabled": True,
        "research_enabled": True,
        "map_summary_enabled": False,
        "research_max_usd": 1.0,
    }
    return get_settings(**{**base, **kw})


def make_researcher(db_dsn: str, llm: ScriptedLLM, **kw: Any) -> rs.Researcher:
    return rs.Researcher(
        ask_settings(db_dsn, **kw), chain=stub_chain(fallback=False), transport=llm.transport
    )


async def ask(connect, world, deps, researcher, question: str, **args: Any) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        await conn.commit()
        try:
            return await rsv.ask(
                conn,
                world.ctx_reader,
                {"question": question, "project": MAIN, **args},
                deps=deps,
                researcher=researcher,
                settings=researcher.settings,
            )
        finally:
            await conn.rollback()


def payloads(llm: ScriptedLLM) -> str:
    """Every recorded provider request body, serialised as it went on the wire (JSON)."""
    return "\n".join(json.dumps(b, ensure_ascii=False) for b in llm.requests)


def summary_provider(llm: ScriptedLLM) -> Provider:
    return Provider(
        stub_chain(fallback=False),
        mode="live",
        budget=MemoryBudget(Decimal("1")),
        ledger=MemoryLedger(),
        transport=llm.transport,
        timeout_s=5.0,
    )


async def _summarizer(connect, db_dsn: str, llm: ScriptedLLM) -> ms.MapSummarizer:  # noqa: ANN001
    async def conn_factory():  # noqa: ANN202
        return await connect()

    settings = ask_settings(db_dsn, map_summary_debounce_s=0.0, map_summary_per_cycle=200)
    return ms.MapSummarizer(settings, provider=summary_provider(llm), connect=conn_factory)


async def _drop_summaries(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        await conn.execute("DELETE FROM memory_map_summaries")
        await conn.commit()


def _summary(body: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
    return {"summary": f"Source {payload['source']} of the synthetic project."}


# --------------------------------------------------------------------------- T1 isolation
async def test_review79_t1_excluded_project_never_reaches_map_prompts_or_summaries(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    iso_vid = world.versions["iso"]
    fake = FakeResearcher(
        facts=["1.2 s", T_SECRET],
        queries=[T_SECRET, "isolated test project"],
        sections=[f"v{iso_vid}", f"v{iso_vid}.0"],
        extra_primary=[f"v{iso_vid}"],
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target and the isolated project?")
    finally:
        await r.aclose()
    sent = payloads(llm)
    for q in fake.queries:  # the planner's own queries echo back (refine); never content
        sent = sent.replace(json.dumps(q)[1:-1], "")
    assert T_SECRET not in sent and "ISOLATED.md" not in sent
    assert not re.search(rf"\bv{iso_vid}(\.\d+)?\b", sent)
    assert T_SECRET not in json.dumps({k: v for k, v in out.items() if k != "meta"})
    # the view (the map's input) and the gate: the [A, T] item is out, for A and for T alike
    async with await connect() as conn:
        for home in (MAIN, ISO):
            view = await mm.load_view(conn, world.ctx_reader, world.projects[home])
            assert iso_vid not in {v.version_id for v in view}, home
        items = await privacy.load_items(conn, [iso_vid, world.versions["D-004"]])
        caps = {
            "trigger_device_id": world.reader_id,
            "question": sorted(world.ctx_reader.grants),
            "isolation_home": world.projects[MAIN],
        }
        verdict = await privacy.check(conn, caps, list(items.values()))
        await conn.commit()
    assert verdict.denied == {iso_vid: privacy.CROSS_PROJECT_ISOLATED}
    # the map summaries: never a member, never in a summary prompt (the recorded payloads)
    llm2 = ScriptedLLM(default=_summary)
    summ = await _summarizer(connect, db_dsn, llm2)
    try:
        assert await summ.cycle() >= 3
    finally:
        await summ.provider.aclose()
    assert T_SECRET not in payloads(llm2) and "ISOLATED.md" not in payloads(llm2)
    async with await connect() as conn:
        rows = await (await conn.execute("SELECT member_ids FROM memory_map_summaries")).fetchall()
        await conn.commit()
    assert rows and all(iso_vid not in r[0] for r in rows)
    await _drop_summaries(connect)


# --------------------------------------------------------------------------- T2 redaction
async def test_review79_t2_secret_assignment_is_redacted_before_serialisation(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    creds = world.versions["creds"]
    fake = FakeResearcher(
        facts=["rotated monthly"], queries=["staging database password"], sections=[f"v{creds}"]
    )
    llm = ScriptedLLM(default=fake)
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(
            connect,
            world,
            deps,
            r,
            f'Is the staging password = "{PASSWORD}" still valid, and when is it rotated?',
        )
    finally:
        await r.aclose()
    assert llm.requests and "plan" in request_job(llm.requests[0])[0]
    assert PASSWORD not in payloads(llm)
    assert "⟦REDACTED:assignment:" in payloads(llm)  # the question AND the drilled body were redacted
    # the caller's own question is echoed in meta.queries (D-097); nothing else carries it
    assert PASSWORD not in json.dumps({k: v for k, v in out.items() if k != "meta"})
    llm2 = ScriptedLLM(default=_summary)
    summ = await _summarizer(connect, db_dsn, llm2)
    try:
        assert await summ.cycle() >= 3
    finally:
        await summ.provider.aclose()
    sent = payloads(llm2)
    assert "CREDENTIALS.md" in sent and PASSWORD not in sent
    await _drop_summaries(connect)


# --------------------------------------------------------------------------- T4 budget per attempt
async def test_review79_t4_schema_invalid_attempt_plus_paid_retry_stays_within_the_question_cap(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """Cap $0.01, per-attempt worst case $0.006 (an answer call: 3000 output tokens at $2/1M): the
    plan costs $0.001, the first answer attempt is schema-invalid and billed $0.006; its paid retry
    (another $0.006) would end at $0.013. It is refused before it is reserved or sent."""
    fake = FakeResearcher(facts=["1.2 s"])
    answers = {"n": 0}

    def route(body: dict[str, Any]) -> Any:
        job = request_job(body)[0]
        if job == "plan":
            return chat(fake(body), cost=0.001)
        answers["n"] += 1
        if answers["n"] == 1:
            return chat("this is not the answer JSON", cost=0.006)
        return chat(fake(body), cost=0.006)

    llm = ScriptedLLM(default=route)
    r = rs.Researcher(
        ask_settings(db_dsn, research_max_usd=0.01),
        chain=[stub_profile("worst6", price_in="0", price_out="2.0")],
        transport=llm.transport,
    )
    try:
        out = await ask(connect, world, deps, r, "What is the retrieval p95 target?")
    finally:
        await r.aclose()
    assert out["meta"]["cost_usd"] <= 0.01, out["meta"]
    assert out["meta"]["flags"]["budget_stop"] is True
    assert llm.calls == 2  # the plan and the one schema-invalid attempt; the retry never went out


async def test_review79_t4_failed_summary_is_retried_after_the_backoff_on_an_idle_db(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    state = {"fail": True}

    def route(body: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
        if payload["source"] == "GATES.md" and state["fail"]:
            return {"summary": ""}  # schema-valid JSON, rejected by the task's own check (twice)
        return _summary(body)

    llm = ScriptedLLM(default=route)
    summ = await _summarizer(connect, db_dsn, llm)
    t0 = 10_000.0
    try:
        assert await summ.cycle(now=t0) >= 3
        async with await connect() as conn:
            failed = await (
                await conn.execute("SELECT source_key FROM memory_map_summaries WHERE status = 'failed'")
            ).fetchall()
            await conn.commit()
        assert [r[0] for r in failed] == ["markdown:docs/gates/GATES.md"]
        calls = llm.calls
        assert await summ.cycle(now=t0 + 1) == 0 and llm.calls == calls  # inside the back-off
        state["fail"] = False
        async with await connect() as conn:  # 301 s later; nothing was written meanwhile (idle DB)
            await conn.execute(
                "UPDATE memory_map_summaries SET updated_at = now() - interval '301 seconds'"
                " WHERE status = 'failed'"
            )
            await conn.commit()
        assert await summ.cycle(now=t0 + 302) == 1 and llm.calls == calls + 1
        async with await connect() as conn:
            status = await (
                await conn.execute(
                    "SELECT status FROM memory_map_summaries"
                    " WHERE source_key = 'markdown:docs/gates/GATES.md'"
                )
            ).fetchone()
            await conn.commit()
        assert status[0] == "ok"
    finally:
        await summ.provider.aclose()
        await _drop_summaries(connect)


# --------------------------------------------------------------------------- T5 release defaults
def _r3_env() -> dict[str, str]:
    """The R3 llm.env as install_llm_env.sh wrote it: the template without the R4 lines."""
    env: dict[str, str] = {}
    for line in (ROOT / "deploy/llm.env.example").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            env[key.strip()] = value.strip()
    for key in list(env):
        if key.startswith(("HLM_RESEARCH_", "HLM_MAP_SUMMARY_")) or key in (
            "HLM_FALLBACK_PROFILE__RESEARCH",
            "HLM_FALLBACK_PROFILE__MAP_SUMMARY",
        ):
            del env[key]
    env.update(
        HLM_ENV_RELEASE="r3", HLM_LIBRARIAN_ENABLED="true", OPENROUTER_API_KEY="dummy-not-a-key-000000"
    )
    return env


@contextlib.asynccontextmanager
async def r3_app(settings: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_review79_t5_an_r3_env_on_this_image_leaves_memory_ask_off_and_spends_nothing(
    connect, world, db_dsn, monkeypatch
) -> None:  # noqa: ANN001
    from hlmemo.librarian.worker import LibrarianWorker

    env = _r3_env()
    assert not any(k.startswith("HLM_RESEARCH") or k.startswith("HLM_MAP_SUMMARY") for k in env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("HLM_RESEARCH_ENABLED", "HLM_MAP_SUMMARY_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    settings = get_settings(db_dsn=db_dsn, admin_token=ADMIN_TOKEN, registration_secret=None)
    assert settings.librarian_enabled and settings.llm_mode == "live"
    assert settings.research_enabled is False and settings.map_summary_enabled is False
    assert "memory.ask" not in {t.name for t in advertised_tools(settings)}
    llm = ScriptedLLM(default=_summary)
    assert rs.Researcher(settings, chain=stub_chain(fallback=False), transport=llm.transport).enabled is False

    async def conn_factory():  # noqa: ANN202
        return await connect()

    worker = LibrarianWorker(settings, provider=summary_provider(llm), connect=conn_factory)
    try:
        assert worker.map_summarizer is None and worker.maybe_map_summaries() is False
    finally:
        await worker.provider.aclose()
    async with await connect() as conn:
        before = (await (await conn.execute("SELECT count(*) FROM llm_calls")).fetchone())[0]
        await conn.commit()
    async with r3_app(settings) as client:
        _did, token = await trusted_device(client, "ask-r3", grants=[{"project": MAIN, "role": "read"}])
        listed = (await mcp_rpc(client, token, "tools/list")).json()["result"]["tools"]
        assert "memory.ask" not in {t["name"] for t in listed}
        res = await call_tool_raw(client, token, "memory.ask", {"question": "p95 target?", "project": MAIN})
        assert res["isError"] and json.loads(res["content"][0]["text"])["details"]["reason"] == "disabled"
    async with await connect() as conn:
        after = (await (await conn.execute("SELECT count(*) FROM llm_calls")).fetchone())[0]
        rows = (await (await conn.execute("SELECT count(*) FROM memory_map_summaries")).fetchone())[0]
        await conn.commit()
    assert after == before and rows == 0 and llm.calls == 0  # nothing spent, nothing summarised


# --------------------------------------------------------------------------- T6 polarity
async def test_review79_t6_a_claim_without_the_sources_not_is_never_answered(
    connect, world, deps, db_dsn
) -> None:  # noqa: ANN001
    """Source: "The release gate is **not** enabled by default." The model claims it IS enabled,
    once quoting the sentence without "not" (a skipped word) and once quoting it verbatim: neither
    is an answer (abstention, guard); a faithful claim with the verbatim quote is answered."""
    gates = world.versions["gates"]

    def model(claim: str, quote: str):  # noqa: ANN202
        def route(body: dict[str, Any]) -> dict[str, Any]:
            job, inp = request_job(body)
            if job in ("plan", "refine"):
                return {"queries": ["release gate default"], "sections": [f"v{gates}"]}
            ex = next((e for e in inp.get("excerpts") or [] if "release gate" in e["text"]), None)
            if ex is None:
                return {"status": "insufficient_evidence", "answer": "", "claims": [], "confidence": "low"}
            return {
                "status": "answered",
                "answer": claim,
                "claims": [{"text": claim, "support": [{"id": ex["id"], "quote": quote}]}],
                "related": [],
                "confidence": "high",
            }

        return route

    verbatim = "The release gate is **not** enabled by default."
    for claim, quote in (
        ("The release gate is enabled by default.", "The release gate is enabled by default."),
        ("The release gate is enabled by default.", verbatim),
    ):
        llm = ScriptedLLM(default=model(claim, quote))
        r = make_researcher(db_dsn, llm)
        try:
            out = await ask(connect, world, deps, r, "Is the release gate enabled by default?")
        finally:
            await r.aclose()
        assert out["abstained"] is True and out["primary"] == [] and out["answer"] == "", (claim, quote, out)
        assert out["meta"]["abstain_reason"] == "guard"
    llm = ScriptedLLM(default=model("The release gate is not enabled by default.", verbatim))
    r = make_researcher(db_dsn, llm)
    try:
        out = await ask(connect, world, deps, r, "Is the release gate enabled by default?")
    finally:
        await r.aclose()
    assert out["abstained"] is False and "not enabled" in out["answer"]
    assert "**not**" in out["primary"][0]["quote"]
