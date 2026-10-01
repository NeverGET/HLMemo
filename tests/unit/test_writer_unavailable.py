"""A writer refused with HTTP 503 (provider overloaded) must not burn the question's budget, but only
where the provider DOCUMENTS that such a failure is never billed (opt-in per profile).

Production failure (2026-10-01, twice): the Gemini writer answered ``503 UNAVAILABLE`` ("This model is
currently experiencing high demand") in ~0.5 s. The latency policy gave the profile up
(``writer_fallback_reasons: ["<writer>:unavailable"]``) and luna wrote the answer, as designed. But
the 503 was settled at the writer's WORST case (~$0.069: 11.7k in, 16000 out), so the question's
tally said ~$0.072 while ~$0.004 had been spent. When luna's answer was dropped and the refine path
needed a second prose call, that call's worst case no longer fitted ``HLM_RESEARCH_MAX_USD`` (0.12):
the question abstained with ``abstain_reason=budget``.

The fixture is Google's byte-exact 503 body: its sha256 is the ``response_sha256`` of both prod
ledger rows. The fix (a proposed amendment to D-062 (5)): a profile MAY declare, citing its
provider's billing document, the 5xx errors that are never billed (``unbilled_errors``; the Google
profiles declare this 503, Google's billing doc says failed 400/500 requests are not charged). Such
a response settles at $0 only when its body is exactly the declared envelope and carries no usage or
output evidence anywhere. A profile without the opt-in, any other 5xx and any other body keep the
worst case (review: docs/consults/93-astra-writer-503.md)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tomllib
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.config import get_settings
from hlmemo.core import research_service as rsv
from hlmemo.librarian import privacy
from hlmemo.librarian import provider as prov
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.errors import LlmConfigError
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile, UnbilledError, named_profile, primary_profile
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import research as rs

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "provider" / "google_openai_503_unavailable.json"
PROD_RESPONSE_SHA256 = "302485908d0a3f54dbc236f62975283dc60500340c931075a82ed075770f1ffc"
PROD_503 = FIXTURE.read_bytes()
GOOGLE_PROFILES = ("google-gemini38-flash-high", "google-gemini38-flash-medium")
#: what the Google profiles declare: the prod body's exact envelope, nothing wider
GOOGLE_POLICY = [{"http_status": 503, "wrappers": ["list"], "error": {"code": 503, "status": "UNAVAILABLE"}}]
PROSE_OK = {"status": "answered", "answer": "The target is 1.2 s.", "sources": [], "confidence": "high"}
OK_COST = Decimal("0.00135413")
MAX_USD = 0.12  # the R4 manifest's HLM_RESEARCH_MAX_USD
PROSE_MAX_TOKENS = 16000  # the R4 manifest's HLM_RESEARCH_PROSE_MAX_TOKENS
#: a prose prompt of roughly the prod size (~11-12k tokens in)
BIG_USER = "JOB: prose\nINPUT: " + " ".join(f"excerpt{i} line" for i in range(5000))

#: the reviewer's counter-examples (consult 93 #2): the first matcher returned True for all three
COUNTER_EXAMPLES = [
    b'{"error":{}}',
    b'{"error":{"code":500,"usage":{"completion_tokens":100}}}',
    b'{"error":{"code":503},"usageMetadata":{"totalTokenCount":100},"candidates":[{}]}',
]
_ERR = '{"code": 503, "message": "High demand.", "status": "UNAVAILABLE"}'
#: Google's exact envelope with usage/output evidence beside it: every one keeps the worst case
WITH_EVIDENCE = [
    f'[{{"error": {_ERR}, "choices": []}}]'.encode(),  # choices in the same object
    f'[{{"error": {_ERR}, "usage": null}}]'.encode(),  # usage: null (missing usage is not zero usage)
    f'[{{"error": {_ERR}}}, {{"choices": [{{"message": {{"content": "x"}}}}]}}]'.encode(),
    b'[{"error": {"code": 503, "message": "m", "status": "UNAVAILABLE",'
    b' "usage": {"completion_tokens": 100}}}]',  # usage inside the error object
    b'[{"error": {"code": 503, "message": "m", "status": "UNAVAILABLE",'
    b' "details": [{"usageMetadata": {"totalTokenCount": 100}}]}}]',  # nested native usage
    b'[{"error": {"code": 503, "message": "m", "status": "UNAVAILABLE", "candidates": [{}]}}]',
]


def _profile_file(name: str) -> str:
    """A Gemini-like writer profile file: Google's usage convention and its prices."""
    return (
        f'HLM_LLM_BASE_URL = "http://{name}.invalid/v1"\nHLM_LLM_MODEL = "stub/{name}"\n'
        'HLM_LLM_API_KEY = "test-key-not-secret"\nprice_in_per_m = 0.75\nprice_out_per_m = 3.75\n'
        'usage_reasoning = "excluded"\nextra = { response_format = { type = "json_object" } }\n'
    )


