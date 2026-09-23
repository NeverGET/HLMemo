"""W2f G-B2 on the shared Postgres budget (the DEFAULT ``hlm bench`` spend guard, Sol 40).

``hlm bench`` reserves every network attempt against ``llm_budget`` (the deployment's hour / day /
month windows, ``DbBudget``) like the librarian does, chained after the run's own ``--max-usd`` cap.

* the default path (no ``--budget-dsn``) uses ``HLM_DB_DSN``: exactly floor(hour cap / worst) calls reach
  the stub, the window is settled at the actual cost and no reservation is left open;
* two CONCURRENT bench runs against one DB cap together send exactly floor(cap / worst) calls: the cap
  is shared, which an in-process cap per run (``--budget local``) cannot guarantee.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.bench.engine import BenchConfig, run_bench

pytestmark = pytest.mark.integration

WORST = Decimal("0.2")  # 200 max_tokens (placement) x 1000 USD/M output


@pytest.fixture
def stub_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
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


class Stub:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
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


async def _hour_window(connect) -> list[tuple]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute("SELECT spent_usd, reserved_usd FROM llm_budget WHERE period_kind = 'hour'")
        rows = await cur.fetchall()
        cur = await conn.execute("SELECT count(*) FROM llm_reservations")
        assert (await cur.fetchone())[0] == 0  # nothing left reserved
        return rows


async def test_default_budget_reserves_against_the_shared_db(
    db_dsn: str, connect, stub_profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_DB_DSN", db_dsn)
    monkeypatch.setenv("HLM_LLM_BUDGET_HOUR_USD", "0.4")
    stub = Stub()
    cfg = BenchConfig(suite="v1", profile=stub_profile, tasks=["placement"], max_usd=10.0, concurrency=1)
    assert cfg.budget == "db" and cfg.budget_dsn is None
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 2 and res.aborted
    assert res.meta["budget"].startswith("db (shared llm_budget of 'hlm_test")
    assert await _hour_window(connect) == [(WORST * 2, Decimal(0))]
    assert res.spent_usd == WORST * 2


async def test_concurrent_runs_share_one_db_cap(
    db_dsn: str, connect, stub_profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_LLM_BUDGET_HOUR_USD", "0.6")  # 3 x worst for BOTH runs together
    stub = Stub()

    def cfg() -> BenchConfig:
        return BenchConfig(
            suite="v1",
            profile=stub_profile,
            tasks=["placement"],
            max_usd=10.0,  # each run's own cap is far above the shared one
            concurrency=4,
            budget_dsn=db_dsn,
        )

    a, b = await asyncio.gather(
        run_bench(cfg(), transport=httpx.MockTransport(stub)),
        run_bench(cfg(), transport=httpx.MockTransport(stub)),
    )
    assert stub.calls == 3  # the shared hour window admitted exactly 3 calls across both runs
    assert a.aborted and b.aborted
    assert a.spent_usd + b.spent_usd == WORST * 3
    assert await _hour_window(connect) == [(WORST * 3, Decimal(0))]
