"""W2f gates.

* **G-B1** replay report byte-identical: strict cassette replay of a recorded run renders exactly the
  committed golden report (v2: ``tests/cassettes/w2f``, public packs, gpt-6-luna; v1: the W2a live
  gate cassettes on the production prompts), and two replays render the same bytes.
* **G-B2** ``--max-usd`` is honoured via reservation: against a stub provider whose every call costs
  exactly its worst case, a cap of N x worst lets exactly N calls reach the network (sequential and
  concurrent), the spend never exceeds the cap, the run reports ``budget_deferred`` and the CLI exits 2.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from hlmemo.bench.cli import EXIT_CAP, bench_app
from hlmemo.bench.engine import BenchConfig, run_bench

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "bench"
WORST = Decimal("0.2")  # 200 max_tokens (placement) x 1000 USD/M output, 0 USD/M input


async def _replay(suite: str, profile: str, cassettes: str, **kw) -> str:  # noqa: ANN003
    cfg = BenchConfig(
        suite=suite,
        profile=profile,
        mode="replay",
        cassette_dir=ROOT / "tests" / "cassettes" / cassettes,
        concurrency=4,
        **kw,
    )
    return (await run_bench(cfg)).markdown


async def test_gb1_v2_replay_report_is_byte_identical() -> None:
    first = await _replay("v2", "openrouter-gpt6-luna", "w2f", limit=2)
    second = await _replay("v2", "openrouter-gpt6-luna", "w2f", limit=2)
    assert first == second
    assert first == (GOLDEN / "replay_v2_public.md").read_text(encoding="utf-8")


async def test_gb1_v1_replay_on_production_prompts_is_byte_identical() -> None:
    first = await _replay("v1", "openrouter", "w2a")
    assert first == await _replay("v1", "openrouter", "w2a")
    assert first == (GOLDEN / "replay_v1_openrouter.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- G-B2
@pytest.fixture
def stub_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "stub.toml").write_text(
        'HLM_LLM_BASE_URL = "http://stub.invalid/v1"\n'
        'HLM_LLM_MODEL = "stub/model"\nHLM_LLM_API_KEY = "none"\n'
        'extra = { response_format = { type = "json_object" }, temperature = 0 }\n'
        "price_in_per_m = 0\nprice_out_per_m = 1000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(d))
    return "stub"


class Stub:
    """Answers every placement request with its first candidate; each call costs exactly WORST."""

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
                "choices": [
                    {"message": {"role": "assistant", "content": json.dumps(answer)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": float(WORST)},
            },
        )


@pytest.mark.parametrize("concurrency", [1, 8])
async def test_gb2_max_usd_is_honoured_by_reservation(stub_profile: str, concurrency: int) -> None:
    stub = Stub()
    cfg = BenchConfig(
        suite="v1",
        profile=stub_profile,
        tasks=["placement"],
        max_usd=float(WORST * 3),
        concurrency=concurrency,
    )
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 3  # exactly floor(cap / worst) attempts reached the network
    assert res.aborted and res.spent_usd == WORST * 3 <= Decimal(str(cfg.max_usd))
    statuses = [r["status"] for r in res.rows]
    assert statuses.count("ok") == 3 and "budget_deferred" in statuses
    assert set(statuses) <= {"ok", "budget_deferred", "not_run"}
    assert "PARTIAL: stopped at the --max-usd cap" in res.markdown
    assert sum(1 for r in res.ledger if r["outcome"] == "budget_deferred") >= 1


async def test_gb2_no_cap_hit_runs_everything(stub_profile: str) -> None:
    stub = Stub()
    cfg = BenchConfig(suite="v1", profile=stub_profile, tasks=["placement"], max_usd=float(WORST * 10))
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 10 and not res.aborted and res.spent_usd == WORST * 10


def test_gb2_cli_exits_nonzero_when_the_cap_stops_the_run(stub_profile: str, tmp_path: Path) -> None:
    """A cap below one call's worst case: the first reservation is refused, nothing is sent (the stub
    host does not even resolve), the run is a FAIL with exit code 2."""
    r = CliRunner().invoke(
        bench_app,
        [
            "--suite",
            "v1",
            "--profile",
            stub_profile,
            "--tasks",
            "placement",
            "--limit",
            "2",
            "--max-usd",
            "0.1",
            "--out",
            str(tmp_path / "out"),
            "--quiet",
        ],
    )
    assert r.exit_code == EXIT_CAP, r.output
    assert "PARTIAL: stopped at the --max-usd cap" in r.stdout
    written = sorted(p.suffix for p in (tmp_path / "out").iterdir())
    assert written == [".json", ".md"]
    doc = json.loads(next((tmp_path / "out").glob("*.json")).read_text())
    assert doc["meta"]["aborted"] is True and Decimal(doc["meta"]["spent_usd"]) == 0
    assert {r["status"] for r in doc["rows"]} == {"budget_deferred", "not_run"}