def _shipped(name: str) -> dict:
    return tomllib.loads((ROOT / "profiles" / f"{name}.toml").read_text())


@pytest.fixture
def writer_profiles(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """``w-gem`` carries the shipped Google profile's own ``unbilled_errors`` (copied from the file,
    as JSON: the env form); ``w-other`` is the same endpoint WITHOUT the opt-in (an unknown/other
    provider)."""
    policy = json.dumps(_shipped(GOOGLE_PROFILES[1])["unbilled_errors"])
    (tmp_path / "w-gem.toml").write_text(_profile_file("w-gem") + f"unbilled_errors = '{policy}'\n")
    (tmp_path / "w-other.toml").write_text(_profile_file("w-other"))
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def google(writer_profiles) -> tuple[UnbilledError, ...]:  # noqa: ANN001
    return named_profile("w-gem").unbilled_errors


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
            "usage": {"prompt_tokens": 11730, "completion_tokens": 117, "cost": float(OK_COST)},
        },
    )


def _researcher(
    writer: str, writer_status: int, writer_body: bytes, *, failures: int | None = None
) -> tuple[rs.Researcher, MemoryBudget]:
    """A prose Researcher whose writer ``writer`` answers ``writer_status``/``writer_body`` (its first
    ``failures`` requests only, when given; then a normal answer); the task profile always answers.
    The reservations go through a ``MemoryBudget`` (the spend guard's contract in memory)."""
    sent = {"writer": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == f"stub/{writer}":
            sent["writer"] += 1
            if failures is None or sent["writer"] <= failures:
                return httpx.Response(
                    writer_status, content=writer_body, headers={"content-type": "application/json"}
                )
        return _ok()

    settings = get_settings(
        research_enabled=True,
        librarian_enabled=True,
        llm_mode="live",
        research_answer_mode="prose",
        research_writer_profile=writer,
        research_prose_max_tokens=PROSE_MAX_TOKENS,
        research_max_usd=MAX_USD,
    )
    budget = MemoryBudget(Decimal(1))
    provider = Provider(
        [_task_profile()],
        mode="live",
        budget=budget,
        ledger=MemoryLedger(),
        transport=httpx.MockTransport(handler),
    )
    r = rs.Researcher(settings, provider=provider)

    async def gate(_caps, _ids):  # noqa: ANN001, ANN202
        return privacy.Verdict(device_ok=True)

    r.gate = gate  # type: ignore[method-assign]
    r.gate_carried = gate  # type: ignore[method-assign]
    return r, budget


async def _prose(r: rs.Researcher, lineage: str):  # noqa: ANN202
    return await r.complete(
        "prose",
        BIG_USER,
        capabilities={},
        gate_ids=[],
        deadline=asyncio.get_running_loop().time() + 30,
        lineage=lineage,
    )


def _run(r: rs.Researcher) -> rsv._Run:
    """A ``_Run`` over the real Researcher (no DB): ``call`` applies the per-question budget check."""
    return rsv._Run(
        conn=None,
        ctx=None,
        researcher=r,
        deps=None,
        settings=r.settings,
        question="What is the retrieval p95 target?",
        slug="p",
        project_id=1,
        end=asyncio.get_running_loop().time() + 60.0,
        reconnect=None,
    )


# --------------------------------------------------------------------------- the profiles
def test_the_fixture_is_the_body_prod_recorded() -> None:
    assert hashlib.sha256(PROD_503).hexdigest() == PROD_RESPONSE_SHA256


def test_only_the_google_profiles_opt_in_and_only_to_the_prod_503() -> None:
    for name in GOOGLE_PROFILES:
        assert _shipped(name)["unbilled_errors"] == GOOGLE_POLICY
    others = [p.stem for p in (ROOT / "profiles").glob("*.toml") if p.stem not in GOOGLE_PROFILES]
    assert others and all("unbilled_errors" not in _shipped(n) for n in others)  # default: off


def test_the_policy_reaches_a_named_and_a_primary_profile(writer_profiles, monkeypatch) -> None:  # noqa: ANN001
    want = (UnbilledError(503, frozenset({"list"}), {"code": 503, "status": "UNAVAILABLE"}),)
    assert named_profile("w-gem").unbilled_errors == want
    assert named_profile("w-other").unbilled_errors == ()
    monkeypatch.setenv("HLM_PROFILE", "w-gem")
    assert primary_profile(get_settings()).unbilled_errors == want  # the Settings path carries it
    monkeypatch.setenv("HLM_PROFILE", "w-other")
    assert primary_profile(get_settings()).unbilled_errors == ()


@pytest.mark.parametrize(
    "policy",
    [
        '"503"',  # not a list
        '[{ http_status = 429, wrappers = ["list"], error = { code = 429 } }]',  # a 4xx: already $0
        '[{ http_status = 200, wrappers = ["list"], error = { code = 200 } }]',
        '[{ http_status = "503", wrappers = ["list"], error = { code = 503 } }]',
        '[{ http_status = true, wrappers = ["list"], error = { code = 503 } }]',
        "[{ http_status = 503, wrappers = [], error = { code = 503 } }]",
        '[{ http_status = 503, wrappers = ["array"], error = { code = 503 } }]',
        '[{ http_status = 503, wrappers = ["list"], error = {} }]',
        '[{ http_status = 503, wrappers = ["list"], error = { code = 500 } }]',  # code != status
        '[{ http_status = 503, wrappers = ["list"], error = { code = 503, message = "x" } }]',
        '[{ http_status = 503, wrappers = ["list"], error = { code = 503, details = [] } }]',
        '[{ http_status = 503, wrappers = ["list"], error = { code = 503 }, usage = "none" }]',
        "[{ http_status = 503, error = { code = 503 } }]",
        '[{ http_status = 503, wrappers = ["list"], error = { code = 503 } },'
        ' { http_status = 503, wrappers = ["object"], error = { code = 503 } }]',
    ],
)
def test_a_malformed_policy_is_a_configuration_error(tmp_path, monkeypatch, policy: str) -> None:  # noqa: ANN001
    (tmp_path / "bad.toml").write_text(_profile_file("bad") + f"unbilled_errors = {policy}\n")
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    with pytest.raises(LlmConfigError, match="unbilled_errors"):
        named_profile("bad")


# --------------------------------------------------------------------------- the matcher
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, PROD_503, True),  # Google's list form, byte-exact (the fixture)
        (503, b'[{"error":{"status":"UNAVAILABLE","code":503,"message":"Overloaded."}}]', True),
        *[(503, b, False) for b in COUNTER_EXAMPLES],
        *[(503, b"[" + b + b"]", False) for b in COUNTER_EXAMPLES],
        *[(503, b, False) for b in WITH_EVIDENCE],
        (503, f'{{"error": {_ERR}}}'.encode(), False),  # the object form: not declared by Google
        (503, f'[{{"error": {_ERR}}}, {{"error": {_ERR}}}]'.encode(), False),  # two elements
        (503, f'[[{{"error": {_ERR}}}]]'.encode(), False),
        (503, f'[{{"error": {_ERR}, "id": "x"}}]'.encode(), False),  # an undeclared top-level key
        (503, b'[{"error": {"code": 503, "message": "m", "status": "UNAVAILABLE", "x": null}}]', False),
        (503, b'[{"error": {"code": 503, "message": "m", "status": "UNAVAILABLE", "tokens": null}}]', False),
        (503, b'[{"error": {"code": 500, "message": "m", "status": "UNAVAILABLE"}}]', False),  # code
        (503, b'[{"error": {"code": "503", "message": "m", "status": "UNAVAILABLE"}}]', False),
        (503, b'[{"error": {"code": 503.0, "message": "m", "status": "UNAVAILABLE"}}]', False),
        (503, b'[{"error": {"code": 503, "message": "m", "status": "INTERNAL"}}]', False),
        (503, b'[{"error": {"code": 503, "message": "m"}}]', False),  # no status string
        (503, b'[{"error": {"code": 503, "status": "UNAVAILABLE"}}]', False),  # no message
        (503, b'[{"error": {"code": 503, "message": " ", "status": "UNAVAILABLE"}}]', False),
        (503, b'[{"error": {"code": 503, "message": 7, "status": "UNAVAILABLE"}}]', False),
        (503, b'[{"error": "overloaded"}]', False),
        (503, b"<html><body>503 Service Temporarily Unavailable</body></html>", False),  # a proxy
        (503, b"", False),
        (503, b"[]", False),
        (503, b"[" * 50_000 + b"]" * 50_000, False),  # too deep to parse: worst case, no crash
        (500, b'[{"error": {"code": 500, "message": "m", "status": "INTERNAL"}}]', False),
        (502, PROD_503, False),
        (504, PROD_503, False),
        (529, b'[{"error": {"code": 529, "message": "m", "status": "UNAVAILABLE"}}]', False),
        (429, PROD_503, False),  # a 4xx is $0 on its own rule, never through this policy
    ],
)
def test_is_unbilled_error_on_the_google_policy(google, status: int, body: bytes, expected: bool) -> None:  # noqa: ANN001
    assert prov.is_unbilled_error(google, status, body) is expected


