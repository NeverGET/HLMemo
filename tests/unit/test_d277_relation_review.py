"""D-277: the live relation review's own models and prompt versions, the opt-in policy-aligned
verifier (relate_verify/v3), the lesson-record guard and the 4000-character older side (review 129)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.unit.test_wf_task_fallbacks import PRIMARY, names, profiles_dir, settings  # noqa: F401

from hlmemo.db.librarian_queries import CandRow, SubjectRow
from hlmemo.librarian import guards
from hlmemo.librarian import profiles as pr
from hlmemo.librarian.budget import MemoryBudget
from hlmemo.librarian.candidates import Scored
from hlmemo.librarian.errors import JobCallCapExceeded, LlmConfigError, PrivacyDenied
from hlmemo.librarian.ledger import MemoryLedger
from hlmemo.librarian.profiles import describe_chains
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Clock, Provider
from hlmemo.librarian.tasks import write_review as wr
from hlmemo.ops.cli import chain_lines

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = datetime(2026, 11, 3, tzinfo=UTC)
NAMED = {
    "librarian_relate_profile": "wf-syn",
    "librarian_relate_verify_profile": "wf-risk",
    "librarian_relate_version": 3,
    "librarian_relate_verify_version": 3,
    "librarian_relate_verify_max_tokens": 2500,
}


def _w(s: Any) -> SimpleNamespace:
    return SimpleNamespace(
        provider=Provider(pr.profile_chain(s), mode="live", ledger=MemoryLedger()), settings=s
    )


def _rows(new_body: str = "the port is now 8080", old_body: str = "the port is 80") -> tuple[Any, Any]:
    subj = SubjectRow(10, 10, 1, [1], "all", "fact", "Port", new_body, [], False, 5, "stable", T1, True, T1)
    cand = CandRow(11, 11, 1, [1], "all", "fact", "Port", old_body, False, T0, T0)
    return subj, cand


async def _no_precheck(_ids: list[int]) -> Any:
    async def pc() -> None:
        return None

    return pc


# --------------------------------------------------------------------------- prompts
def test_the_policy_aligned_verifier_is_opt_in() -> None:
    assert load_task("relate_verify").prompt_version == "v2"  # the default stays byte for byte
    v3 = load_task("relate_verify", 3)
    assert v3.prompt_version == "v3" and v3.max_tokens == 2000
    assert "rule narrowed, tightened or replaced in part" in v3.system
    assert 'When unsure, answer "unclear".' in v3.system
    item = v3.schema["properties"]["results"]["items"]
    assert (
        item["properties"]
        == load_task("relate_verify", 2).schema["properties"]["results"]["items"]["properties"]
    )
    assert set(item["required"]) == {
        "id",
        "same_subject",
        "conflict",
        "current",
        "replaces_all",
        "adds_detail",
    }


def test_the_live_relate_prompt_has_room_for_the_longer_older_side() -> None:
    """The older side reaches the model with up to 4000 characters. The live relate/v3 has 12000
    output tokens; relate/v2 (now the shadow baseline) keeps 2000, so its recorded G-LIVE-B cassettes
    stay valid: review 129 M2 is watched through the shadow's relate_schema_fail (BACKLOG)."""
    assert wr.OLD_TEXT_CHARS == 4000
    assert load_task("relate", 3).max_tokens == 12000
    assert load_task("relate", 2).max_tokens == 2000


# --------------------------------------------------------------------------- live chains and versions
def test_unset_keeps_todays_chains_and_versions(profiles_dir: Path) -> None:  # noqa: F811
    w = _w(settings())
    assert wr.live_relate_chain(w) is None  # the provider's own chain for the task
    assert names(wr.live_verify_chain(w, PRIMARY)) == names(wr.verifier_chain(w, PRIMARY))
    assert wr.live_relate_task(w).prompt_version == "v2"
    assert wr.live_verify_task(w).prompt_version == "v2"
    assert wr.live_verify_task(w).max_tokens == load_task("relate_verify").max_tokens


