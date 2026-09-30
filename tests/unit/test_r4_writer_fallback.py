"""R4 (R-9): the writer's budget policy on a schema failure or a truncation.

With a writer profile (HLM_RESEARCH_WRITER_PROFILE), a writer JOB (prose/expand) that answers badly
falls back to the task profile (the research primary, luna) instead of raising schema_fail: at once
for a truncated answer (finish_reason length), after the one schema retry otherwise, and without that
retry when its worst case would not fit the per-question budget (HLM_RESEARCH_MAX_USD): a retry that
does not fit is never reserved. Every fallback is counted. Without a writer profile nothing changes."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import research_service as rsv
from hlmemo.librarian import privacy
from hlmemo.librarian.errors import SchemaFail
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import research as rs

PROSE_OK = {"status": "answered", "answer": "The target is 1.2 s.", "sources": [], "confidence": "high"}


@pytest.fixture
def gem_profile(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """A Gemini-like writer profile file: Google's usage convention and its prices."""
    (tmp_path / "w-gem.toml").write_text(
        'HLM_LLM_BASE_URL = "http://w-gem.invalid/v1"\nHLM_LLM_MODEL = "stub/w-gem"\n'
        'HLM_LLM_API_KEY = "test-key-not-secret"\nprice_in_per_m = 0.75\nprice_out_per_m = 3.75\n'
        'usage_reasoning = "excluded"\nextra = { response_format = { type = "json_object" } }\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    return tmp_path


def _task_profile() -> LlmProfile:
    return LlmProfile(
        name="t-task",
        base_url="http://t-task.invalid/v1",
        model_id="stub/t-task",
        api_key="test-key-not-secret",
        reasoning=None,
        extra={"response_format": {"type": "json_object"}},
        price_in_per_m=Decimal("0.20"),
        price_out_per_m=Decimal("0.75"),
        supports_json_schema=False,
        prompt_overrides={},
    )


def _reply(content: str, *, finish: str = "stop", usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20},
        },
    )


def _researcher(handler, *, writer: str | None = "w-gem", **kw):  # noqa: ANN001, ANN003, ANN202
    settings = get_settings(
        research_enabled=True,
        librarian_enabled=True,
        llm_mode="live",
        research_answer_mode="prose",
        research_writer_profile=writer,
        **kw,
    )
    provider = Provider(
        [_task_profile()], mode="live", ledger=MemoryLedger(), transport=httpx.MockTransport(handler)
    )
    r = rs.Researcher(settings, provider=provider)

    async def gate(_caps, _ids):  # noqa: ANN001, ANN202
        return privacy.Verdict(device_ok=True)

    r.gate = gate  # type: ignore[method-assign]
    r.gate_carried = gate  # type: ignore[method-assign]
    return r


async def _prose(r: rs.Researcher, max_usd: float | None = None):  # noqa: ANN202
    """One prose call; with ``max_usd``, the per-question affordability check of research_service."""
    lineage = str(uuid.uuid4())
    afford = None
    if max_usd is not None:
        run = SimpleNamespace(
            researcher=r,
            lineage=lineage,
            settings=SimpleNamespace(research_max_usd=max_usd, research_max_tokens=100_000),
        )

        async def afford(profile, worst, tokens):  # noqa: ANN001, ANN202
            return await rsv._Run.attempt_affordable(run, profile, worst, tokens)

    res = await r.complete(
        "prose",
        "JOB: prose\nINPUT: {}",
        capabilities={},
        gate_ids=[],
        deadline=asyncio.get_running_loop().time() + 30,
        lineage=lineage,
        attempt_affordable=afford,
    )
    return res, [(row.profile, row.outcome) for row in r.provider.ledger.inner.rows]


async def test_invalid_json_twice_falls_back_to_the_task_profile(gem_profile) -> None:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        return _reply("{not json") if model == "stub/w-gem" else _reply(json.dumps(PROSE_OK))

    r = _researcher(handler)
    try:
        res, rows = await _prose(r)
    finally:
        await r.aclose()
    assert res.profile == "t-task" and res.output == PROSE_OK
    assert res.fallbacks == [("w-gem", "schema_fail")]
    assert rows == [("w-gem", "schema_fail"), ("w-gem", "schema_fail"), ("t-task", "ok")]


