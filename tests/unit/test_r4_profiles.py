"""R4: the committed Gemini 3.8 Flash writer profiles (high, and the medium sibling)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hlmemo.librarian.profiles import for_task, named_profile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider

NAMES = ("google-gemini38-flash-high", "google-gemini38-flash-medium")


@pytest.mark.parametrize("name", NAMES)
def test_gemini_writer_profile(name: str, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-secret")
    p = named_profile(name)
    effort = name.rsplit("-", 1)[1]
    assert p.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert p.model_id == "gemini-3.8-flash" and p.api_key == "test-key-not-secret"
    assert p.extra == {"reasoning_effort": effort, "response_format": {"type": "json_object"}}
    assert (p.price_in_per_m, p.price_out_per_m) == (Decimal("0.75"), Decimal("3.75"))
    assert p.price_valid_until == date(2026, 12, 31) and not p.price_expired(date(2026, 12, 31))
    assert p.price_expired(date(2027, 1, 1))  # the 2027 price change: unusable until updated
    assert p.usage_reasoning == "excluded" and p.json_mode and p.reasoning is None
    assert p.disabled_tasks == frozenset({"risk_judge"})
    assert [x.name for x in for_task([p], "risk_judge")] == [name]  # a head stays; never a fallback
    # the request: the task's max_tokens (thinking included) is set AFTER the profile's options
    spec = load_task("research", 3)
    body = Provider([p], mode="off", ledger=None).build_body(p, spec, [{"role": "user", "content": "x"}])  # type: ignore[arg-type]
    assert body["reasoning_effort"] == effort and body["max_tokens"] == spec.max_tokens
    assert "reasoning" not in body


def test_no_key_in_the_file() -> None:
    from hlmemo.config import load_profile

    for name in NAMES:
        assert load_profile(name)["HLM_LLM_API_KEY"] == "env:GEMINI_API_KEY"


def test_unset_key_is_no_key(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert named_profile(NAMES[0]).api_key is None  # probe-writer reports missing_key