def test_named_profiles_answer_first_with_one_fallback(profiles_dir: Path) -> None:  # noqa: F811
    w = _w(settings(**NAMED))
    assert names(wr.live_relate_chain(w)) == ["wf-syn", PRIMARY]  # D-019: a primary and one fallback
    # the relate answer came from wf-syn: the named verifier, then the D-067 cross chain's first
    assert names(wr.live_verify_chain(w, "wf-syn")) == ["wf-risk", PRIMARY]
    assert wr.live_relate_task(w).prompt_version == "v3"
    v = wr.live_verify_task(w)
    assert (v.prompt_version, v.max_tokens) == ("v3", 2500)


def test_a_named_profile_already_in_the_chain_is_not_repeated(profiles_dir: Path) -> None:  # noqa: F811
    w = _w(settings(librarian_relate_profile="wf-default", librarian_relate_verify_profile="wf-risk"))
    assert names(wr.live_relate_chain(w)) == ["wf-default", PRIMARY]
    w = _w(settings(librarian_relate_profile=PRIMARY))
    assert names(wr.live_relate_chain(w)) == [PRIMARY, "wf-default"]


def test_the_answering_profile_is_never_the_first_cross_verifier(profiles_dir: Path) -> None:  # noqa: F811
    """Review 129 M1: the named relate profile failed, the default primary answered, and that profile
    is also the named verifier: the cross verifier goes first, the named one is the last resort."""
    w = _w(settings(librarian_relate_profile="wf-syn", librarian_relate_verify_profile=PRIMARY))
    chain = names(wr.live_verify_chain(w, PRIMARY))
    assert chain[0] != PRIMARY and chain == ["wf-default", PRIMARY]
    # HLM_LIBRARIAN_VERIFIER=self: the named verifier does not apply (the answering profile verifies)
    s = settings(librarian_relate_verify_profile="wf-risk").model_copy(update={"librarian_verifier": "self"})
    assert names(wr.live_verify_chain(_w(s), PRIMARY)) == [PRIMARY]


def test_the_startup_check_names_the_variable(profiles_dir: Path) -> None:  # noqa: F811
    assert wr.check_relation_review(settings(**NAMED), today=date(2026, 11, 5)) == []
    with pytest.raises(LlmConfigError, match="HLM_LIBRARIAN_RELATE_PROFILE='nope'"):
        wr.check_relation_review(settings(librarian_relate_profile="nope"))
    with pytest.raises(LlmConfigError, match="HLM_LIBRARIAN_RELATE_VERIFY_VERSION=9"):
        wr.check_relation_review(settings(librarian_relate_verify_version=9))
    with pytest.raises(LlmConfigError, match="HLM_LIBRARIAN_SHADOW_VERIFY_VERSION=7"):
        wr.check_relation_review(settings(librarian_shadow_verify_version=7))
    with pytest.raises(LlmConfigError, match="HLM_LIBRARIAN_SHADOW_RELATE_VERSION=8"):
        wr.check_relation_review(settings(librarian_shadow_relate_version=8))


