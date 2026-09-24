"""Workstream F (D-094): the per-task fallback through the production wiring, against the database.

The worker's provider is built exactly as in production (``Provider.from_settings``: DB spend guard,
DB ledger, per-profile breakers) from settings carrying ``HLM_FALLBACK_PROFILE__<TASK>`` overrides;
stub profiles only (no vendor, no network: in-process transports).

* D-084 latency policy: a stalled primary gets ≤ 55 % of the deadline, the TASK's fallback the rest.
* Background policy (librarian jobs) is unchanged and falls back to the DEFAULT fallback.
* Breakers per profile (the task fallback has its own key); the ledger rows and the spend guard carry
  the price of the fallback profile actually used; the privacy precheck runs before every attempt;
  the llm/1 audit and the recorded cassette name the fallback that answered.
* Ops status shows the effective primary and fallback per task.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hlmemo.config import get_settings
from hlmemo.librarian import profiles as pr
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import LATENCY_PRIMARY_SHARE, ChainBreakers, Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.ops import service as ops
from hlmemo.ops.cli import chain_lines
from tests.integration._librarian_fixtures import (
    CONTRADICTS_B,
    FakeClock,
    ScriptedLLM,
    conn_ctx,
    timeout_honouring,
)

pytestmark = pytest.mark.integration

PRIMARY, DEFAULT, RISK = "wf-primary", "wf-default", "wf-risk"
P_HOST, D_HOST, R_HOST = (f"{n}.invalid" for n in (PRIMARY, DEFAULT, RISK))
RISK_USER = 'JOB: risk_check\nINPUT: {"task": "t", "lessons": [{"id": "R1", "project": "p", "text": "x"}]}'
NO_RISK = {"verdict": "none", "matches": []}
USER = 'JOB: contradiction\nINPUT: {"A": {"text": "x"}, "B": {"text": "y"}}'


def _profile(d: Path, name: str, pin: str, pout: str) -> None:
    (d / f"{name}.toml").write_text(
        f'HLM_LLM_BASE_URL = "http://{name}.invalid/v1"\nHLM_LLM_MODEL = "stub/{name}"\n'
        'HLM_LLM_API_KEY = "test-key-not-secret"\n'
        'extra = { response_format = { type = "json_object" }, temperature = 0 }\n'
        f"price_in_per_m = {pin}\nprice_out_per_m = {pout}\n"
    )


@pytest.fixture
def wf_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_dsn: str):  # noqa: ANN201
    import os

    for key in list(os.environ):
        if key.upper().startswith(("HLM_FALLBACK_PROFILE", "HLM_PROFILE")):
            monkeypatch.delenv(key)
    _profile(tmp_path, PRIMARY, "0.20", "0.75")
    _profile(tmp_path, DEFAULT, "0.45", "1.50")
    _profile(tmp_path, RISK, "0.45", "4.40")
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    # the per-task override comes from the ENVIRONMENT, as in production (llm.env)
    monkeypatch.setenv("HLM_FALLBACK_PROFILE__RISK_JUDGE", RISK)

    def make(**kw: Any):  # noqa: ANN202
        base: dict[str, Any] = {
            "db_dsn": db_dsn,
            "profile": PRIMARY,
            "fallback_profile": DEFAULT,
            "llm_base_url": f"http://{P_HOST}/v1",
            "llm_model": f"stub/{PRIMARY}",
            "llm_api_key": "test-key-not-secret",
            "price_in_per_m": 0.20,
            "price_out_per_m": 0.75,
            "librarian_enabled": True,
            "librarian_heartbeat_file": None,
            "llm_mode": "live",
            "llm_budget_disabled": False,
            "llm_budget_hour_usd": 1.0,
        }
        return get_settings(**{**base, **kw})

    return make


async def _ledger(connect) -> list[dict[str, Any]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT task, profile, model_id, outcome, reserved_usd, cost_usd FROM llm_calls"
            " ORDER BY created_at, call_id"
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]


async def _spent(connect) -> Decimal:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT spent_usd, reserved_usd FROM llm_budget WHERE period_kind = 'hour'")
        rows = await cur.fetchall()
    assert all(r[1] == 0 for r in rows), rows  # every reservation settled
    return sum((r[0] for r in rows), Decimal(0))


async def test_stalled_primary_hands_the_rest_of_the_deadline_to_the_task_fallback(
    wf_settings, connect, db_dsn
) -> None:  # noqa: ANN001
    settings = wf_settings()
    assert settings.task_fallback_profiles == {"risk_judge": RISK}
    seen: list[tuple[str, float]] = []
    route = {P_HOST: "stall", R_HOST: NO_RISK, D_HOST: NO_RISK}
    provider = Provider.from_settings(
        settings, conn=conn_ctx(db_dsn), transport=timeout_honouring(lambda h, _b: route[h], seen)
    )
    assert [p.name for p in provider.chain] == [PRIMARY, DEFAULT]  # the default chain
    assert [p.name for p in provider.chain_for("risk_judge")] == [PRIMARY, RISK]
    prechecks: list[int] = []

    async def precheck() -> None:
        prechecks.append(1)

    try:
        res = await provider.complete(
            load_task("risk_judge"),
            RISK_USER,
            precheck=precheck,
            deadline=asyncio.get_running_loop().time() + 3.0,
            attempt_policy="latency",
        )
    finally:
        await provider.aclose()
    assert res.profile == RISK and res.model_id == f"stub/{RISK}"
    audit = res.audit(Redactor())  # llm/1: the fallback that answered
    assert (audit["profile"], audit["model_id"], audit["task"]) == (RISK, f"stub/{RISK}", "risk_judge")
    (p_host, p_read), (f_host, f_read) = seen
    assert (p_host, f_host) == (P_HOST, R_HOST)  # never the default fallback
    assert p_read <= 3.0 * LATENCY_PRIMARY_SHARE + 0.01  # D-084: one bounded primary attempt
    assert f_read > 0.9  # the task fallback got the rest of the deadline
    assert len(prechecks) == 2  # the privacy precheck before EVERY attempt
    # breakers per profile: the task fallback has its own key, the default one is untouched
    assert set(provider._breakers) == {PRIMARY, RISK}
    assert provider.breaker(PRIMARY).failures == 1 and provider.breaker(RISK).failures == 0
    assert ChainBreakers(lambda: provider, task="risk_judge").allow()
    rows = await _ledger(connect)
    assert [(r["task"], r["profile"], r["model_id"], r["outcome"]) for r in rows] == [
        ("risk_judge", PRIMARY, f"stub/{PRIMARY}", "timeout"),
        ("risk_judge", RISK, f"stub/{RISK}", "ok"),
    ]
    risk, default = pr.named_profile(RISK), pr.named_profile(DEFAULT)
    ok = rows[1]
    assert ok["cost_usd"] == risk.cost_usd(100, 20)  # usage settled at the RISK profile's prices
    assert ok["cost_usd"] != default.cost_usd(100, 20)
    messages = [
        {"role": "system", "content": load_task("risk_judge").system},
        {"role": "user", "content": RISK_USER},
    ]
    worst = risk.worst_usd(provider.estimate_input_tokens(messages), load_task("risk_judge").max_tokens)
    assert ok["reserved_usd"] == worst.quantize(Decimal("1e-8"))  # reserved at its worst case
    assert await _spent(connect) == sum((r["cost_usd"] for r in rows), Decimal(0))


async def test_background_jobs_keep_the_default_fallback_and_its_breaker(
    wf_settings, connect, db_dsn
) -> None:  # noqa: ANN001
    settings = wf_settings()
    llm = ScriptedLLM(default=lambda body: 503 if body["model"] == f"stub/{PRIMARY}" else CONTRADICTS_B)
    clock = FakeClock()
    provider = Provider.from_settings(settings, conn=conn_ctx(db_dsn), transport=llm.transport, clock=clock)
    try:
        res = await provider.complete(load_task("contradiction"), USER, job_id=41)
    finally:
        await provider.aclose()
    assert res.profile == DEFAULT and clock.sleeps == [1, 2, 4, 8]  # the retry/backoff policy
    assert llm.hosts == [P_HOST] * 5 + [D_HOST]
    assert set(provider._breakers) == {PRIMARY, DEFAULT} and provider.breaker(RISK).failures == 0
    rows = await _ledger(connect)
    assert [(r["profile"], r["outcome"]) for r in rows] == [(PRIMARY, "http_error")] * 5 + [(DEFAULT, "ok")]
    assert rows[-1]["cost_usd"] == pr.named_profile(DEFAULT).cost_usd(100, 20)


async def test_recorded_cassette_names_the_task_fallback(wf_settings, tmp_path, db_dsn) -> None:  # noqa: ANN001
    cassettes = tmp_path / "cassettes"
    settings = wf_settings(llm_mode="record", llm_cassette_dir=cassettes)
    llm = ScriptedLLM(default=lambda body: 503 if body["model"] == f"stub/{PRIMARY}" else NO_RISK)
    provider = Provider.from_settings(settings, conn=conn_ctx(db_dsn), transport=llm.transport)
    try:
        res = await provider.complete(
            load_task("risk_judge"),
            RISK_USER,
            deadline=asyncio.get_running_loop().time() + 3.0,
            attempt_policy="latency",
        )
    finally:
        await provider.aclose()
    assert res.profile == RISK
    records = [json.loads(line) for f in cassettes.glob("*.jsonl") for line in f.read_text().splitlines()]
    assert [(r["task"], r["model_id"]) for r in records] == [("risk_judge", f"stub/{RISK}")]


async def test_ops_status_shows_the_effective_chain_per_task(wf_settings, connect) -> None:  # noqa: ANN001
    settings = wf_settings()
    async with await connect() as conn:
        lib = await ops.librarian_status(conn, settings)
        await conn.commit()
    chains = lib["chains"]
    assert chains["default_fallback"] == DEFAULT
    assert chains["tasks"]["risk_judge"] == {
        "primary": PRIMARY,
        "primary_model": f"stub/{PRIMARY}",
        "fallback": RISK,
        "fallback_model": f"stub/{RISK}",
        "source": "task",
    }
    assert (
        chains["tasks"]["relate"]["fallback"] == DEFAULT and chains["tasks"]["relate"]["source"] == "default"
    )
    lines = chain_lines(chains)
    assert f"chain       risk_judge     primary={PRIMARY} fallback={RISK} (task override)" in lines
    assert f"chain       relate         primary={PRIMARY} fallback={DEFAULT}" in lines
