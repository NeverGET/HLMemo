"""R4 (R-14): ``python -m hlmemo.ops probe-writer``: one authenticated minimal writer call with THIS
process's settings; it prints only {"ok", "profile", "status", "latency_ms"} and exits non-zero on a
failure; the key never appears in its output."""

from __future__ import annotations

import json

import httpx
import pytest

from hlmemo.ops import cli, probe

SECRET = "sentinel-writer-credential-not-a-real-key"  # must never be printed


@pytest.fixture
def writer_env(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    base = (
        'HLM_LLM_BASE_URL = "http://w-gem.invalid/v1"\nHLM_LLM_MODEL = "stub/w-gem"\n'
        'HLM_LLM_API_KEY = "env:TEST_WRITER_KEY"\nprice_in_per_m = 0.75\nprice_out_per_m = 3.75\n'
        'extra = { reasoning_effort = "high", response_format = { type = "json_object" } }\n'
    )
    (tmp_path / "w-gem.toml").write_text(base + 'price_valid_until = "2099-12-31"\n')
    (tmp_path / "w-old.toml").write_text(base + 'price_valid_until = "2026-01-31"\n')
    monkeypatch.setenv("HLM_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "w-gem")
    monkeypatch.setenv("TEST_WRITER_KEY", SECRET)
    return tmp_path


def _run(monkeypatch, capsys, handler) -> tuple[int, dict, str]:  # noqa: ANN001
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(probe, "TRANSPORT", httpx.MockTransport(record))
    rc = cli.main(["probe-writer"])
    cap = capsys.readouterr()
    out = cap.out.strip().splitlines()
    assert len(out) == 1  # one JSON line, nothing else on stdout
    blob = cap.out + cap.err
    assert SECRET not in blob and "Bearer" not in blob  # no key, no header ever printed
    return rc, json.loads(out[0]), seen


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": '{"ok"'}, "finish_reason": "length"}]},
    )


def test_ok(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    rc, out, seen = _run(monkeypatch, capsys, _ok)
    assert rc == 0 and set(out) == {"ok", "profile", "status", "latency_ms"}
    assert out["ok"] is True and out["profile"] == "w-gem" and out["status"] == 200
    (req,) = seen  # ONE call: the writer's own options, 16 tokens, its key
    body = json.loads(req.content)
    assert body["max_tokens"] == 16 and body["model"] == "stub/w-gem" and body["reasoning_effort"] == "high"
    assert body["response_format"] == {"type": "json_object"}
    assert req.headers["authorization"] == f"Bearer {SECRET}"  # authenticated with this process's key


def test_wrong_key_401_fails(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"bad key {SECRET}"}})  # echoed back

    rc, out, seen = _run(monkeypatch, capsys, unauthorized)
    assert rc != 0 and out == {**out, "ok": False, "profile": "w-gem", "status": 401} and len(seen) == 1


def test_missing_key_fails_without_a_call(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.delenv("TEST_WRITER_KEY")
    rc, out, seen = _run(monkeypatch, capsys, _ok)
    assert rc != 0 and out["ok"] is False and out["status"] == "missing_key" and seen == []


def test_expired_price_and_transport_errors(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "w-old")
    rc, out, seen = _run(monkeypatch, capsys, _ok)
    assert rc != 0 and out["status"] == "price_expired" and seen == []
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "w-gem")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {SECRET}")

    rc, out, _seen = _run(monkeypatch, capsys, down)
    assert rc != 0 and out["status"] == "transport"
    rc, out, _seen = _run(monkeypatch, capsys, lambda _r: httpx.Response(200, text="<html>"))
    assert rc != 0 and out["status"] == "unparseable"
    monkeypatch.setenv("HLM_RESEARCH_WRITER_PROFILE", "no-such-profile")
    rc, out, _seen = _run(monkeypatch, capsys, _ok)
    assert rc != 0 and out["status"] == "config"


def test_unset_writer_probes_the_research_primary(writer_env, monkeypatch, capsys) -> None:  # noqa: ANN001
    """Contract addendum: with HLM_RESEARCH_WRITER_PROFILE unset, the research primary (the profile that
    writes then) is probed and named."""
    monkeypatch.delenv("HLM_RESEARCH_WRITER_PROFILE")
    monkeypatch.setenv("HLM_PROFILE", "w-gem")  # this process's primary profile
    rc, out, seen = _run(monkeypatch, capsys, _ok)
    assert rc == 0 and out["profile"] == "w-gem" and len(seen) == 1