def test_the_startup_check_refuses_unpriced_disabled_and_broken_profiles(profiles_dir: Path) -> None:  # noqa: F811
    """Review 129 (S-M2, L1/L2): an unpriced named profile would refuse every live call and take the
    relation review down; a profile disabled for its task is not qualified; a broken file must refuse
    the start with the variable named, never a raw traceback."""
    (profiles_dir / "wf-unpriced.toml").write_text(
        'HLM_LLM_BASE_URL = "http://u.invalid/v1"\nHLM_LLM_MODEL = "stub/u"\nHLM_LLM_API_KEY = "k"\n'
    )
    with pytest.raises(
        LlmConfigError, match="HLM_LIBRARIAN_RELATE_PROFILE='wf-unpriced': the profile has no prices"
    ):
        wr.check_relation_review(settings(librarian_relate_profile="wf-unpriced"))
    wr.check_relation_review(settings(librarian_relate_profile="wf-unpriced", llm_budget_disabled=True))
    (profiles_dir / "wf-norelate.toml").write_text(
        (profiles_dir / "wf-syn.toml").read_text() + 'disabled_tasks = ["relate_verify"]\n'
    )
    with pytest.raises(LlmConfigError, match="RELATE_VERIFY_PROFILE='wf-norelate'.*disabled_tasks"):
        wr.check_relation_review(settings(librarian_relate_verify_profile="wf-norelate"))
    (profiles_dir / "wf-broken.toml").write_text("this is = = not toml\n")
    with pytest.raises(LlmConfigError, match="HLM_LIBRARIAN_RELATE_PROFILE='wf-broken'"):
        wr.check_relation_review(settings(librarian_relate_profile="wf-broken"))


def test_the_startup_check_warns_on_expiring_prices_and_self_verification(profiles_dir: Path) -> None:  # noqa: F811
    (profiles_dir / "wf-soon.toml").write_text(
        (profiles_dir / "wf-syn.toml").read_text() + 'price_valid_until = "2026-11-20"\n'
    )
    warnings = wr.check_relation_review(
        settings(librarian_relate_profile="wf-soon", librarian_relate_verify_profile="wf-soon"),
        today=date(2026, 11, 5),
    )
    assert any("prices expire on 2026-11-20" in x for x in warnings)
    assert any("equals HLM_LIBRARIAN_RELATE_PROFILE" in x for x in warnings)


def test_the_startup_check_warns_when_the_named_verifier_is_ignored(profiles_dir: Path) -> None:  # noqa: F811
    s = settings(librarian_relate_verify_profile="wf-risk").model_copy(update={"librarian_verifier": "self"})
    assert any("ignored under HLM_LIBRARIAN_VERIFIER=self" in x for x in wr.check_relation_review(s))


def test_verify_max_tokens_is_zero_or_at_least_256() -> None:
    with pytest.raises(ValueError, match="0 or at least 256"):
        settings(librarian_relate_verify_max_tokens=100)
    assert settings(librarian_relate_verify_max_tokens=0).librarian_relate_verify_max_tokens == 0


def test_ops_status_shows_the_relation_review(profiles_dir: Path) -> None:  # noqa: F811
    s = settings(librarian_relate_profile="wf-syn", librarian_relate_version=3)
    desc = describe_chains(s)
    wr.relation_review_chains(s, desc["tasks"])
    e = desc["tasks"]["relate"]
    assert (e["primary"], e["fallback"], e["prompt_version"]) == ("wf-syn", PRIMARY, "v3")
    assert desc["tasks"]["relate_verify"]["primary"] == PRIMARY  # not configured: unchanged
    line = next(x for x in chain_lines(desc) if " relate " in x)
    assert "primary=wf-syn fallback=wf-primary (relation review v3)" in line


def test_ops_status_never_raises_on_a_broken_profile(profiles_dir: Path) -> None:  # noqa: F811
    (profiles_dir / "wf-broken.toml").write_text("this is = = not toml\n")
    s = settings(librarian_relate_profile="wf-broken")
    desc = describe_chains(s)
    wr.relation_review_chains(s, desc["tasks"])
    assert (
        desc["tasks"]["relate"]["primary"] == "wf-broken" and desc["tasks"]["relate"]["primary_model"] is None
    )


def test_the_shadow_verifier_version_is_its_own_setting(profiles_dir: Path) -> None:  # noqa: F811
    s = settings(librarian_shadow_profile="wf-syn", librarian_shadow_verify_version=3)
    job = SimpleNamespace(lineage="l", job_id=1)

    async def go() -> Any:
        return wr.shadow_config(_w(s), job)

    sh = asyncio.run(go())
    assert sh.verify_task.prompt_version == "v3"
    assert sh.verify_task.max_tokens == s.librarian_shadow_verify_max_tokens