async def test_a_truncated_writer_is_not_retried(gem_profile) -> None:  # noqa: ANN001
    """finish_reason length at 16k (HLM_RESEARCH_PROSE_MAX_TOKENS=16000): the same limit would truncate
    again, so the task profile answers at once; the writer's thinking is booked (Google usage)."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body["model"] == "stub/w-gem":
            usage = {"prompt_tokens": 1000, "completion_tokens": 120, "total_tokens": 17000}
            return _reply('{"status": "answered", "answer": "The tar', finish="length", usage=usage)
        return _reply(json.dumps(PROSE_OK))

    r = _researcher(handler, research_prose_max_tokens=16000)
    try:
        res, rows = await _prose(r)
        writer_row = r.provider.ledger.inner.rows[0]
    finally:
        await r.aclose()
    assert [b["max_tokens"] for b in sent] == [16000, 16000] and [b["model"] for b in sent] == [
        "stub/w-gem",
        "stub/t-task",
    ]
    assert res.profile == "t-task" and res.fallbacks == [("w-gem", "truncated")]
    assert rows == [("w-gem", "schema_fail"), ("t-task", "ok")]
    assert writer_row.output_tokens == 16000  # total - prompt: the thinking is booked


async def test_an_unaffordable_writer_retry_is_never_reserved(gem_profile) -> None:  # noqa: ANN001
    """The first writer attempt fits HLM_RESEARCH_MAX_USD; its retry would not (spent + worst case
    > max): the task profile answers instead, and nothing stops the question."""

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == "stub/w-gem":
            return _reply(
                "{not json", usage={"prompt_tokens": 1000, "completion_tokens": 15000, "cost": 0.06}
            )
        return _reply(json.dumps(PROSE_OK))

    r = _researcher(handler, research_prose_max_tokens=16000, research_max_usd=0.10)
    try:
        writer_worst, _t = r.worst_case("prose", "JOB: prose\nINPUT: {}")
        assert Decimal("0.06") + writer_worst > Decimal("0.10") >= writer_worst  # the retry cannot fit
        res, rows = await _prose(r, max_usd=0.10)
    finally:
        await r.aclose()
    assert res.profile == "t-task" and res.fallbacks == [("w-gem", "retry_unaffordable")]
    assert rows == [("w-gem", "schema_fail"), ("t-task", "ok")]  # no second writer attempt
    # with room for the retry, the writer is retried first (its second answer is bad too)
    r2 = _researcher(handler, research_prose_max_tokens=16000, research_max_usd=1.0)
    try:
        res2, rows2 = await _prose(r2, max_usd=1.0)
    finally:
        await r2.aclose()
    assert rows2 == [("w-gem", "schema_fail"), ("w-gem", "schema_fail"), ("t-task", "ok")]
    assert res2.fallbacks == [("w-gem", "schema_fail")]


async def test_without_a_writer_profile_nothing_changes() -> None:
    r = _researcher(lambda _req: _reply("{not json"), writer=None)
    try:
        with pytest.raises(SchemaFail):
            await _prose(r)
        rows = [(row.profile, row.outcome) for row in r.provider.ledger.inner.rows]
    finally:
        await r.aclose()
    assert rows == [("t-task", "schema_fail"), ("t-task", "schema_fail")]


def test_every_fallback_is_counted_in_the_flags() -> None:
    run = SimpleNamespace(flags={}, last_result=SimpleNamespace(fallbacks=[("w-gem", "truncated")]))
    rsv._Run.count_fallbacks(run)
    run.last_result = SimpleNamespace(fallbacks=[("w-gem", "timeout"), ("t-task", "unavailable")])
    rsv._Run.count_fallbacks(run)
    assert run.flags == {
        "writer_fallbacks": 3,
        "writer_fallback_reasons": ["w-gem:truncated", "w-gem:timeout", "t-task:unavailable"],
    }
    quiet = SimpleNamespace(flags={}, last_result=SimpleNamespace(fallbacks=[]))
    rsv._Run.count_fallbacks(quiet)
    assert quiet.flags == {}  # no key without a fallback (default responses unchanged)


async def test_a_truncated_answer_is_given_up_even_when_it_parses(gem_profile) -> None:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == "stub/w-gem":
            return _reply(json.dumps({**PROSE_OK, "answer": "The target is"}), finish="length")
        return _reply(json.dumps(PROSE_OK))

    r = _researcher(handler)
    try:
        res, rows = await _prose(r)
    finally:
        await r.aclose()
    assert res.output == PROSE_OK and res.fallbacks == [("w-gem", "truncated")]
    assert rows == [("w-gem", "schema_fail"), ("t-task", "ok")]