def test_an_undeclared_profile_never_matches() -> None:
    assert prov.is_unbilled_error((), 503, PROD_503) is False


def test_the_policy_is_the_profiles_not_googles(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """D-017: the shapes that qualify come from the profile. A provider documenting a 529 refusal in
    the object form declares exactly that; Google's list body is then not accepted, nor a 503."""
    (tmp_path / "o.toml").write_text(
        _profile_file("o") + 'unbilled_errors = [{ http_status = 529, wrappers = ["object"],'
        ' error = { type = "overloaded_error" } }]\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    policy = named_profile("o").unbilled_errors
    ok = b'{"error": {"type": "overloaded_error", "message": "Overloaded"}}'
    assert prov.is_unbilled_error(policy, 529, ok) is True
    assert prov.is_unbilled_error(policy, 503, ok) is False
    assert prov.is_unbilled_error(policy, 529, b"[" + ok + b"]") is False  # the list form: not declared
    assert prov.is_unbilled_error(policy, 529, PROD_503.replace(b"503", b"529")) is False
    assert prov.is_unbilled_error(policy, 529, ok.replace(b"overloaded_error", b"api_error")) is False


# --------------------------------------------------------------------------- the settlement
async def test_writer_503_falls_back_as_unavailable_and_is_not_charged(writer_profiles) -> None:  # noqa: ANN001
    r, budget = _researcher("w-gem", 503, PROD_503)
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.profile == "t-task" and res.output["answer"] == PROSE_OK["answer"]
    assert res.fallbacks == [("w-gem", "unavailable")]  # the prod meta: writer_fallback_reasons
    rows = r.provider.ledger.inner.rows
    assert [(x.profile, x.outcome) for x in rows] == [("w-gem", "http_error"), ("t-task", "ok")]
    gem = rows[0]
    assert gem.reserved_usd > Decimal("0.06")  # the worst case was reserved before the send
    assert gem.cost_usd == 0  # ... but the documented never-billed refusal is settled at $0
    assert gem.response_sha256 == PROD_RESPONSE_SHA256
    usd, _tokens = r.spent(lineage)
    assert usd == OK_COST  # the question's tally: what was really spent
    # the spend guard: the writer's reservation was released, not booked; only the task's cost is spent
    assert budget.reserved == 0 and budget.spent == OK_COST and budget.denied == 0
    await r.aclose()


@pytest.mark.parametrize(
    ("writer", "status", "body"),
    [
        ("w-other", 503, PROD_503),  # the same prod body on a profile without the opt-in
        *[("w-gem", 503, b) for b in COUNTER_EXAMPLES],
        *[("w-gem", 503, b) for b in WITH_EVIDENCE[:2]],  # choices beside it; usage: null
        ("w-gem", 500, PROD_503.replace(b"503", b"500").replace(b"UNAVAILABLE", b"INTERNAL")),
        ("w-gem", 529, PROD_503.replace(b"503", b"529")),  # 529: not declared by Google
        ("w-gem", 503, b"<html>503</html>"),
    ],
)
async def test_everything_else_is_still_charged_at_the_worst_case(
    writer_profiles,  # noqa: ANN001
    writer: str,
    status: int,
    body: bytes,
) -> None:
    """D-062 (5) unchanged outside the opt-in's exact envelope: the attempt is booked at its worst
    case, and the spend guard books that worst case as spent (its reservation is settled, not left)."""
    r, budget = _researcher(writer, status, body)
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.fallbacks == [(writer, "unavailable")] and res.profile == "t-task"
    rows = r.provider.ledger.inner.rows
    w = rows[0]
    assert w.outcome == "http_error" and w.cost_usd == w.reserved_usd > Decimal("0.06")
    assert r.spent(lineage)[0] == w.cost_usd + OK_COST
    assert budget.reserved == 0 and budget.spent == w.cost_usd + OK_COST
    await r.aclose()


@pytest.mark.parametrize("writer", ["w-gem", "w-other"])
async def test_after_a_writer_503_the_second_prose_call_runs_only_with_the_opt_in(
    writer_profiles,  # noqa: ANN001
    writer: str,
) -> None:
    """The prod abstention, end to end through ``_Run.call`` (the per-question budget check): the
    first prose call meets the 503 and the task writes; the refine path's SECOND prose call (its
    worst case priced by the writer, ~$0.069) is then actually made. With the Google opt-in it fits
    HLM_RESEARCH_MAX_USD and the recovered writer answers it; without it (an unknown provider) the
    503's worst case is still booked and the question stops on its budget, as before."""
    r, budget = _researcher(writer, 503, PROD_503, failures=1)
    run = _run(r)
    worst_usd, _worst_tokens = r.worst_case("prose", BIG_USER)
    assert worst_usd > Decimal(str(MAX_USD)) / 2  # two writer worst cases never fit the question

    def build() -> tuple[str, list[int]]:
        return BIG_USER, []

    first = await run.call("prose", build, rsv.ANSWER_CAP_S)
    assert first is not None and run.last_result.profile == "t-task"
    if writer == "w-gem":
        second = await run.call("prose", build, rsv.ANSWER_CAP_S)
        assert second is not None and second["answer"] == PROSE_OK["answer"]
        assert run.last_result.profile == "w-gem" and run.flags["budget_stop"] is False
        assert run.steps == ["prose", "prose"]
        assert r.attempts(run.lineage) == [("w-gem", "http_error"), ("t-task", "ok"), ("w-gem", "ok")]
        assert r.spent(run.lineage)[0] == 2 * OK_COST <= Decimal(str(MAX_USD))
        assert budget.reserved == 0 and budget.spent == 2 * OK_COST
    else:
        with pytest.raises(rs.ResearchUnavailable) as exc:
            await run.call("prose", build, rsv.ANSWER_CAP_S)
        assert exc.value.reason == "question_budget" and run.flags["budget_stop"] is True
        assert run.steps == ["prose"]  # nothing more was sent
        assert r.attempts(run.lineage) == [("w-other", "http_error"), ("t-task", "ok")]
        assert budget.reserved == 0 and budget.spent > Decimal("0.06")
    await r.aclose()