# --------------------------------------------------------------------------- the older side's cap
@pytest.mark.parametrize("n", [wr.OLD_TEXT_CHARS - 1, wr.OLD_TEXT_CHARS, wr.OLD_TEXT_CHARS + 1])
def test_payload_texts_and_shown_all_move_together(n: int) -> None:
    subj, cand = _rows(old_body="x" * n)
    payload = wr.relate_payload(subj, [(cand, False)])["existing"][0]["text"]
    text = wr.relate_texts(subj, [(cand, False)])[0]
    side = wr.verify_payload([(subj, cand, False)])[0]["A"]["text"]
    cut = n > wr.OLD_TEXT_CHARS
    assert payload == side and text.old_text == f"Port\n{payload}"
    assert payload.endswith(" …") is cut and text.old_shown_all is (not cut)


# --------------------------------------------------------------------------- lesson-record guard
LESSON = """## Mistake
Set the martingale cap above what the other hard limits already allowed
When: applying a hard cap to the martingale sequence. I set the hard cap to 25 while the per-bot cap was 15.

## Fix
Do: set a hard cap below the combination of the other hard limits.
Avoid: a cap the other limits already make unreachable.

## Context
Evidence: the trader config of 2026-11-03 showed dizi tavani 15 at the time. Status: active.
"""


def _pair(old_kind: str = "lesson", old_body: str = LESSON) -> guards.PairText:
    return guards.PairText(
        "v1",
        "new title\nThe sequence cap is now 25 with a default of 15.",
        f"old title\n{old_body}",
        T1,
        T0,
        new_kind="fact",
        old_kind=old_kind,
        new_body="The sequence cap is now 25 with a default of 15.",
        old_body=old_body,
    )


def _j(old_quote: str, supersedes: str = "new") -> guards.Judgement:
    return guards.Judgement(
        "v1",
        relation="contradicts",
        supersedes=supersedes,
        confidence="high",
        tier="question",
        new_quote="cap is now 25",
        old_quote=old_quote,
    )


def test_a_quote_in_the_lessons_record_is_not_a_supersession() -> None:
    counts: dict[str, int] = {}
    for quote in ("showed dizi tavani 15 at the time", "I set the hard cap to 25"):
        j = _j(quote)
        guards.lesson_record(j, _pair(), counts)
        assert (j.relation, j.supersedes, j.tier) == ("none", "none", "abstain")
        assert "lesson_record" in j.flags
    assert counts == {"lesson_record": 2}


def test_a_quote_in_the_lessons_rule_or_trigger_stays() -> None:
    """The rule is the title, the When: trigger (its first sentence, review 129 S-M3) and Fix."""
    for quote in (
        "set a hard cap below the combination",
        "Set the martingale cap above",
        "applying a hard cap to the martingale sequence",
    ):
        j = _j(quote)
        guards.lesson_record(j, _pair(), {})
        assert j.relation == "contradicts" and j.flags == []


def test_other_kinds_and_other_lesson_shapes_are_untouched() -> None:
    j = _j("showed dizi tavani 15 at the time")
    guards.lesson_record(j, _pair(old_kind="fact"), {})
    assert j.relation == "contradicts"
    j = _j("tavani 15 at the time")
    guards.lesson_record(j, _pair(old_body="A free-form lesson: tavani 15 at the time was wrong."), {})
    assert j.relation == "contradicts"  # no Fix section: never split
    j = _j("showed dizi tavani 15 at the time", supersedes="old")  # the NEW side is replaced: a fact
    guards.lesson_record(j, _pair(), {})
    assert j.relation == "contradicts"


