"""``memory.ask`` live smoke on the D-094 primary (D-136), replayed in CI from cassettes.

Five synthetic questions (EN/TR, one needing the completeness pass, one unanswerable) over the
``_ask_fixtures`` world, after one Memory Map L2 summary cycle, through the production path
(``core.research_service.ask``). Every item's ``valid_from`` is pinned so the prompts — and the
cassette keys — are stable across days.

* CI (default): strict replay of ``tests/cassettes/ask/`` (a miss is a failure: nothing reaches the
  network); asserts the answer contract on every response and the recorded answers' key facts.
* Record: ``HLM_ASK_RECORD=1`` + ``HLM_ASK_ENV_FILE=<.env with OPENROUTER_API_KEY>`` (the key is
  read, never printed) runs live against ``openrouter-gpt6-luna`` behind a hard in-memory spend cap
  (``HLM_ASK_RECORD_MAX_USD``, default 0.30) and rewrites the cassettes. It prints latency and cost
  per question.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hlmemo.config import get_settings
from hlmemo.core import research_service as rsv
from hlmemo.core.budget import Meter
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.cassette import CassetteStore
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import named_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor
from hlmemo.librarian.tasks import map_summary as ms
from hlmemo.librarian.tasks import research as rs
from tests.integration._ask_fixtures import MAIN, SECRETS, seed_world

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "tests" / "cassettes" / "ask"
PRIMARY_PROFILE = "openrouter-gpt6-luna"
PINNED_VALID_FROM = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
METER = Meter()
RECORD = os.environ.get("HLM_ASK_RECORD") == "1"

#: (id, question, facts the answer must carry — any spelling in the list — or None: must abstain)
QUESTIONS: list[tuple[str, str, list[list[str]] | None]] = [
    (
        "q1-latency-history",
        "What is the current retrieval p95 target, what was it before, and who decided the change?",
        [["1.2 s", "1,2 s"], ["1,6 s", "1.6 s"], ["owner"]],
    ),
    (
        "q2-map-budget-tr",
        "Memory Map bütçesi ne kadar ve özetler bu bütçenin en fazla ne kadarını kullanabilir?",
        [["6k", "6 k", "6000", "6.000"], ["40"]],
    ),
    (
        "q3-restore",
        "How do I restore a backup, and what must I stop first?",
        [["restore.sh"], ["api"]],
    ),
    (
        "q4-flag",
        "Which flag turns the research librarian on, and what is its default on the research branch?",
        [["HLM_RESEARCH_ENABLED"], ["true"]],
    ),
    ("q5-unanswerable", "What is the name of the office cat and when was it adopted?", None),
]


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    w = await seed_world(connect, deps.embedder)
    async with await connect() as conn:
        await conn.execute("UPDATE memory_versions SET valid_from = %s", (PINNED_VALID_FROM,))
        await conn.commit()
    return w


def load_env_file() -> None:
    """``HLM_ASK_ENV_FILE`` supplies OPENROUTER_API_KEY (never printed; set variables win)."""
    path = os.environ.get("HLM_ASK_ENV_FILE")
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _settings(db_dsn: str) -> Any:
    return get_settings(
        db_dsn=db_dsn,
        librarian_enabled=True,
        research_enabled=True,
        llm_mode="record" if RECORD else "replay",
        llm_cassette_dir=CASSETTES,
        llm_budget_disabled=True,
        research_timeout_s=90.0 if RECORD else 30.0,
        map_summary_debounce_s=0.0,
        map_summary_per_cycle=50,
    )


def _provider(settings: Any, task: str, budget: MemoryBudget | None) -> Provider:
    redactor = Redactor.from_settings(settings)
    return Provider(
        [named_profile(PRIMARY_PROFILE)],
        mode="record" if RECORD else "replay",
        budget=budget,
        ledger=MemoryLedger(),
        cassettes=CassetteStore(CASSETTES, record_name=task, redactor=redactor),
        redactor=redactor,
        timeout_s=60.0,
    )


def check_contract(out: dict[str, Any]) -> None:
    assert out["budget"]["used"] == METER.count(out) <= out["budget"]["limit"]
    assert len(out["primary"]) <= 3 and len(out["related"]) <= 5
    assert out["meta"]["calls"] <= rs.MAX_CALLS and out["meta"]["attempts"] <= rs.MAX_ATTEMPTS
    if out["abstained"]:
        assert out["answer"] == "" and out["primary"] == [] and out["claims"] == []
        return
    assert out["answer"] and out["claims"] and out["primary"]
    supported = {s["handle"] for c in out["claims"] for s in c["support"]}
    assert {p["handle"] for p in out["primary"]} <= supported
    for c in out["claims"]:
        assert 1 <= len(c["support"]) <= 3 and all(s["quote"] for s in c["support"])
    for text in (out["answer"], *(c["text"] for c in out["claims"])):
        for marker in SECRETS.values():
            assert marker not in text


async def test_ask_live_smoke(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    if not RECORD and not any(CASSETTES.glob("*.jsonl")):
        pytest.fail(f"no cassettes under {CASSETTES}: record them with HLM_ASK_RECORD=1 (live, primary)")
    if RECORD:
        load_env_file()
        assert os.environ.get("OPENROUTER_API_KEY"), "HLM_ASK_ENV_FILE must supply OPENROUTER_API_KEY"
        CASSETTES.mkdir(parents=True, exist_ok=True)
        for f in CASSETTES.glob("*.jsonl"):
            f.unlink()  # a fresh recording
    cap = Decimal(os.environ.get("HLM_ASK_RECORD_MAX_USD", "0.30"))
    budget = MemoryBudget(cap) if RECORD else None
    settings = _settings(db_dsn)

    async def conn_factory():  # noqa: ANN202
        return await connect()

    # 1. one L2 summary cycle (the map then carries the summaries)
    summ = ms.MapSummarizer(settings, provider=_provider(settings, ms.TASK, budget), connect=conn_factory)
    try:
        written = await summ.cycle()
    finally:
        await summ.provider.aclose()
    assert written >= 3, written
    # 2. the questions
    researcher = rs.Researcher(settings, provider=_provider(settings, rs.TASK, budget))
    rows = []
    try:
        for qid, question, facts in QUESTIONS:
            t0 = time.perf_counter()
            async with await connect() as conn:
                await conn.commit()
                out = await rsv.ask(
                    conn,
                    world.ctx_reader,
                    {"question": question, "project": MAIN},
                    deps=deps,
                    researcher=researcher,
                    settings=settings,
                )
            ms_ = (time.perf_counter() - t0) * 1000
            check_contract(out)
            text = " ".join([out["answer"], *(c["text"] for c in out["claims"])])
            if facts is None:
                ok = out["abstained"]
            else:
                ok = not out["abstained"] and all(any(f in text for f in alts) for alts in facts)
            rows.append((qid, ok, out["abstained"], out["meta"], round(ms_)))
            print(
                f"\n{qid}: ok={ok} abstained={out['abstained']} conf={out['confidence']} "
                f"steps={out['meta']['steps']} attempts={out['meta']['attempts']} "
                f"flags={out['meta']['flags']} "
                f"cost=${out['meta']['cost_usd']} latency={round(ms_)} ms\n  answer: {out['answer'][:300]}"
            )
    finally:
        await researcher.aclose()
    spent = f", spent ${budget.spent} of ${cap}" if budget is not None else ""
    total = sum(r[3]["cost_usd"] for r in rows)
    lat = sorted(r[4] for r in rows)
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    print(f"\nmemory.ask latency over {len(lat)} questions: p50 {p50} ms, p95 {p95} ms (max {lat[-1]} ms)")
    print(
        f"\nmemory.ask live smoke ({'record' if RECORD else 'replay'}): "
        f"{sum(r[1] for r in rows)}/{len(rows)} ok, sum of meta.cost_usd ${total:.4f}{spent}"
    )
    assert all(r[1] for r in rows), [r[:3] for r in rows]
