"""R4.1 review F-4: a normal rate-limit 429 is not ``billing_or_quota``; real billing/credit/daily or
monthly exhaustion still is."""

from __future__ import annotations

import json

import httpx
import pytest

from hlmemo.librarian.provider import is_billing_or_quota
from hlmemo.ops import cli, probe

BALANCER = {"error": {"message": "Load balancer rate limit exceeded"}}
GEMINI_RPM = {
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": "You exceeded your current quota, please check your plan and billing details. "
        "Please retry in 26s.",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}],
    }
}
GEMINI_DAILY = {
    "error": {
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}],
    }
}


@pytest.mark.parametrize(
    "body",
    [
        BALANCER,
        GEMINI_RPM,
        {"error": {"message": "Rate limit reached for requests per minute (RPM): Limit 60, Used 60"}},
        {"error": {"message": "Quota exceeded for quota metric 'Requests per minute'"}},
        {"error": {"message": "Too many requests, slow down"}},
        {"error": {"message": "Account balancer overloaded"}},
    ],
)
def test_rate_limits_are_not_billing(body) -> None:  # noqa: ANN001
    assert not is_billing_or_quota(429, body)
    assert not is_billing_or_quota(429, json.dumps(body).encode())


def test_retry_after_header_makes_a_plain_429_a_rate_limit() -> None:
    quota_only = {"error": {"status": "RESOURCE_EXHAUSTED"}}
    assert is_billing_or_quota(429, quota_only)  # no hint: still the old reading
    assert not is_billing_or_quota(429, quota_only, "30")
    assert not is_billing_or_quota(429, b"", "30")


@pytest.mark.parametrize(
    "body",
    [
        GEMINI_DAILY,
        {"error": {"message": "Your prepayment credits are depleted"}},
        {"error": {"message": "Insufficient credits. Add more using the billing page."}},
        {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}},
        {"error": {"message": "Your account balance is too low"}},
        {"error": {"message": "Monthly quota exhausted"}},
        {"error": {"message": "Daily limit reached. Rate limit resets tomorrow."}},  # hard beats the hint
    ],
)
def test_billing_and_daily_monthly_exhaustion_still_count(body) -> None:  # noqa: ANN001
    assert is_billing_or_quota(429, body)
    assert is_billing_or_quota(429, body, "3600")  # a Retry-After does not hide a hard billing word


def test_402_and_other_statuses_unchanged() -> None:
    assert is_billing_or_quota(402, b"")
    assert not is_billing_or_quota(503, {"error": {"message": "quota"}})
    assert not is_billing_or_quota(401, {"error": {"message": "billing"}})


def test_probe_writer_reports_a_rate_limit_as_429(tmp_path, monkeypatch, capsys) -> None:  # noqa: ANN001
    (tmp_path / "w-gem.toml").write_text(
        'HLM_LLM_BASE_URL = "http://w-gem.invalid/v1"\nHLM_LLM_MODEL = "stub/w-gem"\n'
        'HLM_LLM_API_KEY = "env:TEST_WRITER_KEY"\nprice_in_per_m = 0.75\nprice_out_per_m = 3.75\n'
        'extra = { response_format = { type = "json_object" } }\nprice_valid_until = "2099-12-31"\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "w-gem")
    monkeypatch.setenv("TEST_WRITER_KEY", "sentinel-writer-credential-not-a-real-key")
    resp = httpx.Response(429, json=GEMINI_RPM, headers={"retry-after": "26"})
    monkeypatch.setattr(probe, "TRANSPORT", httpx.MockTransport(lambda _r: resp))
    cli.main(["probe-writer"])
    out = json.loads(capsys.readouterr().out.strip())
    assert out["status"] == 429
