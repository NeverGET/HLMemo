"""D-212 (GOAL-PLAN A2 item 4): writer billing/quota failures are classified as ``billing_or_quota``
(the classifier, ``probe-writer``, the ops status line); no ledger migration (the rows stay http_error)."""

from __future__ import annotations

import json

import httpx
import pytest

from hlmemo.librarian.provider import BILLING_OR_QUOTA, is_billing_or_quota
from hlmemo.ops import cli, probe

GEMINI_429_QUOTA = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
    }
}
GEMINI_429_PREPAY = {
    "error": {
        "code": 429,
        "message": "Your prepayment credits are depleted. Please go to AI Studio to manage your project.",
        "status": "RESOURCE_EXHAUSTED",
    }
}
OPENROUTER_402 = {"error": {"code": 402, "message": "Insufficient credits. Add more using the billing page."}}
FORBIDDEN_BILLING = {"error": {"code": 403, "message": "Billing account is disabled for this project."}}


def test_billing_and_quota_bodies_are_classified() -> None:
    assert BILLING_OR_QUOTA == "billing_or_quota"
    assert is_billing_or_quota(429, GEMINI_429_QUOTA)
    assert is_billing_or_quota(429, json.dumps(GEMINI_429_PREPAY).encode())
    assert is_billing_or_quota(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
    assert is_billing_or_quota(402, OPENROUTER_402)
    assert is_billing_or_quota(402, b"")  # a 402 is billing whatever the body says
    assert is_billing_or_quota(403, FORBIDDEN_BILLING)
    assert is_billing_or_quota(403, "Your prepay balance is zero")


def test_other_failures_are_not_billing() -> None:
    assert not is_billing_or_quota(
        429, {"error": {"message": "Too many requests, slow down"}}
    )  # a rate limit
    assert not is_billing_or_quota(403, {"error": {"message": "Permission denied for this model"}})
    assert not is_billing_or_quota(401, {"error": {"message": "Invalid API key"}})
    assert not is_billing_or_quota(401, {"error": {"message": "billing"}})  # only 402/403/429 qualify
    assert not is_billing_or_quota(503, {"error": {"message": "quota"}})
    assert not is_billing_or_quota(200, None) and not is_billing_or_quota(429, None)


@pytest.fixture
def writer_env(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    (tmp_path / "w-gem.toml").write_text(
        'HLM_LLM_BASE_URL = "http://w-gem.invalid/v1"\nHLM_LLM_MODEL = "stub/w-gem"\n'
        'HLM_LLM_API_KEY = "env:TEST_WRITER_KEY"\nprice_in_per_m = 0.75\nprice_out_per_m = 3.75\n'
        'extra = { response_format = { type = "json_object" } }\nprice_valid_until = "2099-12-31"\n'
    )
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "w-gem")
    monkeypatch.setenv("TEST_WRITER_KEY", "sentinel-writer-credential-not-a-real-key")


def _probe(monkeypatch, capsys, response: httpx.Response) -> tuple[int, dict]:  # noqa: ANN001
    monkeypatch.setattr(probe, "TRANSPORT", httpx.MockTransport(lambda _r: response))
    rc = cli.main(["probe-writer"])
    return rc, json.loads(capsys.readouterr().out.strip())


@pytest.mark.parametrize(
    ("status", "body"),
    [(429, GEMINI_429_QUOTA), (429, GEMINI_429_PREPAY), (402, OPENROUTER_402), (403, FORBIDDEN_BILLING)],
)
def test_probe_writer_reports_billing_or_quota(writer_env, monkeypatch, capsys, status, body) -> None:  # noqa: ANN001
    rc, out = _probe(monkeypatch, capsys, httpx.Response(status, json=body))
    assert (
        rc != 0 and out["ok"] is False and out["status"] == "billing_or_quota" and out["profile"] == "w-gem"
    )


def test_probe_writer_keeps_other_statuses(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    _rc, out = _probe(monkeypatch, capsys, httpx.Response(429, json={"error": {"message": "slow down"}}))
    assert out["status"] == 429
    _rc, out = _probe(monkeypatch, capsys, httpx.Response(401, json={"error": {"message": "bad key"}}))
    assert out["status"] == 401


def test_probe_writer_billing_error_inside_a_200(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    _rc, out = _probe(monkeypatch, capsys, httpx.Response(200, json=GEMINI_429_QUOTA))
    assert out["ok"] is False and out["status"] == "billing_or_quota"


def test_ops_status_warns_on_billing_errors() -> None:
    lines = cli.research_lines({"enabled": True, "writer_profile": "w-gem", "writer_billing_quota_24h": 3})
    assert lines[-1] == (
        "WARNING     writer billing/quota errors in the last 24 h: 3 (check the provider balance/auto-reload)"
    )
    none = cli.research_lines({"enabled": True, "writer_profile": "w-gem", "writer_billing_quota_24h": 0})
    assert not any("billing/quota" in x for x in none)