def test_the_trigger_survives_abbreviations_and_wrapped_lines() -> None:
    """Review 129 round 2 NEW-1: "e.g." or a wrapped line does not end the trigger sentence."""
    for when, trigger, rest in (
        (
            "When: e.g. tuning the cap of the sequence. I set it to 25.",
            "tuning the cap of the sequence",
            "I set it to 25",
        ),
        (
            "When: tuning the cap\nof the martingale sequence. I set it to 25.",
            "of the martingale sequence",
            "I set it to 25",
        ),
        ("When: no full stop at all here", "no full stop at all here", None),
    ):
        rule, record = guards.lesson_parts(f"## Mistake\nT\n{when}\n\n## Fix\nDo: x\n")
        assert guards.quote_in(trigger, rule) and not guards.quote_in(trigger, record)
        if rest:
            assert guards.quote_in(rest, record) and not guards.quote_in(rest, rule)


def test_lesson_parts_splits_rule_and_record() -> None:
    rule, record = guards.lesson_parts(LESSON)
    assert "Set the martingale cap above" in rule and "Do: set a hard cap" in rule
    assert "When: applying a hard cap to the martingale sequence." in rule
    assert "I set the hard cap to 25" in record and "Evidence:" in record
    assert "Do:" not in record and "I set the hard cap" not in rule
    assert guards.lesson_parts("## Context\nonly context") is None
    for odd in ("", "## Fix\n", "## Mistake\n## Fix\n## Context\n", "When: x\n## Fix\nDo: y"):
        guards.lesson_parts(odd)  # never raises


# --------------------------------------------------------------------------- the live path, real Provider
class _NoSleep(Clock):
    async def sleep(self, seconds: float) -> None:
        return None


RELATE_ANSWER = {
    "id": "v11",
    "relation": "contradicts",
    "supersedes": "new",
    "scope": "part",
    "refiner": "none",
    "confidence": "high",
    "new_quote": "the port is now 8080",
    "old_quote": "the port is 80",
    "replaced_statements": [],
    "reason": "port changed",
}
VERIFY_ANSWER = {
    "id": "p1",
    "same_subject": True,
    "conflict": True,
    "current": "B",
    "replaces_all": False,
    "adds_detail": "none",
}


