"""R4 B2/B3: HLM_RESEARCH_PROSE_MAX_TOKENS, HLM_RESEARCH_HTTP_TIMEOUT_S, HLM_DETACHED_HOLD_MAX_S and the
raised HLM_RESEARCH_TIMEOUT_S bound. At their defaults every request body and timeout is the one
the former constants produced (golden); an override changes only its own value (expand keeps 1500,
R-17)."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from hlmemo.config import get_settings
from hlmemo.librarian import privacy
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import research as rs

JOB_OUT = {
    "plan": {"queries": ["a b c"], "sections": []},
    "refine": {"queries": ["d e f"], "sections": []},
    "prose": {"status": "answered", "answer": "The target is 1.2 s.", "sources": [], "confidence": "high"},
    "expand": {"add": []},
    "attribute": {"cites": []},
}
#: the golden request bodies of the research JOBs at the defaults (minus the messages): the former
#: constants JOB_MAX_TOKENS
GOLDEN = {
    job: {"model": "stub/t-task", "response_format": {"type": "json_object"}, "max_tokens": n}
    for job, n in (("plan", 800), ("refine", 800), ("prose", 3000), ("expand", 1500), ("attribute", 1500))
}


def _profile() -> LlmProfile:
    return LlmProfile(
        name="t-task",
        base_url="http://t-task.invalid/v1",
        model_id="stub/t-task",
        api_key="test-key-not-secret",
        reasoning=None,
        extra={"response_format": {"type": "json_object"}},
        price_in_per_m=Decimal("1.0"),
        price_out_per_m=Decimal("2.0"),
        supports_json_schema=False,
        prompt_overrides={},
    )


def _settings(**kw):  # noqa: ANN003, ANN202
    return get_settings(
        research_enabled=True,
        librarian_enabled=True,
        llm_mode="live",
        research_answer_mode="prose",
        llm_budget_disabled=True,
        **kw,
    )


async def _bodies(**kw) -> dict[str, dict]:  # noqa: ANN003
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        job = body["messages"][1]["content"].split("\n", 1)[0].removeprefix("JOB: ").strip()
        seen[job] = {k: v for k, v in body.items() if k != "messages"}
        msg = {"role": "assistant", "content": json.dumps(JOB_OUT[job])}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    provider = Provider(
        [_profile()], mode="live", ledger=MemoryLedger(), transport=httpx.MockTransport(handler)
    )
    r = rs.Researcher(_settings(**kw), provider=provider)

    async def gate(_caps, _ids):  # noqa: ANN001, ANN202
        return privacy.Verdict(device_ok=True)

    r.gate = gate  # type: ignore[method-assign]
    r.gate_carried = gate  # type: ignore[method-assign]
    try:
        for job in GOLDEN:
            await r.complete(
                job,
                f"JOB: {job}\nINPUT: {{}}",
                capabilities={},
                gate_ids=[],
                deadline=asyncio.get_running_loop().time() + 30,
                lineage=str(uuid.uuid4()),
            )
    finally:
        await r.aclose()
    return seen


async def test_defaults_produce_the_golden_request_bodies() -> None:
    s = get_settings()
    assert (s.research_prose_max_tokens, s.research_http_timeout_s, s.detached_hold_max_s) == (
        3000,
        20.0,
        60.0,
    )
    assert s.research_timeout_s == 25.0
    assert await _bodies() == GOLDEN


async def test_the_prose_override_changes_only_the_prose_job() -> None:
    bodies = await _bodies(research_prose_max_tokens=16000)
    assert bodies["prose"]["max_tokens"] == 16000
    assert {j: b for j, b in bodies.items() if j != "prose"} == {
        j: b for j, b in GOLDEN.items() if j != "prose"
    }
    assert bodies["expand"]["max_tokens"] == 1500  # R-17: expand keeps its own


def test_the_http_timeout_setting_bounds_the_provider() -> None:
    def built(**kw):  # noqa: ANN003, ANN202
        r = rs.Researcher(_settings(**kw), chain=[_profile()])
        p = r._build_provider(None, None, MemoryLedger())
        return p.timeout_s

    assert built() == min(get_settings().llm_timeout_s, rs.HTTP_TIMEOUT_S) == 20.0  # default: as before
    assert built(research_http_timeout_s=150, llm_timeout_s=180) == 150.0
    assert built(research_http_timeout_s=150) == 60.0  # still bounded by HLM_LLM_TIMEOUT_S (default 60)


@pytest.mark.parametrize(
    ("field", "ok", "bad"),
    [
        ("research_prose_max_tokens", 32000, 32001),
        ("research_http_timeout_s", 180, 181),
        ("detached_hold_max_s", 240, 241),
        ("research_timeout_s", 240, 241),  # was le=120
    ],
)
def test_bounds(field: str, ok: float, bad: float) -> None:
    assert getattr(get_settings(**{field: ok}), field) == ok
    with pytest.raises(ValidationError):
        get_settings(**{field: bad})


def test_env_names(monkeypatch) -> None:  # noqa: ANN001
    for env, value in (
        ("HLM_RESEARCH_PROSE_MAX_TOKENS", "16000"),
        ("HLM_RESEARCH_HTTP_TIMEOUT_S", "150"),
        ("HLM_DETACHED_HOLD_MAX_S", "180"),
        ("HLM_RESEARCH_TIMEOUT_S", "170"),
    ):
        monkeypatch.setenv(env, value)
    s = get_settings()
    assert (
        s.research_prose_max_tokens,
        s.research_http_timeout_s,
        s.detached_hold_max_s,
        s.research_timeout_s,
    ) == (
        16000,
        150.0,
        180.0,
        170.0,
    )
