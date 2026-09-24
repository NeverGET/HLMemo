"""W2d gates for ``memory.risk_check`` (PHASE2-4-ROADMAP W2d; D-014, D-062, D-067).

Fixture world: ``tests/fixtures/risk`` (50 lessons from the real footguns of docs/decisions and
docs/consults, 40 positive + 40 negative tasks), seeded once per module (``_risk_fixtures``).

* **G-R1** CI replay: every case through the full pipeline (deterministic stage + judge) with the
  judge's answers replayed strictly from ``tests/cassettes/w2d`` (recorded from the primary profile,
  ``HLM_W2D_RECORD=1`` + OPENROUTER_API_KEY). Asserts: every case judged, every warning cites a
  candidate clue (D-067), catch ≥ 0.85 and false-warn ≤ 0.10 on the recorded answers.
* **G-R2** deterministic: catch ≥ 0.70 and p95 ≤ 300 ms (``mode="deterministic"``).
* **G-R3** judge stalled (or 503) through the REAL API: every call ``judged:false`` and p95 ≤ 4.5 s;
  queries stay fast while judges stall.
* **G-R4** G2 budget through the wire: ``budget.used`` is the exact count of the text on the wire and
  ≤ limit for 120 random budgets (deterministic and judged with long ``why`` texts).
* Isolation/privacy: an ungranted project's lesson and another device class's lesson are never
  candidates; device-scoped and ``hlm-global`` (policy librarian=off) lessons never reach the judge.
* D-067 guard, verdict/matches consistency, fallback tier, qualification, budget stop, breaker.
* G-SURF: ``tools/list`` ≤ 3,000 o200k tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import statistics
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import risk_service as rs
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian import risk_judge as rj
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import LATENCY_PRIMARY_SHARE
from hlmemo.server.app import create_app
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    chat,
    stub_chain,
    stub_profile,
    timeout_honouring,
)
from tests.integration._mcp_fixtures import (
    ADMIN_TOKEN,
    bearer,
    call_tool_raw,
    mcp_rpc,
    running_app,
    trusted_device,
)
from tests.integration._risk_fixtures import (
    MAIN,
    load_cases,
    load_env_file,
    load_library,
    rates,
    score_case,
    seed_world,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "tests" / "cassettes" / "w2d"
PRIMARY_PROFILE = "openrouter-gpt6-luna"
P95_DET_MS = 300.0
P95_STALL_S = 4.5
BUDGET = 4000


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_world(connect, deps.embedder)


def judge_settings(db_dsn: str, **kw: Any):  # noqa: ANN201
    base = {"db_dsn": db_dsn, "librarian_enabled": True, "llm_mode": "live", "llm_budget_disabled": True}
    return get_settings(**{**base, **kw})


async def run_case(
    connect,  # noqa: ANN001
    world,  # noqa: ANN001
    deps,  # noqa: ANN001
    task: str,
    *,
    mode: str = "auto",
    judge: rj.RiskJudge | None = None,
    budget: int = BUDGET,
) -> tuple[dict[str, Any], list[str | None], float]:
    async with await connect() as conn:
        await conn.commit()  # an idle connection: the judge never runs inside a caller's tx (D-062)
        t0 = time.perf_counter()
        out = await rs.risk_check(
            conn,
            world.ctx_reader,
            {"project": MAIN, "task": task, "token_budget": budget, "mode": mode},
            deps=deps,
            judge=judge,
        )
        ms = (time.perf_counter() - t0) * 1000
        await conn.commit()
    return out, [world.lesson_of_clue(w["clue"]) for w in out["warnings"]], ms


async def candidate_clues(connect, world, deps, task: str) -> set[str]:  # noqa: ANN001
    async with await connect() as conn:
        cands, _ = await rs.candidates(conn, world.ctx_reader, world.projects[MAIN], task, deps)
        await conn.commit()
    return {c.clue for c in cands}


def p95(xs: list[float]) -> float:
    return statistics.quantiles(xs, n=20, method="inclusive")[18] if len(xs) > 1 else xs[0]


# --------------------------------------------------------------------------- G-R2
async def test_g_r2_deterministic_catch_and_latency(connect, world, deps) -> None:  # noqa: ANN001
    cases = load_cases()
    await run_case(connect, world, deps, "warm-up: deploy with docker compose", mode="deterministic")
    scored, times = [], []
    hard, ordinary = [], []
    for case in cases:
        out, lessons, ms = await run_case(connect, world, deps, case["task"], mode="deterministic")
        assert out["judged"] is False and out["judge"] == rj.RETRIEVAL_ONLY, out
        assert out["reason"] == rj.NOT_REQUESTED, out
        s = score_case(case, out["verdict"] == rs.VERDICT_WARN, lessons)
        scored.append(s)
        times.append(ms)
        if not case["gold"]:
            (hard if case.get("near") else ordinary).append(s["false_warn"])
    r = rates(scored)
    lat = p95(times)
    print(
        f"\nG-R2 deterministic: catch={r['catch']:.3f} false_warn={r['false_warn']:.3f} "
        f"(hard near-miss {sum(hard)}/{len(hard)}, ordinary {sum(ordinary)}/{len(ordinary)}) "
        f"p50={statistics.median(times):.1f} ms p95={lat:.1f} ms n={len(times)}"
    )
    assert r["catch"] >= 0.70, r
    assert lat <= P95_DET_MS, lat


# --------------------------------------------------------------------------- G-R1
async def test_g_r1_ci_replay(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    record = os.environ.get("HLM_W2D_RECORD") == "1"
    if record:
        load_env_file()
    if not record and not any(CASSETTES.glob("*.jsonl")):
        pytest.fail(f"no cassettes under {CASSETTES}: record them with HLM_W2D_RECORD=1 (live, primary)")
    settings = judge_settings(db_dsn, llm_mode="record" if record else "replay", llm_cassette_dir=CASSETTES)
    judge = rj.RiskJudge(settings, chain=[named_profile(PRIMARY_PROFILE)], cassette_dir=CASSETTES)
    if record:  # a recording run needs the network: no 4 s cap
        judge.timeout_s = 60.0
    try:
        scored = []
        for case in load_cases():
            out, lessons, _ = await run_case(connect, world, deps, case["task"], judge=judge)
            assert out["judge"] == rj.OK and out["judged"] is True, (case["id"], out)
            allowed = await candidate_clues(connect, world, deps, case["task"])
            assert {w["clue"] for w in out["warnings"]} <= allowed, (case["id"], out)  # D-067
            assert all(len(w["why"]) <= rs.WHY_MAX for w in out["warnings"])
            scored.append(score_case(case, out["verdict"] == rs.VERDICT_WARN, lessons))
    finally:
        await judge.aclose()
    r = rates(scored)
    print(f"\nG-R1 replay ({PRIMARY_PROFILE}): catch={r['catch']:.3f} false_warn={r['false_warn']:.3f}")
    assert r["catch"] >= 0.85 and r["false_warn"] <= 0.10, r


# --------------------------------------------------------------------------- isolation / privacy
async def test_isolation_and_privacy_default_deny(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    lib = {les["id"]: les for les in load_library()["lessons"]}
    llm = ScriptedLLM(default={"verdict": "none", "matches": []})
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    invisible = {world.version_of_lesson["L50"], world.version_of_lesson["L22"]}  # no grant / class:work
    withheld_text = [lib[k]["mistake"][:60] for k in ("L16", "L27", "L28", "L36", "L22", "L50")]
    try:
        p16 = None
        for case in load_cases():
            out, lessons, _ = await run_case(connect, world, deps, case["task"], judge=judge)
            assert out["judged"] is True, out
            clues = await candidate_clues(connect, world, deps, case["task"])
            assert not {f"v{v}" for v in invisible} & clues, case["id"]
            if case["id"] == "P16":
                p16 = out
        for body in llm.requests:
            prompt = body["messages"][1]["content"]
            for snippet in withheld_text:
                assert json.dumps(snippet, ensure_ascii=False)[1:-1] not in prompt, snippet
        # the device-scoped lesson (never sent) still warns deterministically at TAU_STRICT
        assert p16 is not None and p16["verdict"] == rs.VERDICT_WARN, p16
        assert any("withheld by the privacy policy" in w["why"] for w in p16["warnings"]), p16
        assert f"v{world.version_of_lesson['L16']}" in {w["clue"] for w in p16["warnings"]}
    finally:
        await judge.aclose()


# --------------------------------------------------------------------------- D-067 guards
async def test_guard_drops_uncited_matches(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(
        default={
            "verdict": "warn",
            "matches": [{"id": "R99", "why": "invented"}, {"id": "v1", "why": "a clue, not a shown id"}],
        }
    )
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, load_cases()[0]["task"], judge=judge)
    finally:
        await judge.aclose()
    # an all-uncited warn is a judge failure: retrieval-only, never a judged "no matching evidence"
    assert out["judged"] is False and out["reason"] == rj.GUARD and out["guard_dropped"] == 2, out
    assert out["verdict"] == rs.VERDICT_WARN  # P01's lesson matches deterministically
    assert all("not checked by the librarian LLM (guard)" in w["why"] for w in out["warnings"]), out


def _id_of(body: dict[str, Any], needle: str) -> str:
    """The local id (R<n>) the prompt gave the lesson whose text contains ``needle``."""
    lessons = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])["lessons"]
    return next(les["id"] for les in lessons if needle in les["text"])


async def test_guard_keeps_cited_matches_and_counts_the_rest(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    def answer(body: dict[str, Any]) -> dict[str, Any]:
        good = {"id": _id_of(body, "bash -s"), "why": "pipes the script into bash -s"}
        return {"verdict": "warn", "matches": [{"id": "R99", "why": "invented"}, good]}

    llm = ScriptedLLM(default=answer)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, lessons, _ = await run_case(connect, world, deps, load_cases()[0]["task"], judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is True and out["judge"] == rj.OK and out["guard_dropped"] == 1, out
    assert lessons == ["L01"], out


async def test_verdict_matches_mismatch_retried_then_schema_fail(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    task = load_cases()[0]["task"]
    ok = {"verdict": "warn", "matches": [{"id": "R2", "why": "pipes the script into bash -s"}]}
    llm = ScriptedLLM(script=[{"verdict": "warn", "matches": []}, ok])
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, task, judge=judge)
        assert out["judge"] == rj.OK and llm.calls == 2 and len(out["warnings"]) == 1, out
        llm.script = [{"verdict": "none", "matches": ok["matches"]}, {"verdict": "warn", "matches": []}]
        out, _, _ = await run_case(connect, world, deps, task, judge=judge)
        assert out["reason"] == rj.SCHEMA_FAIL and out["judged"] is False, out
        assert out["judge"] == rj.RETRIEVAL_ONLY, out
        assert all("not checked by the librarian LLM" in w["why"] for w in out["warnings"]), out
    finally:
        await judge.aclose()


async def test_fallback_tier_is_reported(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    def answer(body: dict[str, Any]) -> Any:
        return 400 if body["model"].endswith("stub-primary") else {"verdict": "none", "matches": []}

    llm = ScriptedLLM(default=answer)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, "Add a README badge", judge=judge)
    finally:
        await judge.aclose()
    assert out["judge"] == rj.OK_FALLBACK and out["judged"] is True, out


async def test_qualification_excludes_disabled_profiles(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    (tmp_path / "unqualified.toml").write_text(
        'HLM_LLM_BASE_URL = "http://x.invalid/v1"\nHLM_LLM_MODEL = "stub/x"\n'
        'disabled_tasks = ["risk_judge"]\n'
    )
    (tmp_path / "qualified.toml").write_text(
        'HLM_LLM_BASE_URL = "http://y.invalid/v1"\nHLM_LLM_MODEL = "stub/y"\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    assert rj.disabled_tasks("unqualified") == {"risk_judge"}
    assert rj.disabled_tasks("qualified") == set()
    settings = get_settings(
        profile="qualified",
        fallback_profile="unqualified",
        llm_base_url="http://y.invalid/v1",
        llm_model="stub/y",
    )
    assert [p.name for p in rj.judge_chain(settings)] == ["qualified"]


async def test_budget_stop_and_disabled_and_deterministic(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default={"verdict": "none", "matches": []})
    task = load_cases()[0]["task"]
    judge = rj.RiskJudge(
        judge_settings(db_dsn, llm_budget_disabled=False, llm_budget_hour_usd=0.0),
        chain=stub_chain(fallback=False),
        transport=llm.transport,
    )
    try:
        out, _, _ = await run_case(connect, world, deps, task, judge=judge)
        assert out["reason"] == rj.BUDGET and out["judged"] is False and llm.calls == 0, out
    finally:
        await judge.aclose()
    off = rj.RiskJudge(judge_settings(db_dsn, librarian_enabled=False), chain=stub_chain(fallback=False))
    out, _, _ = await run_case(connect, world, deps, task, judge=off)
    assert out["reason"] == rj.DISABLED and out["judge"] == rj.RETRIEVAL_ONLY, out
    out, _, _ = await run_case(connect, world, deps, task, mode="deterministic", judge=off)
    assert out["reason"] == rj.NOT_REQUESTED, out
    assert out["verdict"] == rs.VERDICT_WARN  # P01's lesson matches deterministically


async def test_breaker_and_in_flight_cap(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=("stall", 30.0, {"verdict": "none", "matches": []}))
    judge = rj.RiskJudge(
        judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport, timeout_s=1.0
    )  # room for an attempt: breaker failures are counted per profile by the provider now
    task = load_cases()[0]["task"]
    try:
        statuses = [(await run_case(connect, world, deps, task, judge=judge))[0]["reason"] for _ in range(4)]
        assert statuses == [rj.TIMEOUT] * rj.BREAKER_THRESHOLD + [rj.UNAVAILABLE], statuses
        judge.breaker.success()
        n = rj.MAX_IN_FLIGHT + 2
        outs = await asyncio.gather(*(run_case(connect, world, deps, task, judge=judge) for _ in range(n)))
        got = sorted(o[0]["reason"] for o in outs)
        assert got.count(rj.BUSY) == 2 and got.count(rj.TIMEOUT) == rj.MAX_IN_FLIGHT, got
    finally:
        await judge.aclose()


# --------------------------------------------------------------------------- G-R3 (real API)
async def _g_r3_round(client, token: str, entry: Any) -> tuple[list[float], list[float]]:  # noqa: ANN001
    """12 sequential risk checks, then a burst of 4 concurrent ones next to 10 queries."""
    app = client.app
    llm = ScriptedLLM(default=entry)
    settings = app.state.settings.model_copy(
        update={"librarian_enabled": True, "llm_mode": "live", "llm_budget_disabled": False}
    )
    app.state.risk_judge = rj.RiskJudge(settings, chain=stub_chain(), transport=llm.transport)
    times: list[float] = []
    q_times: list[float] = []
    for case in load_cases()[:12]:
        args = {"project": MAIN, "task": case["task"], "token_budget": 2000}
        t0 = time.perf_counter()
        res = await call_tool_raw(client, token, "memory.risk_check", args)
        times.append(time.perf_counter() - t0)
        out = json.loads(res["content"][0]["text"])
        assert not res.get("isError"), out
        assert out["judged"] is False and out["reason"] in (rj.TIMEOUT, rj.UNAVAILABLE), out
    app.state.risk_judge.breaker.success()  # burst with the judge stalling again

    async def risk() -> float:
        t0 = time.perf_counter()
        args = {"project": MAIN, "task": "deploy", "token_budget": 800}
        res = await call_tool_raw(client, token, "memory.risk_check", args)
        assert json.loads(res["content"][0]["text"])["judged"] is False
        return time.perf_counter() - t0

    async def query() -> None:
        for _ in range(5):
            t0 = time.perf_counter()
            args = {"project": MAIN, "query": "ssh stdin", "token_budget": 1000}
            await call_tool_raw(client, token, "memory.query", args)
            q_times.append(time.perf_counter() - t0)

    burst = await asyncio.gather(*(risk() for _ in range(4)), query(), query())
    times += [t for t in burst if isinstance(t, float)]
    await app.state.risk_judge.aclose()
    return times, q_times


async def test_g_r3_stalled_judge_through_api(db_dsn, world) -> None:  # noqa: ANN001
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(client, "rk-api", grants=[{"project": MAIN, "role": "write"}])
        for label, entry in (("stall", ("stall", 30.0, {"verdict": "none", "matches": []})), ("503", 503)):
            times, q_times = await _g_r3_round(client, token, entry)
            print(
                f"\nG-R3 {label}: p50={statistics.median(times):.2f}s p95={p95(times):.2f}s "
                f"max={max(times):.2f}s n={len(times)}; queries during stall p95={p95(q_times) * 1000:.0f} ms"
            )
            assert p95(times) <= P95_STALL_S and max(times) <= P95_STALL_S + 0.5, times
            assert p95(q_times) <= 1.0, q_times  # the core never waits on the judge


# --------------------------------------------------------------------------- G-R4 (wire budget)
async def test_g_r4_budget_on_the_wire(db_dsn, world) -> None:  # noqa: ANN001
    meter = Meter()
    rng = random.Random(20260924)
    budgets = [256, 257, 300, 400, 32000, *(rng.randint(256, 3000) for _ in range(115))]
    long_why = "Repeats the stdin-swallowing mistake: " + "the piped script is consumed by a child. " * 8
    llm = ScriptedLLM(
        default={"verdict": "warn", "matches": [{"id": f"R{i}", "why": long_why} for i in (1, 2, 3)]}
    )
    task = load_cases()[0]["task"]
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        _did, token = await trusted_device(client, "rk-budget", grants=[{"project": MAIN, "role": "write"}])
        settings = app.state.settings.model_copy(update={"librarian_enabled": True, "llm_mode": "live"})
        app.state.risk_judge = rj.RiskJudge(
            settings, chain=stub_chain(fallback=False), transport=llm.transport
        )
        ok = small = 0
        for i, budget in enumerate(budgets):
            mode = "deterministic" if i % 2 else "auto"
            args = {"project": MAIN, "task": task, "token_budget": budget, "mode": mode}
            res = await call_tool_raw(client, token, "memory.risk_check", args)
            text = res["content"][0]["text"]
            out = json.loads(text)
            if res.get("isError"):
                assert out["code"] == "E_BUDGET_TOO_SMALL" and out["details"]["min"] > budget, out
                small += 1
                continue
            used = meter.count_text(text)
            assert used <= budget and out["budget"] == {
                "limit": budget,
                "used": used,
                "tokenizer": "o200k_base",
            }
            assert len(out["warnings"]) + out["omitted"] >= 1, out
            ok += 1
        await app.state.risk_judge.aclose()
        for bad in (255, 32001):
            res = await call_tool_raw(
                client, token, "memory.risk_check", {"project": MAIN, "task": task, "token_budget": bad}
            )
            assert res["isError"] and json.loads(res["content"][0]["text"])["code"].startswith("E_BUDGET_TOO")
    print(f"\nG-R4: {ok} packed within budget, {small} E_BUDGET_TOO_SMALL, 0 overflow")
    assert ok >= 100


# --------------------------------------------------------------------------- G-SURF + authz
async def test_g_surf_tools_list_budget_and_authz(db_dsn, world) -> None:  # noqa: ANN001
    async with running_app(db_dsn) as client:
        _did, token = await trusted_device(
            client, "rk-surf", grants=[{"project": "rk-shell", "role": "read"}]
        )
        r = await mcp_rpc(client, token, "tools/list")
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"memory.risk_check", "memory.register_lesson"} <= names
        n = Meter().count_text(json.dumps(r.json()["result"], ensure_ascii=False, separators=(",", ":")))
        print(f"\nG-SURF: tools/list = {n} o200k tokens for {len(tools)} tools (limit 3000)")
        assert n <= 3000
        # no read grant on the project -> E_FORBIDDEN_PROJECT (same error for unknown slugs)
        for project in (MAIN, "no-such-project"):
            res = await call_tool_raw(
                client, token, "memory.risk_check", {"project": project, "task": "x", "token_budget": 1000}
            )
            assert res["isError"] and json.loads(res["content"][0]["text"])["code"] == "E_FORBIDDEN_PROJECT"
        res = await call_tool_raw(
            client, token, "memory.risk_check", {"project": "rk-shell", "task": "", "token_budget": 1000}
        )
        assert res["isError"] and json.loads(res["content"][0]["text"])["code"] == "E_INVALID_ARG"


# --------------------------------------------------------------------------- D-062 (no tx across the judge)
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


def _api_judge(app: Any, llm: ScriptedLLM) -> rj.RiskJudge:
    settings = app.state.settings.model_copy(update={"librarian_enabled": True, "llm_mode": "live"})
    judge = rj.RiskJudge(settings, chain=stub_chain(fallback=False), transport=llm.transport)
    app.state.risk_judge = judge
    return judge


async def test_d062_revoke_during_the_judge_discards_the_result(db_dsn, world) -> None:  # noqa: ANN001
    task = load_cases()[0]["task"]
    async with running_app(db_dsn) as client:
        app = client.app  # type: ignore[attr-defined]
        did, token = await trusted_device(client, "rk-revoked", grants=[{"project": MAIN, "role": "write"}])
        revoke_s: list[float] = []

        async def revoke_while_judging(_body: dict[str, Any]) -> None:
            t0 = time.perf_counter()
            r = await client.post(f"/admin/devices/{did}/revoke", headers=bearer(ADMIN_TOKEN), json={})
            revoke_s.append(time.perf_counter() - t0)
            assert r.status_code == 200, r.text

        llm = ScriptedLLM(
            default=lambda body: {
                "verdict": "warn",
                "matches": [{"id": _id_of(body, "bash -s"), "why": "x"}],
            },
            on_request=revoke_while_judging,
        )
        judge = _api_judge(app, llm)
        try:
            args = {"project": MAIN, "task": task, "token_budget": 2000}
            res = await call_tool_raw(client, token, "memory.risk_check", args)
        finally:
            await judge.aclose()
        out = json.loads(res["content"][0]["text"])
        # the revocation committed while the judge ran (no lock held), and the result was discarded
        assert llm.calls == 1 and revoke_s and revoke_s[0] < 1.5, revoke_s
        assert res["isError"] is True and out["code"] == "E_AUTH", out


async def test_d062_pool_not_held_during_the_judge(db_dsn, world) -> None:  # noqa: ANN001
    """pool_max_size=2 and a judge that takes 1.5 s: three concurrent risk checks and a stream of
    queries all finish promptly. Before D-062 each judged call held a pooled connection (and the
    device lock) for the whole judge: two calls exhausted the pool and everything else waited."""
    async with small_pool_app(db_dsn, 2) as client:
        app = client.app  # type: ignore[attr-defined]
        _did, token = await trusted_device(client, "rk-pool", grants=[{"project": MAIN, "role": "write"}])
        llm = ScriptedLLM(default=("stall", 1.5, {"verdict": "none", "matches": []}))
        judge = _api_judge(app, llm)
        q_times: list[float] = []

        async def risk(task: str) -> tuple[float, dict[str, Any]]:
            t0 = time.perf_counter()
            res = await call_tool_raw(
                client, token, "memory.risk_check", {"project": MAIN, "task": task, "token_budget": 1500}
            )
            return time.perf_counter() - t0, json.loads(res["content"][0]["text"])

        async def queries() -> None:
            await asyncio.sleep(0.3)  # the risk checks are inside their judge by now
            for _ in range(6):
                t0 = time.perf_counter()
                await call_tool_raw(
                    client,
                    token,
                    "memory.query",
                    {"project": MAIN, "query": "ssh stdin", "token_budget": 800},
                )
                q_times.append(time.perf_counter() - t0)

        try:
            tasks = [c["task"] for c in load_cases()[:3]]
            got = await asyncio.gather(*(risk(t) for t in tasks), queries())
        finally:
            await judge.aclose()
        risks = [g for g in got if isinstance(g, tuple)]
        print(
            f"\nD-062 pool=2: risk {[round(t, 2) for t, _ in risks]} s; "
            f"queries during the judges max {max(q_times) * 1000:.0f} ms"
        )
        assert all(o["judged"] is True for _, o in risks), risks
        assert max(t for t, _ in risks) < 3.0, risks
        assert max(q_times) < 1.0, q_times


async def test_d062_visibility_rechecked_after_the_judge(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """A grant removed while the judge runs: the warning on that project's lesson is dropped; the
    home-project grant removed: E_FORBIDDEN_PROJECT (the result is discarded)."""
    shell = world.projects["rk-shell"]
    rid = world.ctx_reader.device_id

    async def set_grant(pid: int, present: bool) -> None:
        value = "NULL" if present else "now()"
        async with await connect() as c:
            await c.execute(
                f"UPDATE device_project_grants SET revoked_at = {value}"
                " WHERE device_id = %s AND project_id = %s",
                (rid, pid),
            )
            await c.commit()

    async def drop_shell(_body: dict[str, Any]) -> None:
        await set_grant(shell, False)

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        return {"verdict": "warn", "matches": [{"id": _id_of(body, "while read"), "why": "ssh reads stdin"}]}

    task = next(c["task"] for c in load_cases() if c["id"] == "P02")  # L02 lives in rk-shell
    llm = ScriptedLLM(default=answer, on_request=drop_shell)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, lessons, _ = await run_case(connect, world, deps, task, judge=judge)
        assert out["judged"] is True and "L02" not in lessons, out
        await set_grant(shell, True)
        llm.on_request = None
        out, lessons, _ = await run_case(connect, world, deps, task, judge=judge)
        assert lessons == ["L02"], out  # control: visible again, kept
        main = world.projects[MAIN]
        llm.on_request = lambda _b: set_grant(main, False)
        with pytest.raises(ToolError) as ei:
            await run_case(connect, world, deps, task, judge=judge)
        assert ei.value.code == "E_FORBIDDEN_PROJECT"
    finally:
        await set_grant(shell, True)
        await set_grant(world.projects[MAIN], True)
        await judge.aclose()


def test_fallback_profile_is_not_qualified_for_the_risk_judge(monkeypatch) -> None:  # noqa: ANN001
    """Orchestrator ruling (Sol 41): deepseek (`openrouter`) failed G-LIVE-C → disabled for risk_judge;
    during a primary outage risk_check answers retrieval-only."""
    assert rj.TASK in rj.disabled_tasks("openrouter")
    assert rj.TASK not in rj.disabled_tasks("openrouter-gpt6-luna")
    monkeypatch.setenv("HLM_PROFILE", "openrouter-gpt6-luna")
    settings = get_settings(profile="openrouter-gpt6-luna", fallback_profile="openrouter")
    assert [p.name for p in rj.judge_chain(settings)] == ["openrouter-gpt6-luna"]


def test_stub_chat_helper_shape() -> None:
    assert chat({"verdict": "none", "matches": []})["choices"][0]["message"]["content"]
    assert stub_profile("x").priced


# --------------------------------------------------------------------------- latency policy (BACKLOG)
async def test_stalled_primary_is_retrieval_only_inside_the_cap_without_the_fallback(
    connect, world, deps, db_dsn, tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """D-071 + the latency policy: the judge chain has no fallback (the deepseek-like profile is
    disqualified by ``disabled_tasks``), so a stalled primary ends in retrieval-only within the 4 s
    cap: one bounded attempt, no backoff, and never a call to the disqualified profile."""
    (tmp_path / "judge-primary.toml").write_text(
        'HLM_LLM_BASE_URL = "http://judge-primary.invalid/v1"\nHLM_LLM_MODEL = "stub/judge-primary"\n'
    )
    (tmp_path / "judge-deepseek.toml").write_text(
        'HLM_LLM_BASE_URL = "http://judge-deepseek.invalid/v1"\nHLM_LLM_MODEL = "stub/judge-deepseek"\n'
        'disabled_tasks = ["risk_judge"]\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    settings = judge_settings(
        db_dsn,
        profile="judge-primary",
        fallback_profile="judge-deepseek",
        llm_base_url="http://judge-primary.invalid/v1",
        llm_model="stub/judge-primary",
    )
    seen: list[tuple[str, float]] = []

    def route(host: str, _body: dict[str, Any]) -> Any:
        return "stall" if host == "judge-primary.invalid" else {"verdict": "none", "matches": []}

    judge = rj.RiskJudge(settings, transport=timeout_honouring(route, seen))
    assert [p.name for p in judge.chain] == ["judge-primary"]
    try:
        out, _lessons, ms = await run_case(connect, world, deps, load_cases()[0]["task"], judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is False and out["judge"] == rj.RETRIEVAL_ONLY and out["reason"] == rj.TIMEOUT, out
    assert [h for h, _ in seen] == ["judge-primary.invalid"]  # one attempt; the fallback never called
    assert ms < rj.JUDGE_TIMEOUT_S * 1000 + P95_DET_MS, ms


# --------------------------------------------------------------------------- D-094 task fallback
def _judge_profiles(tmp_path: Path, monkeypatch: Any) -> dict[str, Any]:
    """judge-primary, judge-deepseek (disqualified for the judge) and judge-qwen (qualified)."""
    for key in list(os.environ):
        if key.upper().startswith("HLM_FALLBACK_PROFILE"):
            monkeypatch.delenv(key)
    for name, extra in (
        ("judge-primary", ""),
        ("judge-deepseek", 'disabled_tasks = ["risk_judge"]\n'),
        ("judge-qwen", ""),
    ):
        (tmp_path / f"{name}.toml").write_text(
            f'HLM_LLM_BASE_URL = "http://{name}.invalid/v1"\nHLM_LLM_MODEL = "stub/{name}"\n{extra}'
        )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    return {
        "profile": "judge-primary",
        "llm_base_url": "http://judge-primary.invalid/v1",
        "llm_model": "stub/judge-primary",
    }


def _stalled_primary(host: str, _body: dict[str, Any]) -> Any:
    return "stall" if host == "judge-primary.invalid" else {"verdict": "none", "matches": []}


async def test_qualified_task_fallback_judges_inside_the_cap_when_the_primary_stalls(
    connect, world, deps, db_dsn, tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """D-094 (amends D-071): ``HLM_FALLBACK_PROFILE__RISK_JUDGE`` names a QUALIFIED profile, so a
    stalled primary (one bounded attempt, at most 55 % of the 4 s cap) hands the rest of the cap to
    it and the result is judged, labelled ``ok_fallback``; the default fallback (disqualified by
    ``disabled_tasks``) is never called."""
    primary = _judge_profiles(tmp_path, monkeypatch)
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", "judge-qwen")
    settings = judge_settings(db_dsn, fallback_profile="judge-deepseek", **primary)
    seen: list[tuple[str, float]] = []
    judge = rj.RiskJudge(settings, transport=timeout_honouring(_stalled_primary, seen))
    assert [p.name for p in judge.chain] == ["judge-primary", "judge-qwen"]
    try:
        out, _lessons, ms = await run_case(connect, world, deps, load_cases()[0]["task"], judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is True and out["judge"] == rj.OK_FALLBACK, out
    (p_host, p_read), (f_host, f_read) = seen
    assert (p_host, f_host) == ("judge-primary.invalid", "judge-qwen.invalid")
    assert p_read <= rj.JUDGE_TIMEOUT_S * LATENCY_PRIMARY_SHARE + 0.01
    assert f_read > 1.0  # the task fallback got the rest of the cap, not a sliver
    assert ms < rj.JUDGE_TIMEOUT_S * 1000 + P95_DET_MS, ms


async def test_open_primary_breaker_sends_the_judge_straight_to_its_own_fallback(
    connect, world, deps, db_dsn, tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """Per-profile breakers with a task fallback: after BREAKER_THRESHOLD stalled primary attempts
    the primary's breaker opens; the judge stays available (degraded) and the next call goes to the
    task fallback alone, with the whole cap."""
    primary = _judge_profiles(tmp_path, monkeypatch)
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", "judge-qwen")
    settings = judge_settings(db_dsn, fallback_profile="judge-deepseek", **primary)
    seen: list[tuple[str, float]] = []
    judge = rj.RiskJudge(settings, transport=timeout_honouring(_stalled_primary, seen), timeout_s=2.0)
    task = load_cases()[0]["task"]
    try:
        for _ in range(rj.BREAKER_THRESHOLD):
            out, _l, _ms = await run_case(connect, world, deps, task, judge=judge)
            assert out["judge"] == rj.OK_FALLBACK, out
        assert judge.provider is not None
        assert judge.provider.breaker("judge-primary").state == "open"
        assert judge.provider.breaker("judge-qwen").state == "closed"
        assert "judge-deepseek" not in judge.provider._breakers  # never part of the judge's chain
        assert judge.unavailable() is None and judge.breaker.state == "degraded"
        seen.clear()
        out, _l, _ms = await run_case(connect, world, deps, task, judge=judge)
    finally:
        await judge.aclose()
    assert out["judge"] == rj.OK_FALLBACK, out
    ((host, read),) = seen
    assert host == "judge-qwen.invalid" and read > 2.0 * LATENCY_PRIMARY_SHARE  # the whole cap


async def test_unqualified_task_fallback_keeps_retrieval_only(
    connect, world, deps, db_dsn, tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """D-071 kept: when the configured risk fallback lists risk_judge in disabled_tasks, the judge
    has no fallback (even though the DEFAULT fallback would be qualified), so a stalled primary
    ends in retrieval-only inside the cap and the disqualified profile is never called."""
    primary = _judge_profiles(tmp_path, monkeypatch)
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", "judge-deepseek")
    settings = judge_settings(db_dsn, fallback_profile="judge-qwen", **primary)
    seen: list[tuple[str, float]] = []
    judge = rj.RiskJudge(settings, transport=timeout_honouring(_stalled_primary, seen))
    assert [p.name for p in judge.chain] == ["judge-primary"]
    try:
        out, _lessons, ms = await run_case(connect, world, deps, load_cases()[0]["task"], judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is False and out["judge"] == rj.RETRIEVAL_ONLY and out["reason"] == rj.TIMEOUT, out
    assert [h for h, _ in seen] == ["judge-primary.invalid"]
    assert ms < rj.JUDGE_TIMEOUT_S * 1000 + P95_DET_MS, ms
