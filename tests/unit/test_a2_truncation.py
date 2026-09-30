"""D-207 (GOAL-PLAN A2 item 1): memory.ask truncation is never silent: ``meta.flags.truncated`` plus a
visible last marker line inside the token budget; the default response budget is 6000."""

from __future__ import annotations

import json

from hlmemo.core import research_service as rsv
from hlmemo.core.budget import Meter
from hlmemo.librarian.tasks import research as rs

METER = Meter()


def _big_out(flags: dict | None = None) -> dict:
    return {
        "project": "p",
        "answer": "The answer. " * 20,
        "abstained": False,
        "confidence": "high",
        "claims": [
            {"text": f"Claim {i}.", "support": [{"handle": f"v{i}.0", "quote": "q " * 40}]} for i in range(6)
        ],
        "primary": [{"handle": f"v{i}.0", "path": f"docs/{i}.md", "quote": "q " * 60} for i in range(4)],
        "related": [{"handle": f"v{i}.1", "path": f"docs/r{i}.md"} for i in range(5)],
        "meta": {"calls": 3, "flags": dict(flags or {"truncated": False})},
    }


def _prose(answer: str, sources: list[str]) -> dict:
    return {"status": "answered", "answer": answer, "sources": sources, "related": [], "confidence": "high"}


def test_default_budget_is_6000_and_only_a_response_budget() -> None:
    from hlmemo.server.tools import ask as ask_tool

    assert rsv.DEFAULT_BUDGET == 6000
    bud = ask_tool.INPUT_SCHEMA["properties"]["token_budget"]
    assert bud["default"] == 6000
    assert bud["minimum"] == 256 and bud["maximum"] == 32000  # min/max unchanged
    assert rsv.parse_request({"question": "x"}).token_budget == 6000


def test_pack_dropping_claims_flags_truncated_and_ends_with_a_marker() -> None:
    full = rsv._pack(METER, _big_out(), 8000)
    assert full["meta"]["flags"]["truncated"] is False and "[answer truncated" not in full["answer"]
    budget = full["budget"]["used"] - 500  # forces claims and primary sources out
    packed = rsv._pack(METER, _big_out(), budget)
    assert len(packed["claims"]) < 6
    assert packed["meta"]["flags"]["truncated"] is True
    last = packed["answer"].splitlines()[-1]
    assert last == f"[answer truncated at token_budget={budget}; re-ask with a larger token_budget]"
    assert packed["budget"]["used"] == METER.count(packed) <= budget  # the marker is inside the budget


def test_pack_related_only_trim_is_not_a_truncation() -> None:
    out = _big_out()
    out["related"] = [{"handle": f"v{i}.1", "path": "docs/" + "r" * 400 + ".md"} for i in range(40)]
    big = rsv._pack(METER, json.loads(json.dumps(out)), 20000)
    budget = METER.count(big) - 300
    packed = rsv._pack(METER, json.loads(json.dumps(out)), budget)
    assert len(packed["related"]) < 40 and len(packed["claims"]) == 6
    assert packed["meta"]["flags"]["truncated"] is False and "[answer truncated" not in packed["answer"]


def test_answer_length_limit_sets_the_flag_and_a_marker() -> None:
    sentences = [f"Step {i} runs the gate for run {i}." for i in range(1, 400)]
    text = " ".join(sentences)
    shown = {"v1.0": rs.Excerpt("v1.0", 1, "T", "docs/a.md", "2026-09-26", text)}
    v = rs.validate_prose(_prose(text, ["v1.0"]), shown)
    assert v.answered and v.truncated and len(v.answer) <= rs.ANSWER_MAX_CHARS
    assert "capped" not in v.drop_reasons  # the signal is not a drop reason
    short = rs.validate_prose(_prose("Step 1 runs the gate for run 1.", ["v1.0"]), shown)
    assert not short.truncated
    packed = rsv._pack(METER, _big_out({"truncated": True}), 8000)
    assert packed["meta"]["flags"]["truncated"] is True
    assert (
        packed["answer"].splitlines()[-1].startswith("[answer truncated at the 3200-character answer limit")
    )
    assert packed["budget"]["used"] == METER.count(packed) <= 8000
    abstained = {**_big_out({"truncated": True}), "answer": "", "abstained": True}
    assert "[answer truncated" not in rsv._pack(METER, abstained, 8000)["answer"]
