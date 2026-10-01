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
a response settles at $0 only when its body is exactly the declared envelope (no duplicate key, no
undeclared field: so no usage or output either). A profile without the opt-in, any other 5xx and
any other body keep the worst case (reviews: docs/consults/93-astra-writer-503.md, round 2
docs/consults/94-*: a response that cannot be read after the reservation is settled once at the
worst case; the policy lives in the profile file only and a malformed one stops the api)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import tomllib
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from hlmemo.config import Settings, get_settings
from hlmemo.core import research_service as rsv
from hlmemo.librarian import privacy
from hlmemo.librarian import provider as prov
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.errors import LlmConfigError, ProviderUnavailable
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import LlmProfile, UnbilledError, named_profile, primary_profile
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.tasks import research as rs
from hlmemo.server.app import check_llm_config

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
#: consult 94 #4: a duplicate key at any depth (``json.loads`` keeps the LAST one, so the first,
#: with its usage, was never seen): the body is rejected, worst case
DUPLICATE_KEYS = [
    b'[{"error":{"usage":{"completion_tokens":100}},"error":{"code":503,"message":"m","status":"UNAVAILABLE"}}]',
    b'[{"error":{"code":503,"message":"m","status":"UNAVAILABLE","status":"UNAVAILABLE"}}]',
    b'[{"error":{"code":503,"message":"m","message":"n","status":"UNAVAILABLE"}}]',
    b'[{"error":{"code":503,"message":"m","status":"UNAVAILABLE"},'
    b'"error":{"code":503,"message":"m","status":"UNAVAILABLE"}}]',
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


def _toml(value: object) -> str:
    """A TOML inline value of a policy (tables, arrays, strings, integers)."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value)  # a string (JSON escapes are TOML basic-string escapes) or an integer


@pytest.fixture
def writer_profiles(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """``w-gem`` carries the shipped Google profile's own ``unbilled_errors`` (copied from the file);
    ``w-other`` is the same endpoint WITHOUT the opt-in (an unknown/other provider)."""
    policy = _toml(_shipped(GOOGLE_PROFILES[1])["unbilled_errors"])
    (tmp_path / "w-gem.toml").write_text(_profile_file("w-gem") + f"unbilled_errors = {policy}\n")
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
    writer: str,
    writer_status: int,
    writer_body: bytes,
    *,
    failures: int | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[rs.Researcher, MemoryBudget]:
    """A prose Researcher whose writer ``writer`` answers ``writer_status``/``writer_body`` (with
    ``headers``; its first ``failures`` requests only, when given; then a normal answer); the task
    profile always answers. The reservations go through a ``MemoryBudget`` (the spend guard's
    contract in memory)."""
    sent = {"writer": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == f"stub/{writer}":
            sent["writer"] += 1
            if failures is None or sent["writer"] <= failures:
                return httpx.Response(  # a stream: the CLIENT reads (and decodes) it, as on the wire
                    writer_status,
                    stream=httpx.ByteStream(writer_body),
                    headers={"content-type": "application/json", **(headers or {})},
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
        # consult 94 #3: only an absent key or [] is "off"; any other falsy value is malformed
        "false",
        "0",
        "{}",
        '""',
        "true",
        "1",
        '"[]"',  # a string is not a table array (there is no JSON/env form)
        "'" + json.dumps(GOOGLE_POLICY) + "'",
        '"env:HLM_UNBILLED_ERRORS"',
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
        *[(503, b, False) for b in DUPLICATE_KEYS],
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
        ("w-gem", 503, DUPLICATE_KEYS[0]),  # consult 94 #4: the reviewer's duplicate-key body
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


# --------------------------------------------------------------------------- consult 94 (round 2)
def test_only_an_absent_key_or_an_empty_array_is_off(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Consult 94 #3: ``unbilled_errors = []`` (or no key) is the default "off"; every other falsy
    value is a configuration error (``test_a_malformed_policy_is_a_configuration_error``)."""
    (tmp_path / "empty.toml").write_text(_profile_file("empty") + "unbilled_errors = []\n")
    (tmp_path / "absent.toml").write_text(_profile_file("absent"))
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    assert named_profile("empty").unbilled_errors == () == named_profile("absent").unbilled_errors


def test_the_policy_comes_only_from_the_profile_file(writer_profiles, monkeypatch) -> None:  # noqa: ANN001
    """Consult 94 #5: the policy is release state only through the profile FILE (in the image, under
    the release's fingerprint and manifest). ``HLM_UNBILLED_ERRORS``, an ``[hlm]`` key and a Settings
    argument are not settings at all: none of them widens or narrows a primary profile's policy."""
    want = named_profile("w-gem").unbilled_errors
    assert want and "unbilled_errors" not in Settings.model_fields
    monkeypatch.setenv("HLM_UNBILLED_ERRORS", json.dumps(GOOGLE_POLICY))
    monkeypatch.setenv("HLM_PROFILE", "w-other")
    assert primary_profile(get_settings()).unbilled_errors == ()  # the env cannot opt a profile in
    assert primary_profile(get_settings(unbilled_errors=GOOGLE_POLICY)).unbilled_errors == ()
    monkeypatch.setenv("HLM_UNBILLED_ERRORS", "[]")
    monkeypatch.setenv("HLM_PROFILE", "w-gem")
    assert primary_profile(get_settings()).unbilled_errors == want  # ... nor turn the file's off
    toml = writer_profiles / "hlm.toml"
    toml.write_text("[hlm]\nunbilled_errors = []\n[profiles.w-other]\nunbilled_errors = []\n")
    monkeypatch.setenv("HLM_CONFIG", str(toml))
    assert primary_profile(get_settings()).unbilled_errors == want
    monkeypatch.setenv("HLM_UNBILLED_ERRORS", "false")  # not even read: no startup error from it
    assert primary_profile(get_settings()).unbilled_errors == want


def _writer_settings(writer: str | None, **kw: object) -> Settings:
    return get_settings(
        **{
            "profile": "w-other",
            "fallback_profile": None,
            "task_fallback_profiles": {},
            "librarian_enabled": True,
            "llm_mode": "live",
            "research_enabled": True,
            "research_answer_mode": "prose",
            "research_writer_profile": writer,
            **kw,
        }
    )


@pytest.mark.parametrize(
    "policy", ["false", "{}", '[{ http_status = 503, wrappers = ["list"], error = {} }]']
)
def test_a_malformed_writer_policy_stops_the_api_at_startup(writer_profiles, policy: str) -> None:  # noqa: ANN001
    """Consult 94 #3: the writer profile is resolved lazily (on the first question) and a broken one
    is logged and replaced by the research profile (D-171). A malformed spend-settlement policy is
    never that: the api refuses to start (``check_llm_config``, the lifespan's first check), and the
    lazy resolution raises instead of writing with the task profile."""
    (writer_profiles / "w-bad.toml").write_text(_profile_file("w-bad") + f"unbilled_errors = {policy}\n")
    s = _writer_settings("w-bad")
    with pytest.raises(LlmConfigError, match=r"HLM_RESEARCH_WRITER_PROFILE='w-bad'.*unbilled_errors"):
        check_llm_config(s)
    with pytest.raises(LlmConfigError, match=r"HLM_RESEARCH_WRITER_PROFILE='w-bad'.*unbilled_errors"):
        rs.writer_chain(s, [_task_profile()])
    # the same broken policy on the research PRIMARY: never "research disabled" in silence either
    s = _writer_settings(None, profile="w-bad")
    with pytest.raises(LlmConfigError, match="unbilled_errors"):
        check_llm_config(s)
    with pytest.raises(LlmConfigError, match="unbilled_errors"):
        rs.research_chain(s)


def test_an_unknown_writer_still_only_warns(writer_profiles, caplog) -> None:  # noqa: ANN001
    """D-171 unchanged outside the policy: an unknown writer profile is logged at startup and the
    research profile writes (``test_d171_writer_chain_resolution``)."""
    s = _writer_settings("no-such-profile")
    check_llm_config(s)
    assert rs.writer_chain(s, [_task_profile()]) == []
    assert "no-such-profile" in caplog.text


@pytest.mark.parametrize("declared", ["candidates = 0", 'usageMetadata = "none"', 'retry_token = "t-1"'])
def test_a_declared_field_is_matched_whatever_its_name(tmp_path, monkeypatch, declared: str) -> None:  # noqa: ANN001
    """D-017 (consult 94 #2): the matcher used to veto a body holding any key of a SHARED list of
    OpenAI and Google names (``usage``, ``choices``, ``usageMetadata``, ``candidates``) or a
    token-named key, even when the profile declared that very field: a generic profile's documented
    envelope settled at the worst case because of another provider's names. The profile's exact
    declaration is the only authority; the same body without the field, or with another value,
    keeps the worst case."""
    (tmp_path / "g.toml").write_text(
        _profile_file("g") + 'unbilled_errors = [{ http_status = 529, wrappers = ["object"],'
        f' error = {{ type = "overloaded_error", {declared} }} }}]\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    policy = named_profile("g").unbilled_errors
    key, value = (part.strip() for part in declared.split("=", 1))
    err = {"type": "overloaded_error", key: json.loads(value), "message": "Overloaded"}
    assert prov.is_unbilled_error(policy, 529, json.dumps({"error": err}).encode()) is True
    other = {**err, key: "other" if isinstance(err[key], int) else 1}
    assert prov.is_unbilled_error(policy, 529, json.dumps({"error": other}).encode()) is False
    missing = {k: v for k, v in err.items() if k != key}
    assert prov.is_unbilled_error(policy, 529, json.dumps({"error": missing}).encode()) is False


async def test_a_generic_529_policy_settles_its_declared_envelope_at_zero(writer_profiles) -> None:  # noqa: ANN001
    """Consult 94 #2, end to end: a non-Google profile declaring a 529 whose error object carries a
    ``candidates`` field is settled at $0 for exactly that body (it was booked at the worst case)."""
    (writer_profiles / "w-529.toml").write_text(
        _profile_file("w-529") + 'unbilled_errors = [{ http_status = 529, wrappers = ["object"],'
        ' error = { type = "overloaded_error", candidates = 0 } }]\n'
    )
    body = b'{"error": {"type": "overloaded_error", "candidates": 0, "message": "Overloaded"}}'
    r, budget = _researcher("w-529", 529, body)
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.profile == "t-task" and res.fallbacks == [("w-529", "unavailable")]
    w = r.provider.ledger.inner.rows[0]
    assert w.outcome == "http_error" and w.reserved_usd > Decimal("0.06") and w.cost_usd == 0
    assert budget.reserved == 0 and budget.spent == OK_COST == r.spent(lineage)[0]
    await r.aclose()


#: consult 94 #1: responses that raise AFTER the reservation, before it was settled
UNREADABLE = [
    pytest.param(503, b"not-gzip", {"content-encoding": "gzip"}, id="corrupt-gzip-503"),
    pytest.param(503, b"not-deflate", {"content-encoding": "deflate"}, id="corrupt-deflate-503"),
    pytest.param(200, b"not-gzip", {"content-encoding": "gzip"}, id="corrupt-gzip-200"),
    pytest.param(200, b'{"choices": "abc"}', {}, id="unwalkable-200"),
    pytest.param(
        200,
        json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": "x"},
            }
        ).encode(),
        {},
        id="unreadable-usage-200",
    ),
]


