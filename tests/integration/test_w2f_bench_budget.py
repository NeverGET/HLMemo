"""W2f G-B2 on Postgres: ``hlm bench --budget-dsn`` reserves every attempt against ``llm_budget`` too.

The run's in-process ``--max-usd`` cap is generous here; the deployment's hour window (from settings) is
the binding one. Exactly floor(hour cap / worst) calls reach the stub, the window is settled at the
actual cost, and no reservation is left open.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.bench.engine import BenchConfig, run_bench

pytestmark = pytest.mark.integration

WORST = Decimal("0.2")  # 200 max_tokens (placement) x 1000 USD/M output


def _stub_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "stub.toml").write_text(
        'HLM_LLM_BASE_URL = "http://stub.invalid/v1"\n'
        'HLM_LLM_MODEL = "stub/model"\nHLM_LLM_API_KEY = "none"\n'
        "extra = { temperature = 0 }\nprice_in_per_m = 0\nprice_out_per_m = 1000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(d))
    return "stub"


async def test_budget_dsn_chains_the_postgres_windows(
    db_dsn: str, connect, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_LLM_BUDGET_HOUR_USD", "0.4")
    calls = []

    def stub(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
        answer = {
            "layer": "fact",
            "topic_id": payload["candidates"][0]["topic_id"],
            "importance": 5,
            "stability": "stable",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": float(WORST)},
            },
        )

    cfg = BenchConfig(
        suite="v1",
        profile=_stub_profile(tmp_path, monkeypatch),
        tasks=["placement"],
        max_usd=10.0,
        budget_dsn=db_dsn,
        concurrency=1,
    )
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert len(calls) == 2 and res.aborted
    async with await connect() as conn:
        cur = await conn.execute("SELECT spent_usd, reserved_usd FROM llm_budget WHERE period_kind = 'hour'")
        assert await cur.fetchall() == [(WORST * 2, Decimal(0))]
        cur = await conn.execute("SELECT count(*) FROM llm_reservations")
        assert (await cur.fetchone())[0] == 0
    assert res.spent_usd == WORST * 2  # the in-process cap saw the same two settled calls