def _run(  # noqa: ANN202
    profiles_dir: Path,  # noqa: F811
    *,
    failing: tuple[str, ...] = (),
    cap: int = 20,
    deny: bool = False,
    answer: dict[str, Any] | None = None,
    cand_kind: str = "fact",
    old_body: str = "the port is 80",
):
    """The live _relate + _verify through a REAL Provider with a mock transport (review 129 T1 probe)."""
    s = settings(**NAMED)
    hits: list[tuple[str, int, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        system = body["messages"][0]["content"]
        task = "relate_verify" if 'JOB "relation_check"' in system else "relate"
        hits.append((body["model"], body["max_tokens"], task))
        if body["model"] in failing:
            return httpx.Response(500, json={"error": "down"})
        out = (
            {"results": [VERIFY_ANSWER]}
            if task == "relate_verify"
            else {"results": [answer or RELATE_ANSWER]}
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(out)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    budget, ledger = MemoryBudget(Decimal("3")), MemoryLedger()
    provider = Provider(
        pr.profile_chain(s),
        mode="live",
        budget=budget,
        ledger=ledger,
        transport=httpx.MockTransport(handler),
        clock=_NoSleep(),
        job_call_cap=cap,
    )
    w = SimpleNamespace(provider=provider, settings=s)
    subj, _ = _rows()
    cand = CandRow(11, 11, 1, [1], "all", cand_kind, "Port", old_body, False, T0, T0)
    pairs = [wr._Pair(subj, cand, False, Scored(11))]
    prechecks: list[list[int]] = []

    async def precheck_for(ids: list[int]) -> Any:
        async def pc() -> None:
            prechecks.append(list(ids))
            if deny:
                raise PrivacyDenied("E_PRIVACY_DENIED")

        return pc

    counters: dict[str, int] = {}

    async def go() -> None:
        rv = wr.WriteReview()
        await rv._relate(
            w,
            SimpleNamespace(job_id=1, lineage="lin-1"),
            SimpleNamespace(calls=[]),
            pairs,
            [],
            precheck_for,
            counters,
        )
        await rv._verify(
            w,
            SimpleNamespace(job_id=1, lineage="lin-1"),
            SimpleNamespace(calls=[]),
            pairs,
            precheck_for,
            counters,
        )

    asyncio.run(go())
    return hits, prechecks, budget, ledger, pairs, counters


def test_named_profiles_keep_every_gate(profiles_dir: Path) -> None:  # noqa: F811
    hits, prechecks, budget, ledger, pairs, _ = _run(profiles_dir)
    assert hits[0] == ("stub/wf-syn", load_task("relate", 3).max_tokens, "relate")
    assert hits[1] == ("stub/wf-risk", 2500, "relate_verify")
    assert len(prechecks) == 2  # a precheck before every attempt
    assert budget.spent > 0 and budget.reserved == 0  # reserved, then settled
    assert ledger.claims["lin-1"] == 2  # the job's call cap lineage, per attempt
    assert pairs[0].judgement is not None and pairs[0].judgement.verification["agreed"] is True


def test_a_failing_named_head_falls_back_once_with_every_gate(profiles_dir: Path) -> None:  # noqa: F811
    """T9: the default primary answers relate/v3 and is verified like any other answer; the chain is
    two deep, so a failing head costs one fallback's attempts (review 129 S-L5)."""
    hits, prechecks, _budget, ledger, pairs, _ = _run(profiles_dir, failing=("stub/wf-syn",))
    relate = [m for m, _, t in hits if t == "relate"]
    assert set(relate[:-1]) == {"stub/wf-syn"} and relate[-1] == f"stub/{PRIMARY}"
    assert {m for m, _, _ in hits} <= {"stub/wf-syn", f"stub/{PRIMARY}", "stub/wf-risk"}  # never wf-default
    assert hits[-1][0] == "stub/wf-risk"  # verified by the named verifier
    assert len(prechecks) == len(hits) == ledger.claims["lin-1"]
    assert pairs[0].judgement is not None and pairs[0].judgement.verification["agreed"] is True


def test_the_call_cap_binds_and_a_privacy_denial_sends_nothing(profiles_dir: Path) -> None:  # noqa: F811
    with pytest.raises(JobCallCapExceeded):
        _run(profiles_dir, failing=("stub/wf-syn",), cap=3)
    hits, _, budget, ledger, _, counters = _run(profiles_dir, deny=True)
    assert hits == [] and budget.reserved == 0 and ledger.claims.get("lin-1", 0) == 0
    assert counters.get("relate_denied") == 1


def test_a_dropped_lesson_record_spends_no_verifier_call(profiles_dir: Path) -> None:  # noqa: F811
    """Through the REAL provider path (review 129 round 2 NEW-3): the replaced side is a lesson and the
    model quotes its record, so the guard drops the judgement inside _relate and no relate_verify call
    is made; quoting the lesson's rule keeps it and the verifier runs."""
    lesson = (
        "## Mistake\nPort rule\nWhen: setting the port. I set the port is 80 by mistake.\n\n"
        "## Fix\nDo: use 8080.\n"
    )
    hits, _, _, _, pairs, counters = _run(
        profiles_dir,
        cand_kind="lesson",
        old_body=lesson,
        answer={**RELATE_ANSWER, "old_quote": "I set the port is 80 by mistake"},
    )
    assert [t for _, _, t in hits] == ["relate"]  # no relate_verify call
    assert counters.get("lesson_record") == 1
    assert pairs[0].judgement is not None and pairs[0].judgement.relation == "none"
    hits, _, _, _, pairs, counters = _run(
        profiles_dir, cand_kind="lesson", old_body=lesson, answer={**RELATE_ANSWER, "old_quote": "use 8080"}
    )
    assert [t for _, _, t in hits] == ["relate", "relate_verify"] and "lesson_record" not in counters
