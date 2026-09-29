"""R4 (R-5): ``price_valid_until`` makes a profile unusable for live calls once its prices expired: the
provider skips it without any request or reservation, counts the skip, and the next profile answers;
with no usable profile the call fails closed (``PriceExpired``)."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from hlmemo.librarian.errors import LlmConfigError, PriceExpired
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile, named_profile, primary_profile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider

TODAY = datetime.now(UTC).date()


def _profile(name: str, valid_until: date | None = None) -> LlmProfile:
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
        price_valid_until=valid_until,
    )


def test_price_expired_is_after_the_last_valid_day() -> None:
    p = _profile("p", date(2026, 12, 31))
    assert not p.price_expired(date(2026, 12, 31)) and p.price_expired(date(2027, 1, 1))
    assert not _profile("q").price_expired(date(2099, 1, 1))  # no date: never expires


def test_profile_file_and_primary_settings(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from hlmemo.config import get_settings

    base = (
        'HLM_LLM_BASE_URL = "http://x.invalid/v1"\nHLM_LLM_MODEL = "m"\n'
        + "price_in_per_m = 1\nprice_out_per_m = 2\n"
    )
    (tmp_path / "s.toml").write_text(base + 'price_valid_until = "2026-12-31"\n')
    (tmp_path / "d.toml").write_text(base + "price_valid_until = 2026-12-31\n")  # a TOML date
    (tmp_path / "n.toml").write_text(base)
    (tmp_path / "bad.toml").write_text(base + 'price_valid_until = "31.12.2026"\n')
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    assert named_profile("s").price_valid_until == named_profile("d").price_valid_until == date(2026, 12, 31)
    assert named_profile("n").price_valid_until is None
    with pytest.raises(LlmConfigError, match="price_valid_until"):
        named_profile("bad")
    monkeypatch.setenv("HLM_PROFILE", "s")
    assert primary_profile(get_settings()).price_valid_until == date(2026, 12, 31)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": json.dumps({"summary": "x"})},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


async def test_an_expired_profile_is_skipped_counted_and_the_next_answers() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return _ok(request)

    expired = _profile("old", TODAY - timedelta(days=1))
    ledger = MemoryLedger()
    provider = Provider(
        [expired, _profile("next")], mode="live", ledger=ledger, transport=httpx.MockTransport(handler)
    )
    try:
        res = await provider.complete(load_task("map_summary"), "hello", lineage=str(uuid.uuid4()))
    finally:
        await provider.aclose()
    assert res.profile == "next" and res.fallbacks == [("old", "price_expired")]
    assert hosts == ["next.invalid"]  # no request, no reservation for the expired profile
    assert provider.price_expired_skips == {"old": 1}
    assert [(r.profile, r.outcome) for r in ledger.rows] == [("next", "ok")]
    # valid through today: used
    today = replace(expired, price_valid_until=TODAY)
    provider2 = Provider([today], mode="live", ledger=MemoryLedger(), transport=httpx.MockTransport(_ok))
    try:
        assert (await provider2.complete(load_task("map_summary"), "hello")).profile == "old"
    finally:
        await provider2.aclose()


async def test_no_usable_profile_fails_closed() -> None:
    calls: list[str] = []
    provider = Provider(
        [_profile("a", TODAY - timedelta(days=1)), _profile("b", TODAY - timedelta(days=30))],
        mode="live",
        ledger=MemoryLedger(),
        transport=httpx.MockTransport(lambda r: calls.append(r.url.host) or _ok(r)),
    )
    try:
        with pytest.raises(PriceExpired, match="price_valid_until"):
            await provider.complete(load_task("map_summary"), "hello")
    finally:
        await provider.aclose()
    assert calls == [] and provider.price_expired_skips == {"a": 1, "b": 1}
