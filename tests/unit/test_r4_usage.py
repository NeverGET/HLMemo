"""R4 B1/R-8: billed usage normalization (thinking tokens) for the ledger, the settlement and the
per-question tally; ``usage.cost`` stays the USD authority."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
import pytest

from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider, normalize_usage


def _profile(name: str = "p", reasoning: str = "included") -> LlmProfile:
    return LlmProfile(
        name=name,
        base_url=f"http://{name}.invalid/v1",
        model_id=f"stub/{name}",
        api_key="test-key-not-secret",
        reasoning=None,
        extra={"response_format": {"type": "json_object"}},
        price_in_per_m=Decimal("0.75"),
        price_out_per_m=Decimal("3.75"),
        supports_json_schema=False,
        prompt_overrides={},
        usage_reasoning=reasoning,
    )


# the fixtures: OpenRouter (reasoning inside completion, cost reported), Google OpenAI-compatible
# (completion EXCLUDES thinking), and the unreliable shapes
OPENROUTER = {
    "prompt_tokens": 1000,
    "completion_tokens": 300,
    "total_tokens": 1300,
    "cost": 0.0005,
    "completion_tokens_details": {"reasoning_tokens": 200},
}
GOOGLE_DETAILS = {
    "prompt_tokens": 1000,
    "completion_tokens": 100,
    "total_tokens": 3100,
    "completion_tokens_details": {"reasoning_tokens": 2000},
}
GOOGLE_TOTAL_ONLY = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 3100}
LUNA = {"prompt_tokens": 7554, "completion_tokens": 298, "total_tokens": 7852}


def test_normalize_usage_per_convention() -> None:
    n = normalize_usage(OPENROUTER, reasoning="included", max_tokens=3000)
    assert (n.input_tokens, n.output_tokens, n.reliable, n.basis) == (1000, 300, True, "completion_tokens")
    n = normalize_usage(GOOGLE_DETAILS, reasoning="excluded", max_tokens=16000)
    assert (n.output_tokens, n.reliable, n.basis) == (2100, True, "reasoning_tokens")  # 100 + 2000
    n = normalize_usage(GOOGLE_TOTAL_ONLY, reasoning="excluded", max_tokens=16000)
    assert (n.output_tokens, n.reliable, n.basis) == (2100, True, "total_tokens")  # 3100 - 1000
    n = normalize_usage(LUNA, reasoning="included", max_tokens=3000)
    assert (n.output_tokens, n.reliable) == (298, True)  # the luna regression: unchanged


@pytest.mark.parametrize(
    ("usage", "reasoning", "basis"),
    [
        ({}, "included", "missing"),
        ({"prompt_tokens": 10}, "excluded", "missing"),
        # R-8's example: zero reasoning, yet the total shows 2000 tokens more
        (
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 3100,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            "included",
            "contradictory",
        ),
        (
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 3100,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            "excluded",
            "contradictory",
        ),
        ({**GOOGLE_DETAILS, "total_tokens": 1500}, "excluded", "contradictory"),  # details vs total
        ({"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 900}, "excluded", "contradictory"),
        ({"prompt_tokens": 1000, "completion_tokens": 100}, "excluded", "no_thinking"),  # cannot see it
        ({"prompt_tokens": 1000, "completion_tokens": "100"}, "included", "missing"),
    ],
)
def test_unreliable_usage_books_the_worst_case(usage: dict, reasoning: str, basis: str) -> None:
    n = normalize_usage(usage, reasoning=reasoning, max_tokens=16000)
    assert not n.reliable and n.basis == basis and n.output_tokens == 16000


def _chat(usage: dict | None) -> dict:
    out = {
        "model": "stub",
        "choices": [
            {
                "message": {"role": "assistant", "content": json.dumps({"summary": "x"})},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        out["usage"] = usage
    return out


async def _one(profile: LlmProfile, usage: dict | None) -> tuple[MemoryLedger, MemoryBudget, Decimal]:
    ledger, budget = MemoryLedger(), MemoryBudget(Decimal("1"))
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=_chat(usage)))
    provider = Provider([profile], mode="live", budget=budget, ledger=ledger, transport=transport)
    try:
        await provider.complete(load_task("map_summary"), "hello", lineage=str(uuid.uuid4()))
    finally:
        await provider.aclose()
    (row,) = ledger.rows
    return ledger, budget, row.reserved_usd


async def test_settlement_and_ledger_use_the_normalized_tokens() -> None:
    per = Decimal(1_000_000)
    # OpenRouter: usage.cost is the USD authority; the ledger books completion_tokens
    ledger, budget, _worst = await _one(_profile(), OPENROUTER)
    assert budget.spent == Decimal("0.0005") and ledger.rows[0].output_tokens == 300
    # Google, thinking excluded from completion_tokens: 2100 output tokens booked AND priced
    want = (1000 * Decimal("0.75") + 2100 * Decimal("3.75")) / per
    for usage in (GOOGLE_DETAILS, GOOGLE_TOTAL_ONLY):
        ledger, budget, _worst = await _one(_profile(reasoning="excluded"), usage)
        (row,) = ledger.rows
        assert row.output_tokens == 2100 and row.cost_usd == want and budget.spent == want
    # the same usage read naively (completion only) would have booked 100 tokens: 4.6x less
    assert (1000 * Decimal("0.75") + 100 * Decimal("3.75")) / per < want / 4
    # luna regression: priced exactly as before
    ledger, budget, _worst = await _one(_profile(), LUNA)
    assert ledger.rows[0].output_tokens == 298
    assert budget.spent == (7554 * Decimal("0.75") + 298 * Decimal("3.75")) / per


@pytest.mark.parametrize(
    "usage",
    [None, {"prompt_tokens": 1000, "completion_tokens": 100}, {**GOOGLE_DETAILS, "total_tokens": 1500}],
)
async def test_unreliable_usage_settles_at_the_worst_case(usage: dict | None) -> None:
    ledger, budget, worst = await _one(_profile(reasoning="excluded"), usage)
    (row,) = ledger.rows
    assert worst > 0 and row.cost_usd == worst and budget.spent == worst
    assert row.output_tokens == load_task("map_summary").max_tokens


def test_profile_file_usage_reasoning(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from hlmemo.librarian.errors import LlmConfigError
    from hlmemo.librarian.profiles import named_profile

    base = (
        'HLM_LLM_BASE_URL = "http://x.invalid/v1"\nHLM_LLM_MODEL = "m"\n'
        + "price_in_per_m = 1\nprice_out_per_m = 2\n"
    )
    (tmp_path / "g.toml").write_text(base + 'usage_reasoning = "excluded"\n')
    (tmp_path / "o.toml").write_text(base)
    (tmp_path / "bad.toml").write_text(base + 'usage_reasoning = "maybe"\n')
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    assert (
        named_profile("g").usage_reasoning == "excluded" and named_profile("o").usage_reasoning == "included"
    )
    with pytest.raises(LlmConfigError, match="usage_reasoning"):
        named_profile("bad")


async def test_the_per_question_tally_uses_the_same_numbers() -> None:
    """memory.ask's per-question budget (research._TeeLedger) sums the ledger rows: the normalized
    output tokens and the settled USD."""
    from hlmemo.librarian.tasks.research import _TeeLedger

    tee = _TeeLedger(MemoryLedger())
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=_chat(GOOGLE_TOTAL_ONLY)))
    provider = Provider([_profile(reasoning="excluded")], mode="live", ledger=tee, transport=transport)
    lineage = str(uuid.uuid4())
    try:
        res = await provider.complete(load_task("map_summary"), "hello", lineage=lineage)
    finally:
        await provider.aclose()
    usd, tokens = tee.peek(lineage)
    assert tokens == 1000 + 2100 and usd == res.cost_usd == tee.inner.rows[0].cost_usd
    assert usd == (1000 * Decimal("0.75") + 2100 * Decimal("3.75")) / Decimal(1_000_000)
