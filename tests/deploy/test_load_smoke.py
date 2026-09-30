"""R4 (R-10, plan §2.2): the real-time load smoke's driver (deploy/smoke/load_smoke.py), OFFLINE.

Only its verdict logic (and its refusal of a non-local target or a missing token, which exits before
any request): the smoke itself runs on a disposable local VM, never here.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("load_smoke", ROOT / "deploy" / "smoke" / "load_smoke.py")
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def _obs(**over: Any) -> dict[str, Any]:
    """A passing run: 4 asks answered in time, fast traffic, /ready 200 throughout, the 5th busy."""
    base = {
        "asks": [
            {"delay_s": 60, "seconds": 63.2, "ok": True, "error": None},
            {"delay_s": 90, "seconds": 93.0, "ok": True, "error": None},
            {"delay_s": 130, "seconds": 124.1, "ok": True, "error": None},  # cut at 120 s, fallback wrote
            {"delay_s": 170, "seconds": 124.6, "ok": True, "error": None},
        ],
        "query_s": [0.2] * 190 + [1.5] * 10,
        "write_s": [0.3] * 60,
        "query_errors": [],
        "write_errors": [],
        "ready": [200] * 26,
        "fifth": {"reason": "busy", "seconds": 0.4},
    }
    return {**base, **over}


def test_a_clean_run_passes_and_is_json() -> None:
    out = smoke.verdict(_obs())
    assert out["pass"] is True and all(c["pass"] for c in out["checks"].values())
    assert set(out["checks"]) == {"asks_complete", "query_p95", "write_p95", "ready", "fifth_busy"}
    assert out["checks"]["asks_complete"]["limit_s"] == 180.0  # the 170 s research timeout + 10 s
    assert out["checks"]["query_p95"]["p95_s"] == 0.2 and out["checks"]["query_p95"]["n"] == 200
    json.dumps(out)  # printable as ONE JSON line


@pytest.mark.parametrize(
    ("asks", "why"),
    [
        (
            [{"delay_s": d, "seconds": 70.0, "ok": True, "error": None} for d in (60, 90, 130)],
            "only 3 asks",
        ),
        (
            [
                *({"delay_s": d, "seconds": 70.0, "ok": True, "error": None} for d in (60, 90, 130)),
                {"delay_s": 170, "seconds": 180.5, "ok": True, "error": None},
            ],
            "past 170 + 10 s",
        ),
        (
            [
                *({"delay_s": d, "seconds": 70.0, "ok": True, "error": None} for d in (60, 90, 130)),
                {"delay_s": 170, "seconds": 170.2, "ok": False, "error": "timeout"},
            ],
            "an E_UNAVAILABLE is not a completion",
        ),
        (
            [
                *({"delay_s": d, "seconds": 70.0, "ok": True, "error": None} for d in (60, 90, 130)),
                {"delay_s": 170, "seconds": None, "ok": False, "error": "transport: TimeoutError"},
            ],
            "a client-side cut",
        ),
    ],
)
def test_an_ask_that_does_not_complete_in_time_fails(asks: list[dict[str, Any]], why: str) -> None:
    out = smoke.verdict(_obs(asks=asks))
    assert out["pass"] is False and out["checks"]["asks_complete"]["pass"] is False, why
    assert out["checks"]["query_p95"]["pass"] is True  # the other checks are independent


def test_p95_is_nearest_rank_and_below_the_limit() -> None:
    assert smoke.p95([]) is None and smoke.p95([3.0]) == 3.0
    assert smoke.p95([0.1] * 19 + [5.0]) == 0.1  # the 19th of 20
    assert smoke.p95([0.1] * 18 + [5.0] * 2) == 5.0
    slow = smoke.verdict(_obs(query_s=[0.1] * 18 + [2.5] * 2))
    assert slow["pass"] is False and slow["checks"]["query_p95"]["p95_s"] == 2.5
    edge = smoke.verdict(_obs(write_s=[2.0] * 10))  # "< 2 s": exactly 2 s fails
    assert edge["checks"]["write_p95"]["pass"] is False


@pytest.mark.parametrize("kind", ["query", "write"])
def test_traffic_errors_or_no_samples_fail(kind: str) -> None:
    errored = smoke.verdict(_obs(**{f"{kind}_errors": ["E_UNAVAILABLE"]}))
    assert errored["pass"] is False and errored["checks"][f"{kind}_p95"]["errors"] == 1
    empty = smoke.verdict(_obs(**{f"{kind}_s": []}))
    assert empty["pass"] is False and empty["checks"][f"{kind}_p95"]["p95_s"] is None


@pytest.mark.parametrize("ready", [[], [200, 200, 503, 200], [200, "transport: TimeoutError"]])
def test_ready_must_always_be_200(ready: list[Any]) -> None:
    out = smoke.verdict(_obs(ready=ready))
    assert out["pass"] is False and out["checks"]["ready"]["pass"] is False


@pytest.mark.parametrize(
    "fifth",
    [
        None,
        {"reason": "answered", "seconds": 64.0},
        {"reason": "timeout", "seconds": 1.0},
        {"reason": "busy", "seconds": 6.0},
    ],
)
def test_the_fifth_ask_must_be_busy_at_once(fifth: dict[str, Any] | None) -> None:
    out = smoke.verdict(_obs(fifth=fifth))
    assert out["pass"] is False and out["checks"]["fifth_busy"]["pass"] is False


def test_limits_follow_the_arguments() -> None:
    asks = [{"delay_s": 5, "seconds": 9.0, "ok": True, "error": None}]
    out = smoke.verdict(_obs(asks=asks), {"expected_asks": 1, "ask_limit_s": 10.0})
    assert out["checks"]["asks_complete"]["pass"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:8443",
        "https://127.0.0.1:8443",
        "http://192.168.64.7",
        "https://hlm-vm.local",
        "https://[::1]:8443",
    ],
)
def test_a_local_target_is_accepted(url: str) -> None:
    assert smoke.local_target(url, None) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.hlmemo.com",
        "https://153.92.1.166",
        "https://example.org:8443",
        "https://localhost:8443/mcp",  # a path
        "https://user:pw@localhost",  # credentials
        "ftp://localhost",
    ],
)
def test_a_non_local_or_malformed_target_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        smoke.local_target(url, None)
    assert smoke.local_target("https://vm-r4.example:8443", "vm-r4.example") == "https://vm-r4.example:8443"


def test_main_refuses_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "run", lambda *_a: pytest.fail("no request may be made"))
    monkeypatch.delenv("HLM_SMOKE_TOKEN", raising=False)
    assert smoke.main(["--url", "https://localhost:8443", "--project", "p"]) == 2  # no token
    monkeypatch.setenv("HLM_SMOKE_TOKEN", "sentinel-smoke-token-not-real")
    assert smoke.main(["--url", "https://mcp.hlmemo.com", "--project", "p"]) == 2  # not local
    cap = capsys.readouterr()
    assert "sentinel-smoke-token-not-real" not in cap.out + cap.err and cap.out == ""


def test_main_prints_one_verdict_line_and_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HLM_SMOKE_TOKEN", "sentinel-smoke-token-not-real")
    monkeypatch.setattr(smoke, "run", lambda _args, token: _obs())
    assert smoke.main(["--url", "https://localhost:8443", "--project", "p"]) == 0
    monkeypatch.setattr(
        smoke, "run", lambda _args, token: _obs(fifth={"reason": "answered", "seconds": 60.0})
    )
    assert smoke.main(["--url", "https://localhost:8443", "--project", "p"]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and [json.loads(ln)["pass"] for ln in lines] == [True, False]
    assert all("sentinel-smoke-token-not-real" not in ln for ln in lines)


def test_the_mock_never_takes_the_api_port() -> None:
    """The mock runs inside the api container (the setup notes), where uvicorn holds HLM_API_PORT:
    the smoke profiles and the mock's default port must be another loopback port (local rehearsal
    2026-09-30: both said 8765, and binding it in the api container failed with EADDRINUSE)."""
    import re
    import tomllib
    from urllib.parse import urlsplit

    compose = (ROOT / "deploy" / "compose.prod.yaml").read_text()
    match = re.search(r'HLM_API_PORT:\s*"(\d+)"', compose)
    assert match
    api_port = int(match.group(1))
    mock_path = ROOT / "deploy" / "smoke" / "mock_provider.py"
    spec = importlib.util.spec_from_file_location("mock_provider", mock_path)
    assert spec and spec.loader
    mock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock)
    assert mock.DEFAULT_PORT != api_port
    profiles = sorted((ROOT / "deploy" / "smoke" / "profiles").glob("*.toml"))
    assert len(profiles) == 2
    for path in profiles:
        url = urlsplit(tomllib.loads(path.read_text())["HLM_LLM_BASE_URL"])
        assert (url.hostname, url.port) == ("127.0.0.1", mock.DEFAULT_PORT), path.name
