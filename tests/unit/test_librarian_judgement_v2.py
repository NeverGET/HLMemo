"""Judgement v2 (D-076 hold-out failures): fact-level supersession, strict duplicates, the refine
direction check, doc_chunk pairs, the strict action tier, the partial-supersession read rule, the
legacy-close guard and prompt pinning (pure units; the pipeline tests are integration)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hlmemo.core.supersession import demote_partially_superseded
from hlmemo.librarian import candidates as c
from hlmemo.librarian import guards as g
from hlmemo.librarian.prompts import load_task, parse_pins, pin_versions
from hlmemo.librarian.questions import notice_text
from hlmemo.librarian.tasks.apply_batch import legacy_close

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)
MULTI_OLD = (
    "The API cache TTL is 60 seconds.\n"
    "- The cache is stored in Redis 7 on the api host.\n"
    "- Cache keys are prefixed with the tenant id."
)
NEW_TTL = "Since June the API cache TTL is 300 seconds."


def _pair(new: str, old: str, **kw: object) -> g.PairText:
    return g.PairText("v2", new, old, kw.pop("new_valid", T2), kw.pop("old_valid", T1), **kw)  # type: ignore[arg-type]


def _res(**kw: object) -> dict:
    base = {
        "id": "v2",
        "relation": "contradicts",
        "supersedes": "new",
        "confidence": "high",
        "new_quote": "the API cache TTL is 300 seconds",
        "old_quote": "The API cache TTL is 60 seconds",
        "reason": "changed",
    }
    return {**base, **kw}


def _judge(res: dict, pair: g.PairText) -> tuple[g.Judgement, dict[str, int]]:
    (j,), counts = g.check_relations({"results": [res]}, [pair])
    return j, counts


AGREE = {"same_subject": True, "conflict": True, "current": "B", "replaces_all": True, "adds_detail": "none"}


# --------------------------------------------------------------------------- fact-level supersession
def test_statements_and_coverage() -> None:
    assert g.statements(MULTI_OLD) == [
        "The API cache TTL is 60 seconds.",
        "The cache is stored in Redis 7 on the api host.",
        "Cache keys are prefixed with the tenant id.",
    ]
    assert g.statements("ok") == ["ok"]  # a body without a 3-word claim is one claim
    one = ["The API cache TTL is 60 seconds"]
    assert not g.covers_all(MULTI_OLD, one)  # two statements are not covered
    every = [*one, "stored in Redis 7 on the api host", "Cache keys are prefixed with the tenant id"]
    assert g.covers_all(MULTI_OLD, every)
    assert not g.covers_all(MULTI_OLD, [*every[:2], "tenant id"])  # a 2-word fragment is not a claim
    assert not g.covers_all(MULTI_OLD, ["not in the text at all", *every[:2]])  # quotes must be verbatim


def test_v1_answers_and_partial_scope_never_close() -> None:
    pair = _pair(NEW_TTL, MULTI_OLD)
    j, _ = _judge(_res(), pair)  # a v1 answer has no scope: fact-level by default
    assert (j.relation, j.supersedes, j.scope) == ("contradicts", "new", "part")
    g.apply_verification(j, "supersede", AGREE, new_is_b=True)
    g.finalize(j, pair)
    assert not j.close_ok and j.replaced_quote == "The API cache TTL is 60 seconds"
    j, counts = _judge(_res(scope="whole", replaced_statements=["The API cache TTL is 60 seconds."]), pair)
    assert j.scope == "part" and "close_not_covered" in j.flags and counts["close_not_covered"] == 1


def test_whole_scope_needs_every_statement_the_full_text_and_the_verifier() -> None:
    every = [
        "The API cache TTL is 60 seconds.",
        "The cache is stored in Redis 7 on the api host.",
        "Cache keys are prefixed with the tenant id.",
    ]
    pair = _pair(NEW_TTL, MULTI_OLD)
    j, _ = _judge(_res(scope="whole", replaced_statements=every), pair)
    assert j.scope == "whole" and not j.single_statement
    g.apply_verification(j, "supersede", AGREE, new_is_b=True)
    g.finalize(j, pair)
    assert j.close_ok
    j, _ = _judge(_res(scope="whole", replaced_statements=every), pair)  # verifier: not all outdated
    g.apply_verification(j, "supersede", {**AGREE, "replaces_all": False}, new_is_b=True)
    g.finalize(j, pair)
    assert j.scope == "part" and not j.close_ok and "verifier_not_all" in j.flags
    j, _ = _judge(_res(scope="whole", replaced_statements=every), pair)  # v1 verifier: no replaces_all
    g.apply_verification(
        j, "supersede", {k: AGREE[k] for k in ("same_subject", "conflict", "current")}, new_is_b=True
    )
    g.finalize(j, pair)
    assert not j.close_ok
    cut = _pair(NEW_TTL, MULTI_OLD, old_shown_all=False)  # the model saw a truncated body
    j, counts = _judge(_res(scope="whole", replaced_statements=every), cut)
    assert j.scope == "part" and counts["close_truncated"] == 1
    single = _pair(NEW_TTL, "The API cache TTL is 60 seconds.")
    j, _ = _judge(_res(scope="whole"), single)  # one statement: the quote covers it
    g.apply_verification(j, "supersede", AGREE, new_is_b=True)
    g.finalize(j, single)
    assert j.close_ok and j.single_statement


# --------------------------------------------------------------------------- strict duplicates
def test_duplicates_need_near_identical_text() -> None:
    a = "Every push to main triggers GitHub Actions, which runs lint and the unit tests."
    b = "GitHub Actions runs lint and the unit tests on every push to main."
    assert g.jaccard(a, a + " ") == 1.0 and not g.near_identical(a, b)
    dup = _res(
        relation="duplicate",
        supersedes="none",
        new_quote="runs lint and the unit tests",
        old_quote="runs lint and the unit tests",
    )
    j, counts = _judge(dup, _pair(b, a))
    assert j.relation == "relates" and counts["dup_to_relates"] == 1 and "duplicate_not_identical" in j.flags
    j, _ = _judge(dup, _pair(a, a.replace("Every", "every")))  # a re-import / case change: a duplicate
    assert j.relation == "duplicate"
    more = "GitHub Actions runs lint and the unit tests on every push to main within 90 s on runner_v2."
    j, counts = _judge(dup, _pair(more, b))  # an added number/identifier: a refinement, new side
    assert (j.relation, j.refiner) == ("refines", "new") and counts["dup_to_refines"] == 1
    j, counts = _judge(dup, _pair("The TTL is 300 s.", "The TTL is 60 s."))  # different values
    assert not j.raised and counts["dup_dropped"] == 1


# --------------------------------------------------------------------------- refine direction
def test_refine_direction_flips_and_drops() -> None:
    general, specific = (
        "The database is backed up every night.",
        "The database is backed up every night at 03:30, kept 14 days.",
    )
    ref = _res(
        relation="refines",
        supersedes="none",
        new_quote="database is backed up",
        old_quote="database is backed up",
    )
    j, counts = _judge({**ref, "refiner": "new"}, _pair(general, specific))  # the OLD item is more specific
    assert j.refiner == "old" and counts["refine_flipped"] == 1 and j.tier == "question"
    j, counts = _judge({**ref, "refiner": "old"}, _pair(specific, general))
    assert j.refiner == "new" and counts["refine_flipped"] == 1
    j, counts = _judge({**ref, "refiner": "new"}, _pair(specific, general))
    assert j.refiner == "new" and "refine_flipped" not in counts and j.tier == "action"
    # specificity undecided (paraphrases): the newer one refines, but only as a question
    p1, p2 = "Backups run nightly for the database.", "The database gets a nightly backup run."
    j, counts = _judge(
        {**ref, "refiner": "old", "new_quote": "database", "old_quote": "database"}, _pair(p1, p2)
    )
    assert j.refiner == "new" and j.tier != "action" and "refine_direction_by_time" in j.flags
    j, counts = _judge(ref, _pair("The DB listens on 5432.", "The DB listens on 5433."))
    assert not j.raised and counts["refine_dropped"] == 1  # two different values: not a refinement
    tie = _pair(p1, p2, new_valid=T1, old_valid=T1, new_recorded=T2, old_recorded=T1)
    assert g.newer_side(tie) == "new"


# --------------------------------------------------------------------------- doc_chunk
def test_doc_chunk_pairs_raise_contradictions_only() -> None:
    pair = _pair(
        "The API cache TTL is 300 seconds.", "The API cache TTL is 300 seconds!", old_kind="doc_chunk"
    )
    j, counts = _judge(
        _res(
            relation="duplicate",
            supersedes="none",
            new_quote="cache TTL is 300",
            old_quote="cache TTL is 300",
        ),
        pair,
    )
    assert not j.raised and counts["doc_chunk_link_skipped"] == 1
    j, _ = _judge(_res(), _pair(NEW_TTL, "The API cache TTL is 60 seconds.", old_kind="doc_chunk"))
    assert j.raised and j.relation == "contradicts"
    now = datetime(2026, 9, 24, tzinfo=UTC)
    assert c.reviewable("fact", "t", "b", now, now) and not c.reviewable("session_note", "t", "b", now, now)
    assert not c.reviewable("doc_chunk", "Overview", "The system has three parts.", now, now)
    assert c.reviewable("doc_chunk", "Log", "2026-09-20: moved to Hostinger.", now, now)
    assert c.reviewable("doc_chunk", "Notes", "D-066 sets the default model.", now, now)
    assert c.reviewable("doc_chunk", "Karar", "Varsayılan model değişti, karar verildi.", now, now)
    assert c.reviewable("doc_chunk", "Overview", "The system has three parts.", now - timedelta(days=30), now)


# --------------------------------------------------------------------------- strict action tier
def test_action_tier_needs_verifier_quotes_and_same_kind() -> None:
    pair = _pair(NEW_TTL, "The API cache TTL is 60 seconds.")
    j, _ = _judge(_res(supersedes="none"), pair)
    assert j.tier == "action" and g.quotes_pin(j)
    assert g.verification_kind(j, cross_project=False, pair=pair) == "confirm"
    g.apply_verification(
        j, "confirm", {"same_subject": True, "conflict": True, "current": "B"}, new_is_b=True
    )
    g.finalize(j, pair)
    assert j.tier == "action"
    j, _ = _judge(_res(supersedes="none"), pair)
    g.apply_verification(
        j, "confirm", {"same_subject": True, "conflict": False, "current": "both"}, new_is_b=True
    )
    g.finalize(j, pair)
    assert j.tier == "question" and "verifier_not_confirmed" in j.flags and j.raised
    j, _ = _judge(_res(supersedes="none"), pair)
    g.finalize(j, pair)  # never verified
    assert j.tier == "question" and "action_unverified" in j.flags
    kinds = _pair(NEW_TTL, "The API cache TTL is 60 seconds.", old_kind="lesson")
    j, _ = _judge(_res(supersedes="none"), kinds)
    assert g.verification_kind(j, cross_project=False, pair=kinds) is None  # would never be an action
    g.finalize(j, kinds)
    assert j.tier == "question" and "action_kind" in j.flags
    titles = _res(supersedes="none", new_quote="API cache TTL", old_quote="API cache TTL")
    j, _ = _judge(titles, pair)  # quotes that do not pin the differing values
    assert not g.quotes_pin(j) and g.verification_kind(j, cross_project=False, pair=pair) is None


def test_confirm_for_duplicate_and_refines_uses_adds_detail() -> None:
    v = {"same_subject": True, "conflict": False, "current": "both"}
    assert g.verifier_agrees("confirm", {**v, "adds_detail": "none"}, new_is_b=True, relation="duplicate")
    assert not g.verifier_agrees("confirm", {**v, "adds_detail": "B"}, new_is_b=True, relation="duplicate")
    assert g.verifier_agrees(
        "confirm", {**v, "adds_detail": "B"}, new_is_b=True, relation="refines", refiner="new"
    )
    assert not g.verifier_agrees(
        "confirm", {**v, "adds_detail": "B"}, new_is_b=True, relation="refines", refiner="old"
    )
    assert g.verifier_agrees("confirm", v, new_is_b=True, relation="refines")  # v1 verifier: no detail field


# --------------------------------------------------------------------------- read side, notices, legacy
@dataclass
class _Row:
    text: str


@dataclass
class _Hit:
    logical_id: int
    score: float
    row: _Row | None = None


MULTI_CHUNK = (
    "The API cache TTL is 60 seconds.\nThe cache is stored in Redis 7 on the api host.\n"
    "Cache keys are prefixed with the tenant id."
)
SPAN = "The API cache TTL is 60 seconds"


def _terms(q: str) -> list[str]:
    from hlmemo.core.normalize import extract_terms

    return extract_terms(q)


def test_partial_demotion_only_when_the_query_matched_the_outdated_statement() -> None:
    from hlmemo.core.supersession import matched_in_span

    assert matched_in_span(MULTI_CHUNK, SPAN, set(_terms("API cache TTL seconds")))
    assert not matched_in_span(MULTI_CHUNK, SPAN, set(_terms("where is the cache stored, Redis host")))
    assert not matched_in_span("Keys carry the tenant id.", SPAN, set(_terms("cache TTL")))  # span elsewhere
    assert not matched_in_span(MULTI_CHUNK, SPAN, set(_terms("unrelated words")))  # span is a third
    assert matched_in_span("The API cache TTL is 60 seconds.", SPAN, set(_terms("unrelated words")))

    def hits() -> list[_Hit]:
        return [_Hit(1, 0.9, _Row(MULTI_CHUNK)), _Hit(2, 0.8, _Row("x")), _Hit(3, 0.7, _Row("y"))]

    link = [(3, 1, SPAN)]  # 3 replaced one statement of 1
    out = demote_partially_superseded(hits(), link, _terms("API cache TTL"))
    assert [h.logical_id for h in out] == [2, 3, 1] and out[2].score == 0.7
    out = demote_partially_superseded(hits(), link, _terms("Redis host of the cache"))
    assert [h.logical_id for h in out] == [1, 2, 3]  # a still-valid statement matched: no demotion
    fresh = hits()
    assert [
        h.logical_id for h in demote_partially_superseded(fresh, [(1, 3, SPAN)], _terms("cache TTL"))
    ] == [
        1,
        2,
        3,
    ]  # already below its superseder
    assert demote_partially_superseded(fresh, [(9, 1, SPAN)], _terms("cache TTL")) == fresh  # no hit 9


def test_partial_demotion_needs_the_evidence_inside_the_span() -> None:
    """Review 57: the same sentence is not enough — the query's evidence must lie INSIDE the
    quoted outdated clause, and a mixed match is not demoted."""
    from hlmemo.core.supersession import matched_in_span

    text = "API uses port 8080 and backups retain 30 days."
    span = "API uses port 8080"
    assert not matched_in_span(text, span, set(_terms("backups retain")))
    assert matched_in_span(text, span, set(_terms("API port")))
    assert not matched_in_span(text, span, set(_terms("API port backups")))  # mixed: ambiguous
    hits = [_Hit(1, 0.9, _Row(text)), _Hit(2, 0.8, _Row("API now uses port 8765."))]
    link = [(2, 1, span)]
    assert [h.logical_id for h in demote_partially_superseded(hits, link, _terms("backups retain"))] == [1, 2]
    assert [h.logical_id for h in demote_partially_superseded(hits, link, _terms("API port"))] == [2, 1]


def test_partial_demotion_resolves_chains_in_one_stable_order() -> None:
    a = "The API cache TTL is 60 seconds."
    b = "The API cache TTL is 120 seconds."
    hits = [
        _Hit(1, 0.9, _Row(a)),  # A (oldest value)
        _Hit(2, 0.85, _Row("unrelated note")),
        _Hit(3, 0.8, _Row(b)),  # B replaced A's TTL, C replaced B's
        _Hit(4, 0.7, _Row("The API cache TTL is 300 seconds.")),
    ]
    links = [(3, 1, "API cache TTL is 60 seconds"), (4, 3, "API cache TTL is 120 seconds")]
    out = demote_partially_superseded(hits, links, _terms("API cache TTL"))
    assert [h.logical_id for h in out] == [2, 4, 3, 1]  # C before B before A, the rest stable
    assert [h.score for h in out] == [0.85, 0.7, 0.7, 0.7]  # non-increasing
    cyc = [_Hit(1, 0.9, _Row(a)), _Hit(3, 0.8, _Row(b))]
    both = [(3, 1, "API cache TTL is 60 seconds"), (1, 3, "API cache TTL is 120 seconds")]
    assert [h.logical_id for h in demote_partially_superseded(cyc, both, _terms("cache TTL"))] == [1, 3]
    # review 57: a cycle never pushes its members below unrelated hits (the closing edge is ignored)
    unrelated = _Hit(9, 0.7, _Row("unrelated note"))
    three = [_Hit(1, 0.9, _Row(a)), _Hit(3, 0.8, _Row(b)), unrelated]
    out = demote_partially_superseded(three, both, _terms("cache TTL"))
    assert [h.logical_id for h in out] == [1, 3, 9]
    four = [_Hit(3, 0.95, _Row(b)), _Hit(1, 0.9, _Row(a)), _Hit(9, 0.7, _Row("unrelated note"))]
    out = demote_partially_superseded(four, both, _terms("cache TTL"))
    assert [h.logical_id for h in out] == [1, 3, 9]  # the first edge (1 before 3) stands, 9 stays last


def test_notice_and_legacy_close() -> None:
    prop = {"relation": "contradicts", "supersedes": "new", "scope": "part"}
    assert (
        notice_text("contradiction", ["v9", "v3"], prop)
        == "contradiction: v9 vs v3; proposed: v9 supersedes part of v3"
    )
    close = {"actions": [{"op": "link_insert"}, {"op": "version_close"}]}
    assert legacy_close(close) and not legacy_close({**close, "close_ok": True})
    assert not legacy_close({"actions": [{"op": "link_insert"}]})


def test_prompt_versions_can_be_pinned() -> None:
    assert parse_pins("relate=1, relate_verify=v1,bad") == {"relate": 1, "relate_verify": 1}
    assert load_task("relate").prompt_version == "v2" and load_task("relate").max_tokens == 2000
    pin_versions({"relate": 1})
    try:
        assert load_task("relate").prompt_version == "v1" and load_task("relate").max_tokens == 1400
        assert load_task("relate", 2).prompt_version == "v2"
    finally:
        pin_versions(None)
    assert load_task("relate_verify").schema["properties"]["results"]["items"]["properties"]["replaces_all"]
