"""Active-librarian ceiling harness (eval/active): the deterministic grounding checker."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

AL_DIR = Path(__file__).resolve().parents[2] / "eval" / "active"
if str(AL_DIR) not in sys.path:
    sys.path.insert(0, str(AL_DIR))

import al_grounding as G  # noqa: E402
import al_packets as P  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=UTC)
T1 = datetime(2026, 9, 20, tzinfo=UTC)
DECISION = "Retries use exponential backoff capped at 8 seconds."
UNCERTAIN = "The new cache may be slower under load."
OLD = "Retries use a fixed delay of 2 seconds between attempts."
BODY = (
    "Session\n\nWe changed things in D-120.\n\n"
    f"## Uncertain / unverified\n- {UNCERTAIN}\n\n## Decisions\n- {DECISION}"
)


def e1_packet():
    cands = [
        (
            {
                "version_id": 20,
                "kind": "fact",
                "project_id": 1,
                "title": "Retry",
                "body": OLD,
                "valid_from": T0,
            },
            False,
        ),
    ]
    note = {"handle": "v10", "project_id": 1, "title": "Session s1", "body": BODY, "valid_from": T1}
    return P.e1_packet("E1-001", note, cands, slugs={1: "alpha"}, candidate_chars=1500)


def fact(quote, source="v10", text="Retries back off exponentially, up to 8 s.", **extra):
    return {
        "title": "Retry policy",
        "claims": [{"text": text, "evidence": [{"source": source, "quote": quote}]}],
        "as_of": "2026-09-20",
        "open_unknown": [],
        **extra,
    }


def checks(packet, output):
    return {u["uid"]: u["check"] for u in G.check_output(packet, output)}


def test_quote_status_variants():
    text = "Retries use exponential\n   backoff capped at 8 seconds."
    assert G.quote_status("Retries use exponential backoff capped", text) == "verbatim"
    assert G.quote_status("retries use exponential backoff capped", text) == "near"
    assert G.quote_status("Retries use … capped at 8 seconds", text) == "elided"
    assert G.quote_status("Retries", text) == "too_short"
    assert G.quote_status("Retries never back off at all", text) == "absent"


def test_e1_fact_from_decisions_is_grounded():
    pk = e1_packet()
    res = checks(pk, {"abstain": False, "facts": [fact("exponential backoff capped at 8 seconds")]})
    assert res["E1-001#f1"]["grounded_det"] is True
    assert res["E1-001#f1"]["grounding_flags"] == []


def test_e1_fact_from_uncertain_or_elsewhere_is_flagged():
    pk = e1_packet()
    res = checks(
        pk,
        {
            "abstain": False,
            "facts": [
                fact("The new cache may be slower under load"),
                fact("We changed things in D-120"),
                fact(OLD, source="v20"),
                fact("exponential backoff capped at 8 seconds", source="v77"),
            ],
        },
    )
    assert res["E1-001#f1"]["grounding_flags"] == ["from_uncertain"]
    assert res["E1-001#f2"]["grounding_flags"] == ["outside_decisions"]
    assert res["E1-001#f3"]["grounding_flags"] == ["not_from_subject"]
    assert "unknown_source" in res["E1-001#f4"]["grounding_flags"]


def test_e1_paraphrased_quote_is_not_grounded():
    pk = e1_packet()
    res = checks(
        pk, {"abstain": False, "facts": [fact("Retries back off exponentially up to eight seconds")]}
    )
    assert res["E1-001#f1"]["grounding_flags"] == ["quote_not_verbatim"]


def test_invented_ids_and_handles_are_flagged():
    pk = e1_packet()
    q = "exponential backoff capped at 8 seconds"
    res = checks(
        pk,
        {
            "abstain": False,
            "facts": [
                fact(q, text="Decided in D-1 (commit abc1234f) to back off."),
                fact(q, text="As D-120 says, retries back off; see v999."),
                fact(q, text="Decided in D-120; replaces v20."),
            ],
        },
    )
    assert "invented_ref" in res["E1-001#f1"]["grounding_flags"]
    assert sorted(res["E1-001#f1"]["invented"]["refs"]) == ["D-1", "abc1234f"]
    assert res["E1-001#f2"]["grounding_flags"] == ["invented_handle"]
    assert res["E1-001#f3"]["grounding_flags"] == []


def test_supersede_unit_checks_target_and_target_quote():
    pk = e1_packet()
    q = [{"source": "v10", "quote": "exponential backoff capped at 8 seconds"}]
    good = {
        "target": "v20",
        "target_quote": "a fixed delay of 2 seconds",
        "reason": "replaced",
        "evidence": q,
    }
    bad_target = {**good, "target": "v10"}
    bad_quote = {**good, "target_quote": "a fixed delay of 3 seconds"}
    out = {"abstain": False, "facts": [fact(q[0]["quote"], supersedes=[good, bad_target, bad_quote])]}
    res = checks(pk, out)
    assert res["E1-001#f1.s1"]["hiding"] is True and res["E1-001#f1.s1"]["grounded_det"] is True
    assert res["E1-001#f1.s2"]["grounding_flags"] == ["target_not_candidate"]
    assert res["E1-001#f1.s3"]["grounding_flags"] == ["target_quote_not_verbatim"]


def test_duplicate_must_be_a_candidate():
    pk = e1_packet()
    res = checks(
        pk,
        {"abstain": False, "facts": [fact("exponential backoff capped at 8 seconds", duplicate_of=["v5"])]},
    )
    assert res["E1-001#f1"]["grounding_flags"] == ["duplicate_not_candidate"]


def e2_packet():
    card = {"version_id": 5, "body": "Old card says the deploy is manual.", "valid_from": T0}
    notes = [{"version_id": 11, "title": "Session", "body": BODY, "valid_from": T1}]
    items = [
        {"version_id": 20, "kind": "fact", "title": "Retry", "body": OLD, "valid_from": T0},
        {
            "version_id": 21,
            "kind": "fact",
            "title": "Retry 2",
            "body": OLD + " Same again.",
            "valid_from": T0,
        },
    ]
    return P.e2_packet(
        "E2-001", "alpha", card=card, card_stale=False, memory_map="(map)", notes=notes, items=items,
        note_chars=2500, item_chars=700,
    )  # fmt: skip


def test_e2_card_lines_merge_and_structure():
    pk = e2_packet()
    out = {
        "abstain": False,
        "card": {
            "now": [{"text": "Retries back off.", "evidence": [{"source": "v11", "quote": DECISION}]}],
            "decisions": [
                {
                    "text": "Deploys are manual.",
                    "evidence": [{"source": "v5", "quote": "the deploy is manual"}],
                }
            ],
            "open_unknown": [],
            "as_of": "2026-09-20",
        },
        "merges": [
            {
                "keep": "v20",
                "absorb": ["v21"],
                "reason": "same",
                "evidence": [{"source": "v20", "quote": OLD}],
            },
            {
                "keep": "v20",
                "absorb": ["v21"],
                "reason": "same",
                "evidence": [{"source": "v20", "quote": OLD}, {"source": "v21", "quote": OLD}],
            },
        ],
    }
    res = checks(pk, out)
    assert res["E2-001#c1"]["grounded_det"] is True
    assert res["E2-001#c2"]["grounding_flags"] == ["cites_context"]  # the previous card is never a source
    assert res["E2-001#card"]["structure_flags"] == ["missing_open_section"]
    assert res["E2-001#m1"]["hiding"] is True and res["E2-001#m1"]["grounding_flags"] == [
        "merge_under_evidenced"
    ]
    assert res["E2-001#m2"]["grounded_det"] is True


def test_e2_card_over_budget():
    pk = e2_packet()
    lines = [{"text": "word " * 60, "evidence": [{"source": "v11", "quote": DECISION}]}] * 12
    out = {
        "abstain": False,
        "card": {"now": lines, "decisions": [], "open_unknown": ["x"], "as_of": "2026-09-20"},
    }
    res = checks(pk, out)
    assert "over_budget" in res["E2-001#card"]["structure_flags"]


def test_e3_requires_two_lessons_from_two_projects():
    rows = [
        {
            "version_id": 1,
            "kind": "lesson",
            "slug": "a",
            "title": "x",
            "body": "Always pin dependency versions in CI.",
            "valid_from": T0,
        },
        {
            "version_id": 2,
            "kind": "lesson",
            "slug": "a",
            "title": "y",
            "body": "Pin dependency versions before release.",
            "valid_from": T0,
        },
        {
            "version_id": 3,
            "kind": "lesson",
            "slug": "b",
            "title": "z",
            "body": "We now pin dependency versions everywhere.",
            "valid_from": T0,
        },
    ]
    pk = P.e3_packet("E3-001", rows, lesson_chars=1200)
    same_project = [
        {"source": "v1", "quote": "pin dependency versions in CI"},
        {"source": "v2", "quote": "Pin dependency versions before"},
    ]
    two_projects = [
        {"source": "v1", "quote": "pin dependency versions in CI"},
        {"source": "v3", "quote": "pin dependency versions everywhere"},
    ]
    exp = {"title": "Pin", "when": [], "avoid": [], "not_verified_for": [], "open_unknown": []}
    out = {
        "abstain": False,
        "experiences": [
            {**exp, "do": [{"text": "Pin versions.", "evidence": same_project}]},
            {**exp, "do": [{"text": "Pin versions.", "evidence": two_projects}]},
        ],
    }
    res = checks(pk, out)
    assert res["E3-001#x1"]["grounding_flags"] == ["under_evidenced"]
    assert res["E3-001#x2"]["grounded_det"] is True


def test_abstention_has_no_units():
    assert G.units_of("E1", "E1-001", {"abstain": True, "abstain_reason": "nothing", "facts": []}) == []
    assert G.units_of("E3", "E3-001", {"abstain": True, "experiences": []}) == []