@pytest.mark.parametrize("writer", ["w-gem", "w-other"])
@pytest.mark.parametrize(("status", "body", "headers"), UNREADABLE)
async def test_an_unreadable_response_is_settled_once_at_the_worst_case(
    writer_profiles,  # noqa: ANN001
    writer: str,
    status: int,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Consult 94 #1 (HIGH; pre-existing on main 0986678): a response that raises after the
    reservation (a gzip body httpx cannot decode: ``httpx.DecodingError``, which is not a
    ``TransportError``; a body this code cannot walk) left the reservation OPEN: ``MemoryBudget``
    leaked it for good, ``DbBudget`` held it until the TTL sweep booked its worst case, no ledger row
    was written and the question failed instead of falling back. Billing is uncertain: it is settled
    once at the worst case, its ``http_error`` row is written and the next profile answers."""
    r, budget = _researcher(writer, status, body, headers=headers)
    lineage = str(uuid.uuid4())
    res = await _prose(r, lineage)
    assert res.profile == "t-task" and res.fallbacks == [(writer, "unavailable")]
    rows = r.provider.ledger.inner.rows
    assert [(x.profile, x.outcome) for x in rows] == [(writer, "http_error"), ("t-task", "ok")]
    w = rows[0]
    assert w.cost_usd == w.reserved_usd > Decimal("0.06")
    assert budget.reserved == 0 and budget._open == {} and budget.spent == w.cost_usd + OK_COST
    assert r.spent(lineage)[0] == w.cost_usd + OK_COST
    await r.aclose()


async def test_a_valid_gzip_503_is_matched_after_decoding(writer_profiles) -> None:  # noqa: ANN001
    """A complete gzip body is decoded by httpx before the matcher sees it: the prod 503, gzipped, is
    still the declared envelope ($0)."""
    r, budget = _researcher("w-gem", 503, gzip.compress(PROD_503), headers={"content-encoding": "gzip"})
    res = await _prose(r, str(uuid.uuid4()))
    w = r.provider.ledger.inner.rows[0]
    assert res.fallbacks == [("w-gem", "unavailable")] and w.cost_usd == 0 < w.reserved_usd
    assert w.response_sha256 == PROD_RESPONSE_SHA256  # the decoded body
    assert budget.reserved == 0 and budget.spent == OK_COST
    await r.aclose()


class _CountingBudget(MemoryBudget):
    """``MemoryBudget`` that records every settlement call."""

    def __init__(self) -> None:
        super().__init__(Decimal(1))
        self.settles: list[tuple[uuid.UUID, Decimal | None]] = []

    async def settle(self, call_id: uuid.UUID, actual_usd: Decimal | None) -> None:
        self.settles.append((call_id, actual_usd))
        await super().settle(call_id, actual_usd)


class _FailingLedger(MemoryLedger):
    """The ledger write of an ``http_error`` row fails (e.g. the database went away)."""

    async def record(self, row) -> None:  # noqa: ANN001
        if row.outcome == "http_error":
            raise OSError("ledger down")
        await super().record(row)


def _lone_provider(handler, budget: MemoryBudget, ledger: MemoryLedger | None = None) -> Provider:  # noqa: ANN001
    return Provider(
        [_task_profile()],
        mode="live",
        budget=budget,
        ledger=ledger or MemoryLedger(),
        transport=httpx.MockTransport(handler),
    )


async def test_a_corrupt_body_settles_exactly_once_without_a_fallback() -> None:
    """Consult 94 #1: with no later profile the call fails as unavailable (not with httpx's
    ``DecodingError``), after exactly one worst-case settlement and its ledger row."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"not-gzip"))

    budget = _CountingBudget()
    p = _lone_provider(handler, budget)
    with pytest.raises(ProviderUnavailable):
        await p.complete(load_task("contradiction"), "USER: x", attempt_policy="latency")
    ((_cid, actual),) = budget.settles
    assert actual is None and budget.reserved == 0 and budget._open == {}
    (row,) = p.ledger.rows
    assert row.outcome == "http_error" and row.cost_usd == row.reserved_usd == budget.spent > 0
    await p.aclose()


async def test_a_failure_after_the_settlement_never_settles_twice() -> None:
    """Exactly once: when the settlement already happened (here the ledger write after it fails),
    the error propagates as before and the reservation is not settled a second time."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": 500, "message": "boom"}})

    budget = _CountingBudget()
    p = _lone_provider(handler, budget, _FailingLedger())
    with pytest.raises(OSError, match="ledger down"):
        await p.complete(load_task("contradiction"), "USER: x", attempt_policy="latency")
    ((_cid, actual),) = budget.settles
    assert actual is not None and actual > 0 and budget.reserved == 0 and budget._open == {}
    await p.aclose()
