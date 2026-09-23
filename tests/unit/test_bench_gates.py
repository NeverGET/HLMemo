"""W2f gates (no database; the shared-DB budget gates are in tests/integration/test_w2f_bench_budget.py).

* **G-B1** replay report byte-identical: strict cassette replay of a recorded run renders exactly the
  committed golden report (v2: ``tests/cassettes/w2f``, public packs, gpt-6-luna; v1: the W2a live
  gate cassettes on the production prompts), and two replays render the same bytes.
* **G-B2 (local mode)** ``--max-usd`` is honoured via reservation: against a stub provider whose every
  call costs exactly its worst case, a cap of N x worst lets exactly N calls reach the network
  (sequential and concurrent), the spend never exceeds the cap, the run reports ``budget_deferred``
  and the CLI exits 2. ``--budget db`` (the default) refuses to run without a reachable budget DB.
* **Redaction from settings**: ``HLM_LLM_REDACT_EMAIL/PHONE`` reach the request and the cassette.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from hlmemo.bench.cli import EXIT_CAP, bench_app
from hlmemo.bench.engine import BenchConfig, BudgetUnavailable, run_bench

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "bench"
WORST = Decimal("0.2")  # 200 max_tokens (placement) x 1000 USD/M output, 0 USD/M input
EMAIL = "oncall.person@example.com"
PHONE = "+49 151 2345 6789"


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


# --------------------------------------------------------------------------- G-B2 (local)
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
    """Answers placement with its first candidate and answer_or_abstain with 's1'; each call costs
    exactly WORST. Keeps every request body it saw."""

    def __init__(self) -> None:
        self.calls = 0
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        self.bodies.append(body)
        user = body["messages"][1]["content"]
        payload = json.loads(user.split("INPUT: ", 1)[1])
        if user.startswith("JOB: placement"):
            answer = {
                "layer": "fact",
                "topic_id": payload["candidates"][0]["topic_id"],
                "importance": 5,
                "stability": "stable",
            }
        else:
            answer = {"answer": "the on-call person", "confidence": 1, "evidence_ids": ["s1"]}
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
async def test_gb2_local_max_usd_is_honoured_by_reservation(stub_profile: str, concurrency: int) -> None:
    stub = Stub()
    cfg = BenchConfig(
        suite="v1",
        profile=stub_profile,
        tasks=["placement"],
        max_usd=float(WORST * 3),
        concurrency=concurrency,
        budget="local",
    )
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 3  # exactly floor(cap / worst) attempts reached the network
    assert res.aborted and res.spent_usd == WORST * 3 <= Decimal(str(cfg.max_usd))
    statuses = [r["status"] for r in res.rows]
    assert statuses.count("ok") == 3 and "budget_deferred" in statuses
    assert set(statuses) <= {"ok", "budget_deferred", "not_run"}
    assert "PARTIAL: stopped at the --max-usd cap" in res.markdown
    assert "NOT shared across runs" in res.markdown and res.meta["budget"].startswith("local")
    assert sum(1 for r in res.ledger if r["outcome"] == "budget_deferred") >= 1


async def test_gb2_no_cap_hit_runs_everything(stub_profile: str) -> None:
    stub = Stub()
    cfg = BenchConfig(
        suite="v1", profile=stub_profile, tasks=["placement"], max_usd=float(WORST * 10), budget="local"
    )
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 10 and not res.aborted and res.spent_usd == WORST * 10


def test_gb2_cli_exits_nonzero_when_the_cap_stops_the_run(stub_profile: str, tmp_path: Path) -> None:
    """A cap below one call's worst case: the first reservation is refused, nothing is sent (the stub
    host does not even resolve), the run is a FAIL with exit code 2."""
    out = tmp_path / "out"
    r = CliRunner().invoke(
        bench_app,
        [
            *("--suite", "v1", "--profile", stub_profile, "--tasks", "placement", "--limit", "2"),
            *("--max-usd", "0.1", "--budget", "local", "--out", str(out), "--quiet"),
        ],
    )
    assert r.exit_code == EXIT_CAP, r.output
    assert "PARTIAL: stopped at the --max-usd cap" in r.stdout
    assert sorted(p.suffix for p in out.iterdir()) == [".json", ".md"]
    doc = json.loads(next(out.glob("*.json")).read_text())
    assert doc["meta"]["aborted"] is True and Decimal(doc["meta"]["spent_usd"]) == 0
    assert {r["status"] for r in doc["rows"]} == {"budget_deferred", "not_run"}


async def test_default_budget_is_the_shared_db_and_refuses_without_it(stub_profile: str) -> None:
    stub = Stub()
    cfg = BenchConfig(
        suite="v1",
        profile=stub_profile,
        tasks=["placement"],
        budget_dsn="postgresql://nobody:x@127.0.0.1:1/none",
    )
    assert cfg.budget == "db"
    with pytest.raises(BudgetUnavailable, match="--budget local"):
        await run_bench(cfg, transport=httpx.MockTransport(stub))
    assert stub.calls == 0


# --------------------------------------------------------------------------- redaction
def _pii_pack(tmp_path: Path) -> Path:
    pack = {
        "task": "answer_or_abstain",
        "family": "T10",
        "version": 1,
        "pack": "test",
        "cases": [
            {
                "id": "TX-01",
                "tier": "easy",
                "question": "Who is on call this week?",
                "snippets": [{"id": "s1", "date": "2026-09-01", "text": f"On call: {EMAIL}, phone {PHONE}."}],
                "gold": {"answerable": True, "answer_keys": ["on-call"], "evidence_ids": ["s1"]},
            }
        ],
    }
    p = tmp_path / "pii.json"
    p.write_text(json.dumps(pack), encoding="utf-8")
    return p


@pytest.mark.parametrize("masked", [True, False])
async def test_redaction_follows_settings_into_requests_and_cassettes(
    stub_profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, masked: bool
) -> None:
    monkeypatch.setenv("HLM_LLM_REDACT_EMAIL", "true" if masked else "false")
    monkeypatch.setenv("HLM_LLM_REDACT_PHONE", "true" if masked else "false")
    stub = Stub()
    cassettes = tmp_path / "cassettes"
    cfg = BenchConfig(
        suite="v2",
        profile=stub_profile,
        builtin=False,
        packs=[str(_pii_pack(tmp_path))],
        mode="record",
        cassette_dir=cassettes,
        budget="local",
        max_usd=10.0,  # one T10 call reserves 2000 tokens x 1000 USD/M
    )
    res = await run_bench(cfg, transport=httpx.MockTransport(stub))
    sent = stub.bodies[0]["messages"][1]["content"]
    recorded = "".join(p.read_text(encoding="utf-8") for p in cassettes.glob("*.jsonl"))
    assert res.meta["redact_email"] is masked and res.meta["redact_phone"] is masked
    for pii, kind in ((EMAIL, "email"), (PHONE, "phone")):
        if masked:
            assert pii not in sent and pii not in recorded
            assert f"⟦REDACTED:{kind}:" in sent and f"⟦REDACTED:{kind}:" in recorded
        else:
            assert pii in sent
