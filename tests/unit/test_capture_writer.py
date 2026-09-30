"""GOAL-PLAN B1: the writer paths and the summarizer prompt (no network, no prod)."""

from __future__ import annotations

from typing import Any

from hlmemo.capture import config as C
from hlmemo.capture import summarize as S
from hlmemo.capture import write as W

SID = "0a1b2c3d-1111-2222-3333-444455556666"


def test_write_direct_not_feasible_without_token_and_maps_errors(monkeypatch):
    from hlmemo.cli import credentials
    from hlmemo.cli.mcp_client import MemoryClient, ToolCallError

    payload = {"project": "hlmemo", "notes": "n", "session_id": W.session_key(SID, "hlmemo")}
    monkeypatch.setattr(credentials, "load_token", lambda *a, **k: None)
    assert W.write_direct(payload, C.CaptureConfig()) is None  # -> the relay fallback is used

    monkeypatch.setattr(credentials, "load_token", lambda *a, **k: "t-not-a-real-token")
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_call(self, tool, args):  # noqa: ANN001
        calls.append((tool, args))
        if len(calls) == 2:
            raise ToolCallError("E_SESSION_CLOSED", "closed")
        if len(calls) == 3:
            raise ToolCallError("E_UNAVAILABLE", "down", retryable=True)
        return {"ok": True}

    monkeypatch.setattr(MemoryClient, "call", fake_call)
    assert W.write_direct(payload, C.CaptureConfig()).status == "ok"
    assert W.write_direct(payload, C.CaptureConfig()).status == "already_closed"
    out = W.write_direct(payload, C.CaptureConfig())
    assert out.status == "error" and out.detail == "E_UNAVAILABLE"
    assert calls[0] == ("memory.call_the_day", payload)


def test_user_prompt_marks_delimiters_and_span():
    p = S.build_user_prompt("BODY", cwd="/w/proj", date="2026-09-22 to 2026-09-23")
    assert "2026-09-22 to 2026-09-23" in p and "<<<TRANSCRIPT\nBODY\nTRANSCRIPT>>>" in p
    assert "delimiters" in p


def test_session_key_is_uuid5_of_session_and_slug():
    import uuid

    k = W.session_key(SID, "hlmemo")
    assert str(uuid.UUID(k)) == k and uuid.UUID(k).version == 5
    assert k == str(uuid.uuid5(W.NS, f"{SID}:hlmemo"))
    assert W.session_key(SID, "hlmemo", 2) != k
