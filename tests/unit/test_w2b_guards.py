"""D-067 guards, W2b candidate fusion/drop rule, D-057 tie rule and W2c notice text (pure units)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hlmemo.core.supersession import newer_first_on_ties
from hlmemo.librarian import candidates as c
from hlmemo.librarian import guards as g
from hlmemo.librarian.questions import notice_text, rule_text

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)


def _pair(
    cid: str = "v2", new: str = "Since June the TTL is 300 s.", old: str = "The TTL is 60 s."
) -> g.PairText:
    return g.PairText(cid, new, old, T2, T1)


def _res(**kw: object) -> dict:
    base = {
        "id": "v2",
        "relation": "contradicts",
        "supersedes": "new",
        "confidence": "high",
        "new_quote": "the TTL is 300 s",
        "old_quote": "TTL is 60 s",
        "reason": "changed",
    }
    return {**base, **kw}


def test_cited_ids_only_first_answer_wins_missing_is_abstention() -> None:
    out = {"results": [_res(id="v99"), _res(), _res(relation="none"), "junk"]}
    (j,), counts = g.check_relations(out, [_pair()])
    assert counts["uncited"] == 1 and counts["duplicate_id"] == 1
    assert (j.relation, j.supersedes, j.tier) == ("contradicts", "new", "action")
    (j,), counts = g.check_relations({"results": []}, [_pair()])
    assert counts["missing"] == 1 and not j.raised and j.flags == ["missing"]


def test_quote_evidence_caps_below_action() -> None:
    (j,), counts = g.check_relations({"results": [_res(new_quote="TTL is 999 s")]}, [_pair()])
    assert counts["quote_unverified"] == 1 and j.tier == "question" and j.raised
    assert g.quote_in("  «The  ttl IS 60 s.» ", "The TTL is 60 s.")  # case/space/punct-insensitive
    assert not g.quote_in("60", "The TTL is 60 s.")  # one word is not evidence


def test_on_equal_time_only_the_new_item_may_replace() -> None:
    tie = g.PairText("v2", "The TTL is 300 s.", "The TTL is 60 s.", T1, T1)
    (j,), counts = g.check_relations({"results": [_res(supersedes="old", new_quote="TTL is 300 s")]}, [tie])
    assert counts["supersedes_against_time"] == 1 and j.supersedes == "none"
    (j,), _ = g.check_relations({"results": [_res(new_quote="TTL is 300 s")]}, [tie])
    assert j.supersedes == "new"


def test_supersession_must_follow_time_and_needs_a_contradiction() -> None:
    (j,), counts = g.check_relations({"results": [_res(supersedes="old")]}, [_pair()])
    assert counts["supersedes_against_time"] == 1 and j.supersedes == "none" and j.relation == "contradicts"
    (j,), counts = g.check_relations({"results": [_res(relation="refines", supersedes="new")]}, [_pair()])
    assert counts["supersedes_without_contradiction"] == 1 and j.supersedes == "none"


def test_calibrated_tiers_low_confidence_is_never_an_action() -> None:
    assert all(tier != "action" for (rel, conf), tier in g.TIERS.items() if conf != "high")
    (j,), counts = g.check_relations(
        {"results": [_res(relation="duplicate", supersedes="none", confidence="low")]}, [_pair()]
    )
    assert counts["abstained_low"] == 1 and not j.raised
    (j,), _ = g.check_relations({"results": [_res(confidence="low")]}, [_pair()])
    assert j.raised and j.tier == "question"  # a possible conflict is always worth a question


def test_verifier_agreement_rules() -> None:
    agree = {"same_subject": True, "conflict": True, "current": "B"}
    assert g.verifier_agrees("supersede", agree, new_is_b=True, direction="new")
    assert not g.verifier_agrees("supersede", {**agree, "current": "A"}, new_is_b=True, direction="new")
    assert g.verifier_agrees("supersede", {**agree, "current": "A"}, new_is_b=False, direction="new")
    assert g.verifier_agrees("supersede", {**agree, "current": "A"}, new_is_b=True, direction="old")
    assert not g.verifier_agrees("supersede", {**agree, "conflict": False}, new_is_b=True)
    assert g.verifier_agrees(
        "widen", {"same_subject": True, "conflict": False, "current": "both"}, new_is_b=True
    )
    assert not g.verifier_agrees(
        "widen", {"same_subject": False, "conflict": False, "current": "both"}, new_is_b=True
    )


def test_verification_downgrades_or_drops() -> None:
    (j,), _ = g.check_relations({"results": [_res()]}, [_pair()])
    g.apply_verification(
        j, "supersede", {"same_subject": True, "conflict": True, "current": "unclear"}, new_is_b=True
    )
    assert (j.relation, j.supersedes, j.tier) == ("contradicts", "none", "question")
    assert "verifier_direction_disputed" in j.flags
    (j,), _ = g.check_relations({"results": [_res()]}, [_pair()])
    g.apply_verification(
        j, "supersede", {"same_subject": False, "conflict": False, "current": "both"}, new_is_b=True
    )
    assert not j.raised and "verifier_rejected" in j.flags
    (j,), _ = g.check_relations({"results": [_res()]}, [_pair()])
    g.apply_verification(j, "supersede", None, new_is_b=True)  # no second opinion: not raised
    assert not j.raised and "verifier_no_answer" in j.flags
    same = _pair(new="The TTL is 60 s for every endpoint.", old="The TTL is 60 s for every endpoint!")
    dup = _res(relation="duplicate", supersedes="none", new_quote="TTL is 60 s", old_quote="TTL is 60 s")
    (j,), _ = g.check_relations({"results": [dup]}, [same])
    assert j.relation == "duplicate"  # near-identical text: a real duplicate
    assert g.high_impact(j, cross_project=True) == "widen" and g.high_impact(j, cross_project=False) is None
    g.apply_verification(j, "widen", None, new_is_b=True)
    assert not j.raised


def test_placement_guard_and_tags() -> None:
    out = {
        "items": [
            {
                "id": "v1",
                "importance": 7,
                "stability": "stable",
                "topic_hint": "deploy",
                "tags_add": ["Deploy", "deploy", "ok-tag", "has space", "x" * 40, "a", "b", "c", "d"],
            },
            {"id": "v9", "importance": 3, "stability": "volatile"},
            {"id": "v2", "importance": 11, "stability": "stable"},
        ]
    }
    got, counts = g.check_placement(out, ["v1", "v2"])
    assert set(got) == {"v1"} and counts == {"uncited": 1, "duplicate_id": 0, "missing": 1}
    assert g.clean_tags(got["v1"]["tags_add"], ["ok-tag"]) == ["deploy", "has-space", "a", "b", "c"]


def test_fusion_and_drop_rule() -> None:
    fused = c.fuse([(10, 0.91), (11, 0.70), (12, 0.79)], [(11, 3.0), (13, 1.0)])
    assert [f.version_id for f in fused] == [11, 10, 13, 12]
    subject = c.content_terms(["cache", "ttl", "300", "seconds", "the", "api"])
    cands = {10: {"cache", "seconds"}, 11: {"cache"}, 12: {"unrelated"}, 13: {"300", "other"}}
    c.mark_lexical_hits(subject, cands, fused)
    hits = {f.version_id: f.lexical_hit for f in fused}
    assert hits == {10: True, 11: False, 12: False, 13: True}  # 2 distinctive terms, or an identifier
    kept, dropped = c.select(fused, top=8)
    assert [f.version_id for f in kept] == [10, 13] and {f.version_id for f in dropped} == {11, 12}
    kept, _ = c.select(fused, top=1)
    assert [f.version_id for f in kept] == [10]


@dataclass
class _Row:
    title: str
    valid_from: datetime


@dataclass
class _Hit:
    score: float
    row: _Row | None
    name: str


def test_newer_first_only_on_exact_ties_with_the_same_title() -> None:
    hits = [
        _Hit(0.5, _Row("Cache TTL", T1), "old"),
        _Hit(0.5, _Row("cache  ttl", T2), "new"),
        _Hit(0.5, _Row("Other", T2), "other"),
        _Hit(0.4, _Row("Cache TTL", T2), "lower"),
    ]
    assert [h.name for h in newer_first_on_ties(hits)] == ["new", "old", "other", "lower"]
    untied = [_Hit(0.6, _Row("Cache TTL", T1), "old"), _Hit(0.5, _Row("Cache TTL", T2), "new")]
    assert [h.name for h in newer_first_on_ties(untied)] == ["old", "new"]


def test_notice_and_rule_text_are_templates() -> None:
    assert notice_text("contradiction", ["v5", "v2"], {"relation": "contradicts", "supersedes": "new"}) == (
        "contradiction: v5 vs v2; proposed: v5 supersedes v2"
    )
    assert notice_text("link", ["v5", "v2"], {"relation": "duplicate"}) == "duplicate: v5 ~ v2"
    assert notice_text("widen_scope", ["v5", "v2"], {"relation": "refines"}).startswith("widen_scope: v2")
    assert rule_text("link", {"relation": "duplicate"}, "reject", ["v5", "v2"]) == (
        "Owner rejected a link proposal (duplicate) for v5 / v2."
    )


def test_d269_a_replaced_episode_or_session_note_drops_the_supersession() -> None:
    for kind in ("episode", "session_note"):
        p = g.PairText("v2", "Since June the TTL is 300 s.", "The TTL is 60 s.", T2, T1, old_kind=kind)
        (j,), _ = g.check_relations({"results": [_res()]}, [p])
        counts: dict[str, int] = {}
        g.historical(j, p, counts)
        assert (j.relation, j.supersedes, j.tier) == ("none", "none", "abstain") and not j.raised
        assert j.flags[-1] == "historical_kind" and counts == {"historical_kind": 1}
    # the newer side may be the episode: only the REPLACED side's kind counts
    p = g.PairText("v2", "Since June the TTL is 300 s.", "The TTL is 60 s.", T2, T1, new_kind="episode")
    (j,), _ = g.check_relations({"results": [_res()]}, [p])
    g.historical(j, p)
    assert (j.relation, j.supersedes) == ("contradicts", "new")


def test_d269_a_replaced_decision_row_keeps_the_link_but_is_never_closed() -> None:
    old = "D-012 | 2026-01-01 | ACCEPTED | The TTL is 60 s."
    p = g.PairText("v2", "Since June the TTL is 300 s.", old, T2, T1)
    (j,), _ = g.check_relations({"results": [_res()]}, [p])
    counts: dict[str, int] = {}
    g.historical(j, p, counts)
    assert (j.relation, j.supersedes) == ("contradicts", "new") and counts == {"decision_record": 1}
    j.scope, j.verification = "whole", {"agreed": True, "answer": {"replaces_all": True}}
    g.finalize(j, p)
    assert j.close_ok is False
    # a refines/none judgement is untouched
    (j,), _ = g.check_relations({"results": [_res(relation="none", supersedes="none")]}, [p])
    g.historical(j, p)
    assert "decision_record" not in j.flags


def test_d269_supersedes_old_checks_the_new_side_and_other_relations_are_untouched() -> None:
    # supersedes "old": the candidate is newer, so the subject (new side) is the replaced one
    p = g.PairText("v2", "The TTL is 60 s.", "Since June the TTL is 300 s.", T1, T2, new_kind="session_note")
    j = g.Judgement("v2", relation="contradicts", supersedes="old", tier="action")
    g.historical(j, p)
    assert (j.relation, j.tier, j.flags) == ("none", "abstain", ["historical_kind"])
    for rel, sup in (("refines", "none"), ("contradicts", "none")):
        j = g.Judgement("v2", relation=rel, supersedes=sup, tier="action")
        g.historical(j, g.PairText("v2", "a b c", "d e f", T2, T1, old_kind="episode"))
        assert (j.relation, j.flags) == (rel, [])
