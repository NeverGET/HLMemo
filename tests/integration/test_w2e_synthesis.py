"""W2e gates for ``memory.query`` synthesis (PHASE2-4-ROADMAP W2e; CC-4 ``query/2``; D-062, D-067).

Fixture world: HLMemo's public ``docs/`` at the pinned corpus commit, imported into ``syn-docs``
(``_synthesis_fixtures``; about two minutes, once per module); questions from
``tests/fixtures/synthesis`` (τ_s calibrated on its cal split, README).

* **CI replay**: every weak-evidence question (top RRF < τ_s; both splits) through the production
  path with the primary profile's answers replayed strictly from ``tests/cassettes/w2e``
  (``HLM_W2E_RECORD=1`` + OPENROUTER_API_KEY re-records). Asserts, for 100% of the responses:
  ``synthesis.clues`` ⊆ the returned hits' clues, every sentence carries citation markers of those
  clues, text ≤ 400 tokens, ``budget.used`` exact and ≤ the limit; every call reached the model.
* **No flag = unchanged**: through the real API, a call without ``synthesize`` (or with ``false``)
  returns text byte-identical to the Phase-0 tool path, and its p95 is unchanged (interleaved A/B).
* **Stalled model** (scripted stub, real API): every synthesizing call answers
  ``synthesis_unavailable`` and p95 ≤ 6.5 s; queries without the flag stay fast meanwhile.
* **G2 with synthesis**: 120 random budgets through the wire: exact ``budget.used`` ≤ limit, clues ⊆
  hits, the synthesis survives whenever one cited sentence fits.
* Privacy default-deny (``device:*``, ``policy.librarian=off``, co-owned by an ungranted project),
  the D-067 citation guard, abstention, schema retry, fallback label, qualification, budget stop,
  D-062 (revoke during the call → ``E_AUTH``; the pool is not held; cited visibility re-checked),
  strong evidence → no LLM call, G-SURF.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import synthesis_service as ss
from hlmemo.core.budget import Meter, canonical
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps, query
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.tasks import synthesis as syn
from hlmemo.server.app import create_app
from hlmemo.server.tools import TOOL_BY_NAME, handlers, schemas
from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    bearer,
    call_tool_raw,
    mcp_rpc,
    running_app,
    trusted_device,
)
from tests.integration._synthesis_fixtures import (
    PROJECT,
    keys_in,
    load_env_file,
    load_questions,
    seed_corpus,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "tests" / "cassettes" / "w2e"
PRIMARY_PROFILE = "openrouter-gpt6-luna"
BUDGET = 3000
P95_STALL_S = 6.5
MARKERS = re.compile(r"\[(v\d+\.\d+(?:, v\d+\.\d+)*)\]")
METER = Meter()


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_corpus(connect, deps, deps.embedder)


@pytest.fixture(scope="module")
async def weak_ids(connect, world, deps) -> list[str]:  # noqa: ANN001
    """Positive questions whose fast path is weak at budget 3000 (both splits)."""
    out = []
    async with await connect() as conn:
        for q in load_questions():
            if q.get("negative"):
                continue
            res = await query(conn, world.ctx, _args(q["question"]), deps=deps)
            await conn.commit()
            if ss.weak(res) is None:
                out.append(q["id"])
    return out


def _args(question: str, budget: int = BUDGET) -> dict[str, Any]:
    return {"project": PROJECT, "query": question, "token_budget": budget}


def synth_settings(db_dsn: str, **kw: Any):  # noqa: ANN201
    base = {"db_dsn": db_dsn, "librarian_enabled": True, "llm_mode": "live", "llm_budget_disabled": True}
    return get_settings(**{**base, **kw})


def stub_synth(db_dsn: str, llm: ScriptedLLM, *, fallback: bool = False, **kw: Any) -> syn.Synthesizer:
    settings = synth_settings(db_dsn, **kw.pop("settings", {}))
    return syn.Synthesizer(settings, chain=stub_chain(fallback=fallback), transport=llm.transport, **kw)


async def run(
    connect,  # noqa: ANN001
    world,  # noqa: ANN001
    deps,  # noqa: ANN001
    question: str,
    synth: syn.Synthesizer | None,
    *,
    budget: int = BUDGET,
    tau: float = ss.TAU_S,
    ctx: Any = None,
) -> tuple[dict[str, Any], float]:
    async with await connect() as conn:
        await conn.commit()  # an idle connection: synthesis never runs inside a caller's tx (D-062)
        t0 = time.perf_counter()
        out = await ss.query_synthesize(
            conn, ctx or world.ctx, _args(question, budget), deps=deps, synth=synth, tau=tau
        )
        ms = (time.perf_counter() - t0) * 1000
        await conn.commit()
    return out, ms


def check_response(out: dict[str, Any]) -> None:
    """The CI invariants of one ``synthesize:true`` response."""
    assert out["contract_version"] == "query/2"
    text = canonical(out)
    assert out["budget"]["used"] == METER.count_text(text) <= out["budget"]["limit"]
    hit_clues = {h["clue"] for h in out["hits"]}
    s = out.get("synthesis")
    if s is None:
        assert out["synthesis_unavailable"] is True and out["synthesis_reason"], out
        return
    assert set(s["clues"]) <= hit_clues, (s["clues"], hit_clues)  # cited clues ⊆ hits
    if s["status"] == "answered":
        marked = [c for group in MARKERS.findall(s["text"]) for c in group.split(", ")]
        assert marked and set(marked) == set(s["clues"]), s
        assert METER.count_text(s["text"]) <= ss.TEXT_MAX_TOKENS
    else:
        assert s["status"] == "insufficient_evidence" and s["text"] == "" and s["clues"] == []


def _ids(body: dict[str, Any]) -> list[str]:
    payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
    return [e["id"] for e in payload["excerpts"]]


def _excerpt_texts(body: dict[str, Any]) -> str:
    return body["messages"][1]["content"]


def answer_citing(ids: list[str], n: int = 2, text: str = "The answer is stated here.") -> dict[str, Any]:
    return {
        "status": "answered",
        "sentences": [{"text": f"{text} ({i})", "cite": [ids[i]]} for i in range(n)],
    }


def weak_question(weak_ids: list[str]) -> dict[str, Any]:
    qs = {q["id"]: q for q in load_questions()}
    return qs[weak_ids[0]]


# --------------------------------------------------------------------------- CI replay (100%)
async def test_ci_replay_cited_clues_subset_of_hits(connect, world, deps, weak_ids, db_dsn) -> None:  # noqa: ANN001
    record = os.environ.get("HLM_W2E_RECORD") == "1"
    if not record and not any(CASSETTES.glob("*.jsonl")):
        pytest.fail(f"no cassettes under {CASSETTES}: record them with HLM_W2E_RECORD=1 (live, primary)")
    settings = synth_settings(db_dsn, llm_mode="record" if record else "replay", llm_cassette_dir=CASSETTES)
    budget = None
    if record:  # live: the provider behind a runaway guard (HLM_W2E_RECORD_MAX_USD), no 6 s cap
        load_env_file()
        budget = MemoryBudget(Decimal(os.environ.get("HLM_W2E_RECORD_MAX_USD", "0.5")))
        provider = Provider(
            [named_profile(PRIMARY_PROFILE)],
            mode="record",
            budget=budget,
            ledger=MemoryLedger(),
            cassettes=CassetteStore(
                CASSETTES, record_name=syn.TASK, redactor=Redactor.from_settings(settings)
            ),
            redactor=Redactor.from_settings(settings),
            timeout_s=60.0,
        )
        synth = syn.Synthesizer(settings, provider=provider, timeout_s=90.0)
    else:
        synth = syn.Synthesizer(settings, chain=[named_profile(PRIMARY_PROFILE)], cassette_dir=CASSETTES)
    qs = {q["id"]: q for q in load_questions()}
    statuses: dict[str, int] = {}
    correct = 0
    try:
        for qid in weak_ids:
            synth.breaker.success()
            out, _ = await run(connect, world, deps, qs[qid]["question"], synth)
            check_response(out)
            s = out.get("synthesis")
            key = s["status"] if s else out["synthesis_reason"]
            statuses[key] = statuses.get(key, 0) + 1
            assert s is not None or out["synthesis_reason"] in (syn.GUARD,), (
                qid,
                out,
            )  # the model was reached
            correct += bool(s and keys_in(s["text"], qs[qid]["keys"]))
    finally:
        await synth.aclose()
    spent = f", recorded for ${budget.spent}" if budget is not None else ""
    print(
        f"\nW2e CI replay ({PRIMARY_PROFILE}): {len(weak_ids)} weak questions, statuses {statuses}, "
        f"answer accuracy on the recorded answers {correct}/{len(weak_ids)}{spent}"
    )
    assert len(weak_ids) >= 60, len(weak_ids)


# --------------------------------------------------------------------------- strong → no LLM
async def test_strong_evidence_makes_no_llm_call(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default={"status": "insufficient_evidence", "sentences": []})
    synth = stub_synth(db_dsn, llm)
    try:
        strong = None
        async with await connect() as conn:
            for q in load_questions():
                res = await query(conn, world.ctx, _args(q["question"]), deps=deps)
                await conn.commit()
                if ss.weak(res) == ss.STRONG:
                    strong = q
                    break
        assert strong is not None
        out, _ = await run(connect, world, deps, strong["question"], synth)
        check_response(out)
        assert out["synthesis_reason"] == ss.STRONG and llm.calls == 0, out
    finally:
        await synth.aclose()


# --------------------------------------------------------------------------- D-067 guards
async def test_citation_guard_abstention_and_schema_retry(connect, world, deps, weak_ids, db_dsn) -> None:  # noqa: ANN001
    q = weak_question(weak_ids)["question"]

    def mixed(body: dict[str, Any]) -> dict[str, Any]:
        ids = _ids(body)
        return {
            "status": "answered",
            "sentences": [
                {"text": "Supported.", "cite": [ids[0]]},
                {"text": "Invented source.", "cite": ["C99"]},
                {"text": "No source at all.", "cite": []},
            ],
        }

    llm = ScriptedLLM(default=mixed)
    synth = stub_synth(db_dsn, llm)
    try:
        out, _ = await run(connect, world, deps, q, synth)
        check_response(out)
        s = out["synthesis"]
        assert s["status"] == "answered" and s["dropped"] == 2 and s["text"].startswith("Supported."), s
        assert s["clues"] == [out["hits"][0]["clue"]]
        # every sentence uncited: a guard failure, never an empty "answer"
        llm.default = {"status": "answered", "sentences": [{"text": "x", "cite": ["C42"]}]}
        out, _ = await run(connect, world, deps, q, synth)
        assert out["synthesis_reason"] == syn.GUARD and "synthesis" not in out, out
        # abstention is a result, not a failure
        llm.default = {"status": "insufficient_evidence", "sentences": []}
        out, _ = await run(connect, world, deps, q, synth)
        check_response(out)
        assert out["synthesis"]["status"] == "insufficient_evidence", out
        # status/sentences mismatch = schema failure: retried once, then schema_fail
        n0 = llm.calls
        llm.script = [
            {"status": "answered", "sentences": []},
            {"status": "insufficient_evidence", "sentences": []},
        ]
        out, _ = await run(connect, world, deps, q, synth)
        assert out["synthesis"]["status"] == "insufficient_evidence" and llm.calls == n0 + 2, out
        llm.script = [{"status": "answered", "sentences": []}, {"status": "maybe", "sentences": []}]
        out, _ = await run(connect, world, deps, q, synth)
        assert out["synthesis_reason"] == syn.SCHEMA_FAIL, out
    finally:
        await synth.aclose()


async def test_fallback_label_budget_stop_and_disabled(connect, world, deps, weak_ids, db_dsn) -> None:  # noqa: ANN001
    q = weak_question(weak_ids)["question"]

    def answer(body: dict[str, Any]) -> Any:
        return 400 if body["model"].endswith("stub-primary") else answer_citing(_ids(body), 1)

    llm = ScriptedLLM(default=answer)
    synth = stub_synth(db_dsn, llm, fallback=True)
    try:
        out, _ = await run(connect, world, deps, q, synth)
        check_response(out)
        assert out["synthesis"]["tier"] == "fallback", out  # D-066: the caller is told
    finally:
        await synth.aclose()
    llm = ScriptedLLM(default={"status": "insufficient_evidence", "sentences": []})
    capped = stub_synth(db_dsn, llm, settings={"llm_budget_disabled": False, "llm_budget_hour_usd": 0.0})
    try:
        out, _ = await run(connect, world, deps, q, capped)
        assert out["synthesis_reason"] == syn.BUDGET and llm.calls == 0, out
    finally:
        await capped.aclose()
    off = syn.Synthesizer(synth_settings(db_dsn, librarian_enabled=False), chain=stub_chain())
    out, _ = await run(connect, world, deps, q, off)
    assert out["synthesis_reason"] == syn.DISABLED, out
    out, _ = await run(connect, world, deps, q, None)
    assert out["synthesis_reason"] == syn.DISABLED, out


async def test_breaker_and_in_flight_cap(connect, world, deps, weak_ids, db_dsn) -> None:  # noqa: ANN001
    q = weak_question(weak_ids)["question"]
    llm = ScriptedLLM(default=("stall", 30.0, {"status": "insufficient_evidence", "sentences": []}))
    synth = stub_synth(db_dsn, llm, timeout_s=0.3)
    try:
        reasons = [(await run(connect, world, deps, q, synth))[0]["synthesis_reason"] for _ in range(4)]
        assert reasons == [syn.TIMEOUT] * syn.BREAKER_THRESHOLD + [syn.UNAVAILABLE], reasons
        synth.breaker.success()
        n = syn.MAX_IN_FLIGHT + 2
        outs = await asyncio.gather(*(run(connect, world, deps, q, synth) for _ in range(n)))
        got = sorted(o[0]["synthesis_reason"] for o in outs)
        assert got.count(syn.BUSY) == 2 and got.count(syn.TIMEOUT) == syn.MAX_IN_FLIGHT, got
    finally:
        await synth.aclose()


# --------------------------------------------------------------------------- privacy default-deny
async def _write(connect, ctx, items: list[dict[str, Any]]) -> list[int]:  # noqa: ANN001
    async with await connect() as conn:
        ack = await write(
            conn,
            ctx,
            {"project": PROJECT, "request_id": str(uuid.uuid4()), "client": "pytest-w2e/0", "items": items},
            deps=default_deps(),
        )
        await conn.commit()
    return [v.version_id for v in ack.versions]


async def test_privacy_default_deny(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    from hlmemo.worker.main import drain

    async with await connect() as conn:
        cur = await conn.execute(
            "INSERT INTO projects (slug, name) VALUES ('syn-secret', 'ungranted') RETURNING project_id"
        )
        (secret,) = await cur.fetchone()
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'write', 1)",
            (world.device_id, secret),
        )
        await conn.commit()
    loader = replace(world.ctx, grants={**world.ctx.grants, secret: world.ctx.grants[world.project_id]})
    scoped = f"device:{world.device_id}"
    await _write(
        connect,
        loader,
        [
            {
                "kind": "fact",
                "title": "Zebra-plum port",
                "body": "The zebra-plum relay listens on port 45123.",
                "device_scope": scoped,
            },
            {
                "kind": "fact",
                "title": "Zebra-plum owner",
                "body": "The zebra-plum relay is owned by team 9-orchid.",
                "project_ids": [PROJECT, "syn-secret"],
            },
            {
                "kind": "fact",
                "title": "Zebra-plum region",
                "body": "The zebra-plum relay runs in region eu-kilo-7.",
            },
        ],
    )
    async with await connect() as conn:  # the reader keeps no grant on syn-secret
        await conn.execute(
            "UPDATE device_project_grants SET revoked_at = now() WHERE device_id = %s AND project_id = %s",
            (world.device_id, secret),
        )
        await conn.commit()
    await drain(connect, deps.embedder)
    llm = ScriptedLLM(default=lambda body: answer_citing(_ids(body), 1))
    synth = stub_synth(db_dsn, llm)
    q = "zebra-plum relay port owner region?"
    try:
        out, _ = await run(connect, world, deps, q, synth, tau=1.0)
        check_response(out)
        titles = [h["title"] for h in out["hits"][:10]]
        assert {"Zebra-plum port", "Zebra-plum owner", "Zebra-plum region"} <= set(titles), titles
        sent = "\n".join(_excerpt_texts(b) for b in llm.requests)
        assert "eu-kilo-7" in sent  # an ordinary item is sent
        assert "45123" not in sent and "9-orchid" not in sent  # device-scoped / co-owned: never
        # policy.librarian=off on the home project: nothing may leave the host
        async with await connect() as conn:
            await conn.execute(
                'UPDATE projects SET policy = policy || \'{"librarian": "off"}\' WHERE project_id = %s',
                (world.project_id,),
            )
            await conn.commit()
        n0 = llm.calls
        out, _ = await run(connect, world, deps, q, synth, tau=1.0)
        assert out["synthesis_reason"] == syn.WITHHELD and llm.calls == n0, out
    finally:
        async with await connect() as conn:
            await conn.execute(
                "UPDATE projects SET policy = policy - 'librarian' WHERE project_id = %s", (world.project_id,)
            )
            await conn.commit()
        await synth.aclose()


# --------------------------------------------------------------------------- D-062
async def test_cited_visibility_rechecked_after_the_call(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """An item superseded while the LLM runs loses its citation; a revoked home grant discards all."""
    vids = await _write(
        connect,
        world.ctx,
        [{"kind": "fact", "title": "Quokka switch", "body": "The quokka switch defaults to mode amber-3."}],
    )
    from hlmemo.worker.main import drain

    await drain(connect, deps.embedder)
    q = "quokka switch default mode?"

    async def revise(_body: dict[str, Any]) -> None:
        async with await connect() as conn:
            (lid,) = await (
                await conn.execute("SELECT logical_id FROM memory_versions WHERE version_id = %s", (vids[0],))
            ).fetchone()
            await conn.commit()
        await _write(
            connect,
            world.ctx,
            [
                {
                    "kind": "fact",
                    "title": "Quokka switch",
                    "body": "The quokka switch defaults to mode teal-9.",
                    "logical_id": lid,
                    "expected_version_id": vids[0],
                }
            ],
        )

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        content = body["messages"][1]["content"]
        payload = json.loads(content.split("INPUT: ", 1)[1])
        cid = next(e["id"] for e in payload["excerpts"] if "amber-3" in e["text"])
        return {"status": "answered", "sentences": [{"text": "It defaults to amber-3.", "cite": [cid]}]}

    llm = ScriptedLLM(default=answer, on_request=revise)
    synth = stub_synth(db_dsn, llm)
    try:
        out, _ = await run(connect, world, deps, q, synth, tau=1.0)
        assert llm.calls == 1 and out["synthesis_reason"] == syn.GUARD, out  # the only citation is stale now
    finally:
        await synth.aclose()
    llm = ScriptedLLM(default=lambda body: answer_citing(_ids(body), 1))

    async def drop_home(_body: dict[str, Any]) -> None:
        async with await connect() as conn:
            await conn.execute(
                "UPDATE device_project_grants SET revoked_at = now()"
                " WHERE device_id = %s AND project_id = %s",
                (world.device_id, world.project_id),
            )
            await conn.commit()

    llm.on_request = drop_home
    synth = stub_synth(db_dsn, llm)
    try:
        with pytest.raises(ToolError) as ei:
            await run(connect, world, deps, q, synth, tau=1.0)
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
    finally:
        async with await connect() as conn:
            await conn.execute(
                "UPDATE device_project_grants SET revoked_at = NULL WHERE device_id = %s AND project_id = %s",
                (world.device_id, world.project_id),
            )
            await conn.commit()
        await synth.aclose()


def _install(app: Any, synth: syn.Synthesizer) -> None:
    app.state.synthesizer = synth


def _api_synth(app: Any, llm: ScriptedLLM, **kw: Any) -> syn.Synthesizer:
    settings = app.state.settings.model_copy(
        update={"librarian_enabled": True, "llm_mode": "live", "llm_budget_disabled": True}
    )
    synth = syn.Synthesizer(settings, chain=stub_chain(fallback=False), transport=llm.transport, **kw)
    _install(app, synth)
    return synth


async def _reader(client: httpx.AsyncClient, name: str) -> tuple[int, str]:
    return await trusted_device(client, name, grants=[{"project": PROJECT, "role": "read"}])


async def test_d062_revoke_during_synthesis_discards_the_result(db_dsn, world, weak_ids) -> None:  # noqa: ANN001
    q = weak_question(weak_ids)["question"]
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        did, token = await _reader(client, "syn-revoked")
        revoke_s: list[float] = []

        async def revoke(_body: dict[str, Any]) -> None:
            t0 = time.perf_counter()
            r = await client.post(f"/admin/devices/{did}/revoke", headers=bearer(ADMIN_TOKEN), json={})
            revoke_s.append(time.perf_counter() - t0)
            assert r.status_code == 200, r.text

        llm = ScriptedLLM(default=lambda body: answer_citing(_ids(body), 1), on_request=revoke)
        synth = _api_synth(app, llm)
        try:
            res = await call_tool_raw(client, token, "memory.query", {**_args(q), "synthesize": True})
        finally:
            await synth.aclose()
        out = json.loads(res["content"][0]["text"])
        # the revocation committed while the LLM ran (no lock held), and the result was discarded
        assert llm.calls == 1 and revoke_s and revoke_s[0] < 1.5, revoke_s
        assert res["isError"] is True and out["code"] == "E_AUTH", out


@contextlib.asynccontextmanager
async def small_pool_app(db_dsn: str, pool_max: int) -> AsyncIterator[httpx.AsyncClient]:
    settings = get_settings(
        db_dsn=db_dsn,
        admin_token=ADMIN_TOKEN,
        registration_secret=None,
        pool_min_size=1,
        pool_max_size=pool_max,
    )
    app = create_app(settings, register_rate_limit=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_d062_pool_not_held_during_synthesis(db_dsn, world, weak_ids) -> None:  # noqa: ANN001
    """pool_max_size=2 and a model that takes 1.5 s: three concurrent syntheses and a stream of plain
    queries all finish promptly (each synthesis returned its connection before the LLM)."""
    qs = {q["id"]: q for q in load_questions()}
    async with small_pool_app(db_dsn, 2) as client:
        app = client.app  # type: ignore[attr-defined]
        _did, token = await _reader(client, "syn-pool")
        llm = ScriptedLLM(default=("stall", 1.5, {"status": "insufficient_evidence", "sentences": []}))
        synth = _api_synth(app, llm)
        q_times: list[float] = []

        async def synthesize(question: str) -> tuple[float, dict[str, Any]]:
            t0 = time.perf_counter()
            res = await call_tool_raw(client, token, "memory.query", {**_args(question), "synthesize": True})
            return time.perf_counter() - t0, json.loads(res["content"][0]["text"])

        async def queries() -> None:
            await asyncio.sleep(0.5)  # the syntheses are inside their LLM call by now
            for i in range(6):
                t0 = time.perf_counter()
                await call_tool_raw(client, token, "memory.query", _args(qs[weak_ids[i]]["question"], 800))
                q_times.append(time.perf_counter() - t0)

        try:
            got = await asyncio.gather(*(synthesize(qs[i]["question"]) for i in weak_ids[:3]), queries())
        finally:
            await synth.aclose()
        syns = [g for g in got if isinstance(g, tuple)]
        print(
            f"\nD-062 pool=2: synthesis {[round(t, 2) for t, _ in syns]} s; "
            f"plain queries meanwhile max {max(q_times) * 1000:.0f} ms"
        )
        assert all(o["synthesis"]["status"] == "insufficient_evidence" for _, o in syns), syns
        assert max(t for t, _ in syns) < 3.5, syns
        assert max(q_times) < 1.0, q_times


# --------------------------------------------------------------------------- stalled model (real API)
async def test_stalled_model_through_api(db_dsn, world, weak_ids) -> None:  # noqa: ANN001
    qs = {q["id"]: q for q in load_questions()}
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        _did, token = await _reader(client, "syn-stall")
        for label, entry in (
            ("stall", ("stall", 30.0, {"status": "insufficient_evidence", "sentences": []})),
            ("503", 503),
        ):
            llm = ScriptedLLM(default=entry)
            synth = _api_synth(app, llm)
            times: list[float] = []
            q_times: list[float] = []
            reasons: list[str] = []
            try:
                for qid in weak_ids[:12]:
                    t0 = time.perf_counter()
                    res = await call_tool_raw(
                        client, token, "memory.query", {**_args(qs[qid]["question"]), "synthesize": True}
                    )
                    times.append(time.perf_counter() - t0)
                    out = json.loads(res["content"][0]["text"])
                    assert not res.get("isError"), out
                    assert out["synthesis_unavailable"] is True and "synthesis" not in out, out
                    reasons.append(out["synthesis_reason"])
                synth.breaker.success()  # a burst of stalled syntheses next to plain queries

                async def one(question: str) -> float:
                    t0 = time.perf_counter()
                    res = await call_tool_raw(
                        client, token, "memory.query", {**_args(question), "synthesize": True}
                    )
                    assert json.loads(res["content"][0]["text"])["synthesis_unavailable"] is True
                    return time.perf_counter() - t0

                async def plain(sink: list[float] = q_times) -> None:
                    for i in range(5):
                        t0 = time.perf_counter()
                        await call_tool_raw(
                            client, token, "memory.query", _args(qs[weak_ids[i]]["question"], 1000)
                        )
                        sink.append(time.perf_counter() - t0)

                burst = await asyncio.gather(
                    *(one(qs[i]["question"]) for i in weak_ids[12:16]), plain(), plain()
                )
                times += [t for t in burst if isinstance(t, float)]
            finally:
                await synth.aclose()
            p95 = statistics.quantiles(times, n=20, method="inclusive")[18]
            qp95 = statistics.quantiles(q_times, n=20, method="inclusive")[18]
            print(
                f"\nW2e stalled model ({label}): p50={statistics.median(times):.2f}s p95={p95:.2f}s "
                f"max={max(times):.2f}s n={len(times)} reasons={sorted(set(reasons))}; "
                f"plain queries meanwhile p95={qp95 * 1000:.0f} ms"
            )
            assert p95 <= P95_STALL_S and max(times) <= P95_STALL_S + 0.5, times
            assert set(reasons) <= {syn.TIMEOUT, syn.UNAVAILABLE}, reasons
            assert qp95 <= 1.0, q_times  # the core never waits on the synthesis


# --------------------------------------------------------------------------- G2 with synthesis
async def test_g2_budget_with_synthesis_on_the_wire(db_dsn, world, weak_ids) -> None:  # noqa: ANN001
    rng = random.Random(20260924)
    budgets = [256, 257, 300, 400, 32000, *(rng.randint(256, 3500) for _ in range(115))]
    long = "Stated in the excerpt, with its identifier kept verbatim and more words to take room"

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        ids = _ids(body)
        cites = [[ids[0]], ids[: min(3, len(ids))], [ids[-1]], [ids[len(ids) // 2]]]
        return {
            "status": "answered",
            "sentences": [{"text": f"{long} {i}.", "cite": c} for i, c in enumerate(cites)],
        }

    qs = {q["id"]: q for q in load_questions()}
    q = qs[weak_ids[1]]["question"]
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        _did, token = await _reader(client, "syn-budget")
        llm = ScriptedLLM(default=answer)
        synth = _api_synth(app, llm)
        outcomes: dict[str, int] = {}
        try:
            for budget in budgets:
                res = await call_tool_raw(
                    client, token, "memory.query", {**_args(q, budget), "synthesize": True}
                )
                text = res["content"][0]["text"]
                out = json.loads(text)
                if res.get("isError"):
                    assert out["code"] == "E_BUDGET_TOO_SMALL" and out["details"]["min"] > budget, out
                    outcomes["too_small"] = outcomes.get("too_small", 0) + 1
                    continue
                assert METER.count_text(text) == out["budget"]["used"] <= budget, (budget, out["budget"])
                check_response(out)
                k = "synthesis" if "synthesis" in out else out["synthesis_reason"]
                outcomes[k] = outcomes.get(k, 0) + 1
        finally:
            await synth.aclose()
    print(f"\nW2e G2 with synthesis: {outcomes} over {len(budgets)} budgets, 0 overflow")
    assert outcomes.get("synthesis", 0) >= 100, outcomes


# --------------------------------------------------------------------------- no flag = unchanged
#: a fixed (future) bi-temporal point: the as_of block is then deterministic (the live path: a
#: future point is not historical, so the D-055 term filter runs as for "now")
AS_OF = {"valid_at": "2030-01-01T00:00:00Z", "known_at": "2030-01-01T00:00:00Z"}


def _phase0_spec() -> Any:
    """The Phase-0 ``memory.query`` registration (before W2e), for the A/B below."""
    spec = TOOL_BY_NAME["memory.query"]
    return replace(spec, input_schema=schemas.QUERY_INPUT, handler=handlers.memory_query, app_bound=False)


async def test_no_flag_is_byte_identical_and_as_fast(db_dsn, world, monkeypatch) -> None:  # noqa: ANN001
    questions = [q["question"] for q in load_questions()][:40]
    new_spec, old_spec = TOOL_BY_NAME["memory.query"], _phase0_spec()
    async with running_app(db_dsn) as client:
        _did, token = await _reader(client, "syn-ab")

        async def call(spec: Any, args: dict[str, Any]) -> tuple[str, float]:
            monkeypatch.setitem(TOOL_BY_NAME, "memory.query", spec)
            t0 = time.perf_counter()
            res = await call_tool_raw(client, token, "memory.query", args)
            dt = time.perf_counter() - t0
            assert not res.get("isError"), res
            return res["content"][0]["text"], dt

        for q in questions[:5]:  # warm-up
            await call(new_spec, _args(q))
        new_t: list[float] = []
        old_t: list[float] = []
        for rnd in range(3):
            for i, q in enumerate(questions):
                budget = 600 + 97 * i
                first, second = (old_spec, new_spec) if (i + rnd) % 2 else (new_spec, old_spec)
                a_text, a_t = await call(first, {**_args(q, budget), **AS_OF})
                b_text, b_t = await call(second, {**_args(q, budget), **AS_OF})
                assert a_text == b_text  # byte-identical output
                (old_t if first is old_spec else new_t).append(a_t)
                (new_t if first is old_spec else old_t).append(b_t)
                if rnd == 0 and i < 10:
                    f_text, _ = await call(new_spec, {**_args(q, budget), **AS_OF, "synthesize": False})
                    assert f_text == a_text
        monkeypatch.setitem(TOOL_BY_NAME, "memory.query", new_spec)
    p95 = lambda xs: statistics.quantiles(xs, n=20, method="inclusive")[18] * 1000  # noqa: E731
    print(
        f"\nW2e no-flag A/B through the API ({len(new_t)} calls each): Phase-0 path p50/p95 "
        f"{statistics.median(old_t) * 1000:.1f}/{p95(old_t):.1f} ms, W2e path "
        f"{statistics.median(new_t) * 1000:.1f}/{p95(new_t):.1f} ms"
    )
    assert p95(new_t) <= p95(old_t) * 1.25 + 10.0


# --------------------------------------------------------------------------- G-SURF + wire contract
async def test_g_surf_and_wire_contract(db_dsn, world) -> None:  # noqa: ANN001
    async with running_app(db_dsn) as client:
        _did, token = await _reader(client, "syn-surf")
        r = await mcp_rpc(client, token, "tools/list")
        tools = r.json()["result"]["tools"]
        q_tool = next(t for t in tools if t["name"] == "memory.query")
        assert q_tool["inputSchema"]["properties"]["synthesize"]["type"] == "boolean"
        n = Meter().count_text(json.dumps(r.json()["result"], ensure_ascii=False, separators=(",", ":")))
        print(f"\nG-SURF: tools/list = {n} o200k tokens for {len(tools)} tools (limit 3000)")
        assert n <= 3000
        res = await call_tool_raw(client, token, "memory.query", {**_args("x"), "synthesize": "yes"})
        assert res["isError"] and json.loads(res["content"][0]["text"])["code"] == "E_INVALID_ARG"
        # the librarian is off in this app: a synthesize call still answers, labelled
        res = await call_tool_raw(
            client, token, "memory.query", {**_args("why is the sky green?"), "synthesize": True}
        )
        out = json.loads(res["content"][0]["text"])
        check_response(out)
        assert out["synthesis_reason"] in (syn.DISABLED, ss.STRONG), out
