"""A writer refused with HTTP 503 (provider overloaded) must not burn the question's budget.

Production failure (2026-10-01, twice): the Gemini writer answered ``503 UNAVAILABLE`` ("This model is
currently experiencing high demand") in ~0.5 s. The latency policy gave the profile up
(``writer_fallback_reasons: ["<writer>:unavailable"]``) and luna wrote the answer, as designed. But
the 503 was settled at the writer's WORST case (~$0.069: 11.7k in, 16000 out), so the question's
tally said ~$0.072 while ~$0.004 had been spent. When luna's answer was dropped and the refine path
needed a second prose call, that call's worst case no longer fitted ``HLM_RESEARCH_MAX_USD`` (0.12):
the question abstained with ``abstain_reason=budget``.

The fixture is Google's byte-exact 503 body: its sha256 is the ``response_sha256`` of both prod
ledger rows. The fix (a proposed amendment to D-062 (5)): a 503/529 carrying the provider's own
error object and no usage was rejected before generation and is settled at $0, like a 4xx. Every
other 5xx keeps the worst-case settlement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.librarian import privacy
from hlmemo.librarian import provider as prov
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import research as rs

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "provider" / "google_openai_503_unavailable.json"
PROD_RESPONSE_SHA256 = "302485908d0a3f54dbc236f62975283dc60500340c931075a82ed075770f1ffc"
PROSE_OK = {"status": "answered", "answer": "The target is 1.2 s.", "sources": [], "confidence": "high"}
MAX_USD = 0.12  # the R4 manifest's HLM_RESEARCH_MAX_USD
PROSE_MAX_TOKENS = 16000  # the R4 manifest's HLM_RESEARCH_PROSE_MAX_TOKENS
#: a prose prompt of roughly the prod size (~11-12k tokens in)
BIG_USER = "JOB: prose\nINPUT: " + " ".join(f"excerpt{i} line" for i in range(5000))


def body_503() -> bytes:
    return FIXTURE.read_bytes()


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


def _ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": json.dumps(PROSE_OK)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 11730, "completion_tokens": 117, "cost": 0.00135413},
        },
    )


def _researcher(writer_status: int, writer_body: bytes) -> rs.Researcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == "stub/w-gem":
            return httpx.Response(
                writer_status, content=writer_body, headers={"content-type": "application/json"}
            )
        return _ok()

    settings = get_settings(
        research_enabled=True,
        librarian_enabled=True,
        llm_mode="live",
        research_answer_mode="prose",
        research_writer_profile="w-gem",
        research_prose_max_tokens=PROSE_MAX_TOKENS,
        research_max_usd=MAX_USD,
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


async def _prose(r: rs.Researcher, lineage: str):  # noqa: ANN202
    return await r.complete(
        "prose",
        BIG_USER,
        capabilities={},
        gate_ids=[],
        deadline=asyncio.get_running_loop().time() + 30,
        lineage=lineage,
    )


def test_the_fixture_is_the_body_prod_recorded() -> None:
    assert hashlib.sha256(body_503()).hexdigest() == PROD_RESPONSE_SHA256


async def test_writer_503_falls_back_as_unavailable_and_is_not_charged(gem_profile) -> None:  # noqa: ANN001
    r = _researcher(503, body_503())
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.profile == "t-task" and res.output["answer"] == PROSE_OK["answer"]
    assert res.fallbacks == [("w-gem", "unavailable")]  # the prod meta: writer_fallback_reasons
    rows = r.provider.ledger.inner.rows
    assert [(x.profile, x.outcome) for x in rows] == [("w-gem", "http_error"), ("t-task", "ok")]
    gem = rows[0]
    assert gem.reserved_usd > Decimal("0.06")  # the worst case was reserved before the send
    assert gem.cost_usd == 0  # ... but a refusal at admission is never billed
    assert gem.response_sha256 == PROD_RESPONSE_SHA256
    usd, _tokens = r.spent(lineage)
    assert usd == Decimal("0.00135413")  # the question's tally: what was really spent
    await r.aclose()


async def test_after_a_writer_503_a_second_writer_call_still_fits_the_question_budget(gem_profile) -> None:  # noqa: ANN001
    """The prod abstention: after the 503 and the fallback, the refine path's second prose call (its
    worst case priced by the writer, ~$0.069) must still fit HLM_RESEARCH_MAX_USD."""
    r = _researcher(503, body_503())
    lineage = str(uuid.uuid4())
    await _prose(r, lineage)
    usd, _tokens = r.spent(lineage)
    worst_usd, _worst_tokens = r.worst_case("prose", BIG_USER)
    assert worst_usd > Decimal(str(MAX_USD)) / 2  # two worst cases never fit: the old settlement failed
    assert usd + worst_usd <= Decimal(str(MAX_USD))  # research_service._call's per-question check
    await r.aclose()


async def test_a_500_is_still_charged_at_the_worst_case(gem_profile) -> None:  # noqa: ANN001
    """D-062 (5) unchanged for every other 5xx: a 500 may come mid-generation."""
    body = body_503().replace(b"503", b"500").replace(b"UNAVAILABLE", b"INTERNAL")
    r = _researcher(500, body)
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.fallbacks == [("w-gem", "unavailable")]
    gem = r.provider.ledger.inner.rows[0]
    assert gem.outcome == "http_error" and gem.cost_usd == gem.reserved_usd > 0
    await r.aclose()


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, None, True),  # Google's list form (the fixture)
        (503, b'{"error": {"code": 503, "message": "No available provider"}}', True),
        (529, b'{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}', True),
        (500, None, False),  # another 5xx: D-062's worst case
        (502, None, False),
        (504, None, False),
        (503, b"<html><body>503 Service Temporarily Unavailable</body></html>", False),  # a proxy
        (503, b"", False),
        (503, b"[]", False),
        (503, b'{"error": "overloaded"}', False),  # not an error object
        (503, b'{"error": {"code": 503}, "usage": {"prompt_tokens": 10}}', False),  # usage reported
        (503, b'[{"error": {"code": 503}}, {"choices": []}]', False),
        (429, None, False),  # a 4xx is already $0 on its own rule
    ],
)
def test_rejected_before_generation(status: int, body: bytes | None, expected: bool) -> None:
    assert prov.rejected_before_generation(status, body_503() if body is None else body) is expected
