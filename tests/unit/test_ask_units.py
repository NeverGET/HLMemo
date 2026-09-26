"""D-136 ``memory.ask`` units: the Memory Map builder (spread, handles, budget), the deterministic
quote check (TR numerals), the answer contract and the completeness-pass prompt assembly."""

from __future__ import annotations

import json

import pytest

from hlmemo.core import memory_map as mm
from hlmemo.core import research_service as rsv
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.tasks import research as rs

METER = Meter()


# --------------------------------------------------------------------------- map: spread
def test_spread_prefix_covers_the_whole_list() -> None:
    out = mm.spread(list(range(16)))
    assert sorted(out) == list(range(16))
    assert out[:4] == [0, 8, 4, 12]  # any prefix spans the whole document
    assert mm.spread([1, 2]) == [1, 2] and mm.spread([]) == []
    odd = mm.spread(list(range(11)))
    assert sorted(odd) == list(range(11)) and max(odd[:3]) >= 8


# --------------------------------------------------------------------------- map: entries
def test_structural_entries_headings_rows_git_and_fences() -> None:
    body = (
        "# Title of doc\n"
        "## Where we are\n"
        "text\n"
        "```\n## not a heading\n| D-999 | x | y |\n```\n"
        "| D-130 | 2026-09-26 | ACCEPTED (owner) | **Product definition** of the research librarian |\n"
        "### *Set* `HLM_RESEARCH_ENABLED`\n"
        "## 10:32 0123456789ab Fix the importer title\n"
    )
    starts = [(0, 0), (40, 1), (70, 2)]
    ents = mm.structural_entries("Title of doc · docs/x.md", body, starts)
    labels = [e.label for e in ents]
    assert "Where we are" in labels and "Fix the importer title" in labels
    assert not any("not a heading" in lab or "D-999" in lab for lab in labels)  # fenced code
    row = next(e for e in ents if e.label.startswith("D-130"))
    assert row.level == 2 and "Product definition" in row.label  # the first descriptive cell
    deep = next(e for e in ents if e.level == 3)
    assert "HLM_RESEARCH_ENABLED" in deep.label and "*" not in deep.label  # identifiers keep "_"
    assert all(e.label != "Title of doc" for e in ents)  # the H1 repeating the title is skipped
    assert ents[0].ordinal == 0 and row.ordinal == 2  # the chunk each entry starts in


def test_locate_source_title_forms_and_clusters() -> None:
    def item(
        title: str, path: str | None = None, system: str | None = "markdown", kind: str = "fact"
    ) -> mm.ViewItem:
        return mm.ViewItem(1, title, kind, system if path else None, path, 1)

    loc = mm.locate(
        item(
            "D-130 · ACCEPTED: Product definition · docs/decisions/DECISIONS.md",
            "docs/decisions/DECISIONS.md#D-130",
        )
    )
    assert (loc.group, loc.name, loc.key) == (
        "docs/decisions/",
        "DECISIONS.md",
        "markdown:docs/decisions/DECISIONS.md",
    )
    assert loc.part and loc.part.startswith("D-130")
    whole = mm.locate(item("STATUS · docs/status/STATUS.md", "docs/status/STATUS.md"))
    assert whole.part is None and whole.name == "STATUS.md"
    auto = mm.locate(item("Lesson · feedback.md", "feedback.md", system="automemory"))
    assert auto.group == "automemory/"
    eval_b = mm.locate(item("docs/specs/SPEC.md § Tool contracts"))
    assert (eval_b.group, eval_b.name, eval_b.part) == ("docs/specs/", "SPEC.md", "Tool contracts")
    git = mm.locate(item("git log 2026-09-22 § part 2"))
    assert (git.group, git.name, git.key) == ("git log/", "2026-09-22", "git:2026-09-22")
    note = mm.locate(item("Some free-form fact", kind="lesson"))
    assert (note.group, note.key) == ("(no source)/", "kind:lesson")


# --------------------------------------------------------------------------- map: build
def _items(n_files: int = 3) -> tuple[list[mm.ViewItem], dict[int, list[mm.Entry]]]:
    items, entries = [], {}
    vid = 1
    for d in range(4):  # a decision log split into rows
        items.append(
            mm.ViewItem(
                vid,
                f"D-00{d} · ACCEPTED: decision {d} · docs/decisions/DECISIONS.md",
                "fact",
                "markdown",
                f"docs/decisions/DECISIONS.md#D-00{d}",
                1,
            )
        )
        vid += 1
    big = vid  # one large document with many headings
    items.append(
        mm.ViewItem(
            big, "STATUS · docs/status/STATUS.md", "doc_chunk", "markdown", "docs/status/STATUS.md", 40
        )
    )
    entries[big] = [mm.Entry(2, i, f"Section number {i}") for i in range(40)]
    vid += 1
    for f in range(n_files):
        items.append(
            mm.ViewItem(
                vid, f"Note {f} · docs/notes/n{f}.md", "doc_chunk", "markdown", f"docs/notes/n{f}.md", 2
            )
        )
        vid += 1
    return items, entries


def test_build_map_lines_handles_and_drill_rule() -> None:
    items, entries = _items()
    m = mm.build_map(items, entries, {}, budget_tokens=6000, project="p")
    assert m.text.startswith("MEMORY MAP of project p:")
    assert "[docs/decisions/]" in m.text and "- DECISIONS.md (4 items)" in m.text
    assert "- STATUS.md v5(40)" in m.text and "- n0.md v6(2)" in m.text
    assert m.drillable("v5.3") and not m.drillable("v5")  # a 40-chunk item: chunk handles only
    assert m.drillable("v6")  # 2 chunks: the whole item may be drilled
    assert not m.drillable("v999.0") and not m.drillable("v1.7")  # never a handle not on the map
    assert m.sections == 40 + 4  # everything fits in 6k tokens
    assert set(m.gate_ids()) == {it.version_id for it in items}
    assert m.tokens == METER.count_text(m.text) <= 6000


def test_build_map_budget_spreads_entries_instead_of_cutting_the_head() -> None:
    items, entries = _items()
    m = mm.build_map(items, entries, {}, budget_tokens=320, project="p")
    assert m.tokens <= 320 + 10
    taken = sorted(int(h.split(".")[1]) for h, (vid, o) in m.handles.items() if vid == 5 and o is not None)
    assert 3 <= len(taken) < 40
    assert min(taken) < 10 and max(taken) > 30  # spread over the whole document, not its head


def test_build_map_mandatory_overflow_keeps_every_group() -> None:
    items = [
        mm.ViewItem(
            i + 1, f"F{i} · dir{i % 5}/file{i}.md", "doc_chunk", "markdown", f"dir{i % 5}/file{i}.md", 1
        )
        for i in range(400)
    ]
    m = mm.build_map(items, {}, {}, budget_tokens=900, project="p")
    assert m.files_omitted > 0 and "more sources" in m.text
    assert all(f"[dir{g}/]" in m.text for g in range(5))  # spread truncation keeps every group
    assert m.tokens <= 900
    assert set(m.version_ids) < {it.version_id for it in items}  # omitted files are not gated/sent


def test_build_map_summaries_are_bounded_and_gated() -> None:
    items, entries = _items()
    key = "markdown:docs/decisions/DECISIONS.md"
    s = {
        key: ([1, 2, 3, 4], "The append-only decision log of the project."),
        "markdown:docs/status/STATUS.md": ([5], "x " * 400),
    }
    m = mm.build_map(items, entries, s, budget_tokens=6000, project="p")
    assert "- DECISIONS.md (4 items) ~ The append-only decision log of the project." in m.text
    assert m.summaries == 2 and set(m.summary_members) == {1, 2, 3, 4, 5}
    status_line = next(ln for ln in m.text.splitlines() if ln.startswith("- STATUS.md"))
    assert len(status_line) < mm.SUMMARY_CHARS + 60  # clipped
    small = mm.build_map(items, entries, s, budget_tokens=400, project="p")
    assert small.tokens <= 410


def test_members_digest_is_order_free() -> None:
    assert mm.members_digest([3, 1, 2]) == mm.members_digest([1, 2, 3, 3]) != mm.members_digest([1, 2])


# --------------------------------------------------------------------------- quote check
def test_find_verbatim_normalises_and_returns_the_original_span() -> None:
    text = "The **retrieval** p95 target is now 1.2 s (it was 1,6 s in D-001), measured by “G4”."
    assert (
        rs.find_verbatim("the retrieval p95 target is now 1.2 s", text)
        == "The **retrieval** p95 target is now 1.2 s"
    )
    assert rs.find_verbatim('measured by "G4"', text) == "measured by “G4”"
    assert rs.find_verbatim("the   retrieval\np95  target", text) is not None  # whitespace
    assert rs.find_verbatim("p95", text) is None  # too short to be a quote
    assert rs.find_verbatim("the retrieval p95 target is now 2.2 s", text) is None


def test_quote_check_tr_decimal_comma_equals_point() -> None:
    tr = "Gecikme hedefi 1,6 s idi; şimdi 1,25 s."
    assert rs.find_verbatim("Gecikme hedefi 1.6 s idi", tr) == "Gecikme hedefi 1,6 s idi"
    assert rs.find_verbatim("şimdi 1.25 s", tr) == "şimdi 1,25 s"
    en = "The target was 1.6 s before D-004."
    assert rs.find_verbatim("The target was 1,6 s before", en) == "The target was 1.6 s before"
    # a thousands-style comma is NOT a decimal comma: 1,600 is not 1.600
    assert rs.find_verbatim("costs 1.600 tokens per call", "It costs 1,600 tokens per call.") is None
    assert rs.qnorm("1,6 s") == rs.qnorm("1.6 s") and rs.qnorm("1,600") != rs.qnorm("1.600")


def test_literal_support_uses_the_same_numeral_rule() -> None:
    hay = rs._lit_norm("Gecikme hedefi 1,6 s; D-004 kararı; `HLM_RESEARCH_ENABLED=true`")
    assert rs.literals_ok("The target was 1.6 s (D-004).", hay)
    assert rs.literals_ok("HLM_RESEARCH_ENABLED=true is set.", hay)
    assert not rs.literals_ok("The target was 1.7 s.", hay)
    assert not rs.literals_ok("It is 16 s.", hay)  # 16 is not a token of 1,6


def test_literal_support_tr_apostrophe_suffixes() -> None:
    """Turkish case suffixes after an apostrophe are not part of a literal (live smoke finding)."""
    assert rs.literals("Özetler bütçenin en fazla %40’ını kullanabilir.") == ["40"]
    assert rs.literals("D-130'da karar verildi; v12.3'ün içinde.") == ["D-130", "v12.3"]
    hay = rs._lit_norm("Summaries take at most 40 % of it. D-130 decided it; see v12.3.")
    assert rs.literals_ok("Özetler en fazla %40’ını kullanabilir; D-130'da karar verildi.", hay)
    assert not rs.literals_ok("Özetler en fazla %41’ini kullanabilir.", hay)


# --------------------------------------------------------------------------- answer contract
def _ex(handle: str, text: str, vid: int | None = None) -> rs.Excerpt:
    return rs.Excerpt(
        handle, vid or int(handle[1:].split(".")[0]), f"T {handle}", f"docs/{handle}.md", "2026-09-26", text
    )


SHOWN = {
    "v10.0": _ex(
        "v10.0",
        "D-004 | ACCEPTED | The retrieval p95 target is now 1.2 s (it was 1,6 s in D-001)."
        " The owner decided it after R3.",
    ),
    "v11.0": _ex(
        "v11.0",
        "D-001 | ACCEPTED | Use Postgres 17 with pgvector. The retrieval p95 target is 1,6 s on the VPS.",
    ),
    "v12.3": _ex("v12.3", "## Next steps\nMeasure the Production-Ready gate on the dev replica."),
}


def _claim(text: str, *support: tuple[str, str]) -> dict:
    return {"text": text, "support": [{"id": h, "quote": q} for h, q in support]}


def _answer(**kw) -> dict:  # noqa: ANN003
    base = {
        "status": "answered",
        "answer": "The p95 target is 1.2 s; it was 1.6 s before (D-001).",
        "claims": [
            _claim(
                "The retrieval p95 target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")
            ),
            _claim(
                "The target was 1,6 s in D-001 on the VPS.",
                ("v11.0", "The retrieval p95 target is 1.6 s on the VPS"),
                ("v11.0", "D-001 | ACCEPTED | Use Postgres 17"),
            ),
        ],
        "related": ["v12.3"],
        "confidence": "high",
    }
    base.update(kw)
    return base


def test_validate_answer_contract_quotes_and_sources() -> None:
    v = rs.validate_answer(_answer(), SHOWN)
    assert v.answered and v.confidence == "high" and set(v.primary) == {"v10.0", "v11.0"}
    assert (
        v.quote_of("v11.0") == "The retrieval p95 target is 1,6 s on the VPS"
    )  # the ORIGINAL span (TR comma)
    assert v.related == ["v12.3"] and v.dropped_sentences == 0
    for c in v.kept:
        assert 1 <= len(c.support) <= rs.MAX_SUPPORT
        for h, q in c.support:
            assert rs.find_verbatim(q, SHOWN[h].text) == q  # every returned quote is verbatim
    out = v.kept[1].out()
    assert out == {
        "text": "The target was 1,6 s in D-001 on the VPS.",
        "support": [
            {"handle": "v11.0", "quote": "The retrieval p95 target is 1,6 s on the VPS"},
            {"handle": "v11.0", "quote": "D-001 | ACCEPTED | Use Postgres 17"},
        ],
    }


def test_claim_needs_its_quotes_to_cover_every_literal() -> None:
    """Gate v1 finding (b): one quote covering only PART of a claim does not support it."""
    partial = _answer(
        claims=[
            # "D-001" is only in the second (missing) quote: the claim is not fully supported
            _claim(
                "The target was 1,6 s in D-001.", ("v11.0", "The retrieval p95 target is 1.6 s on the VPS")
            ),
            _claim(
                "The retrieval p95 target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")
            ),
        ],
        answer="The p95 target is now 1.2 s.",
    )
    v = rs.validate_answer(partial, SHOWN)
    assert [c.state for c in v.claims] == ["downgraded", "kept"]  # true to its excerpt, not to its quote
    assert v.primary == ["v10.0"] and "v11.0" in v.related


def test_primary_is_the_handles_supporting_most_claims() -> None:
    claims = [
        _claim("It is 1.2 s now.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
        _claim("The owner decided it after R3.", ("v10.0", "The owner decided it after R3")),
        _claim("Postgres 17 with pgvector is used.", ("v11.0", "Use Postgres 17 with pgvector")),
    ]
    v = rs.validate_answer(
        _answer(claims=claims, answer="1.2 s now; the owner decided it after R3; Postgres 17."), SHOWN
    )
    assert v.primary == ["v10.0", "v11.0"] and rs.rank_sources(v.claims) == ["v10.0", "v11.0"]


def test_validate_answer_never_cites_unseen_and_reattributes() -> None:
    obj = _answer(
        claims=[
            _claim(
                "The target is now 1.2 s.",
                ("v99.0", "The retrieval p95 target is now 1.2 s"),
                ("v77.1", "made up text here"),
            )
        ],
        related=["v77.1", "v12.3"],
        answer="The target is now 1.2 s.",
    )
    v = rs.validate_answer(obj, SHOWN)
    assert v.answered and v.primary == ["v10.0"]  # re-attributed to the excerpt that holds the quote
    handles = (
        set(v.primary)
        | set(v.related)
        | {h for c in v.claims for h, _q in c.support}
        | {h for c in v.claims for h in c.cited}
    )
    assert handles <= set(SHOWN)  # v99.0 / v77.1 were never shown: never cited


def test_validate_answer_downgrades_drops_and_grounds_the_answer() -> None:
    obj = _answer(
        answer="The target is now 1.2 s. The team also said 3.5 s at some point.",
        claims=[
            _claim("The target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
            _claim("Postgres 17 is the store.", ("v11.0", "Postgres 17 is the only database we use")),
            _claim("It was 3.5 s.", ("v10.0", "it was 3.5 s")),
        ],
        related=[],
    )
    v = rs.validate_answer(obj, SHOWN)
    assert [c.state for c in v.claims] == ["kept", "downgraded", "dropped"]
    assert v.primary == ["v10.0"] and "v11.0" in v.related  # no verified quote: only related
    # lowered once per failure class: a failed claim, then an ungrounded answer sentence
    assert v.confidence == "low"
    assert v.answer == "The target is now 1.2 s." and v.dropped_sentences == 1  # 3.5 s is in no claim


def test_answer_states_only_what_the_claims_state() -> None:
    """A number in the answer that only an UNQUOTED part of an excerpt states is dropped too."""
    obj = _answer(
        answer="The target is now 1.2 s. The owner decided it after R3.",
        claims=[_claim("The target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s"))],
    )
    v = rs.validate_answer(obj, SHOWN)
    assert v.answer == "The target is now 1.2 s." and v.dropped_sentences == 1


def test_validate_answer_guard_and_abstention() -> None:
    liar = _answer(claims=[_claim("It is 0.4 s.", ("v10.0", "the target is 0.4 s"))])
    v = rs.validate_answer(liar, SHOWN)
    assert not v.answered and v.guard and v.primary == [] and v.confidence == "low"
    ab = rs.validate_answer(
        {
            "status": "insufficient_evidence",
            "answer": "",
            "claims": [],
            "related": ["v12.3", "v5.0"],
            "confidence": "low",
        },
        SHOWN,
    )
    assert not ab.answered and not ab.guard and ab.related == ["v12.3"]
    assert not rs.validate_answer(None, SHOWN).answered


def test_validate_answer_caps_primary_related_and_support() -> None:
    shown = {
        f"v{i}.0": _ex(f"v{i}.0", f"Fact number {i} says the value is {i} units exactly.")
        for i in range(1, 12)
    }
    claims = [
        _claim(f"Value {i}.", *[(f"v{i}.0", f"Fact number {i} says the value is {i} units")] * 5)
        for i in range(1, 9)
    ]
    obj = {
        "status": "answered",
        "answer": "Values 1 to 8.",
        "claims": claims,
        "related": [f"v{i}.0" for i in range(9, 12)],
        "confidence": "high",
    }
    v = rs.validate_answer(obj, shown)
    assert len(v.primary) == rs.MAX_PRIMARY and len(v.related) <= rs.MAX_RELATED
    assert all(len(c.support) == 1 for c in v.kept)  # duplicate quotes collapse


# --------------------------------------------------------------------------- completeness + self-check
def test_completeness_prompt_assembly() -> None:
    excerpts = list(SHOWN.values())
    draft = rs.validate_answer(_answer(claims=_answer()["claims"][:1]), SHOWN)
    msg = rs.check_user("What is the p95 target and what was it?", draft.draft(), excerpts)
    first, _, rest = msg.partition("\n")
    assert first == "JOB: check" and rest.startswith("INPUT: ")
    payload = json.loads(rest.removeprefix("INPUT: "))
    assert payload["question"] == "What is the p95 target and what was it?"
    assert payload["draft"]["answer"] == draft.answer
    assert payload["draft"]["claims"] == [
        {
            "text": "The retrieval p95 target is now 1.2 s.",
            "support": [{"id": "v10.0", "quote": "The retrieval p95 target is now 1.2 s"}],
        }
    ]
    # every retrieved candidate fact is in the check prompt, exactly as the answer call saw it
    answer_payload = json.loads(rs.answer_user("q", excerpts).partition("\n")[2].removeprefix("INPUT: "))
    assert payload["excerpts"] == answer_payload["excerpts"] == [e.shown() for e in excerpts]
    system = load_task("research").system
    assert 'JOB "check"' in system and '"sub_asks"' in system and "never a fact without quotes" in system


def test_verify_prompt_carries_only_quotes() -> None:
    v = rs.validate_answer(_answer(), SHOWN)
    msg = rs.verify_user("q?", v.answer, v.kept)
    payload = json.loads(msg.partition("\n")[2].removeprefix("INPUT: "))
    assert msg.startswith("JOB: verify\n") and set(payload) == {"question", "answer", "claims"}
    assert payload["claims"][1] == {
        "i": 1,
        "text": "The target was 1,6 s in D-001 on the VPS.",
        "quotes": ["The retrieval p95 target is 1,6 s on the VPS", "D-001 | ACCEPTED | Use Postgres 17"],
    }
    assert "Next steps" not in msg  # no excerpt text beyond the quotes


def test_apply_verify_narrows_drops_and_rewrites() -> None:
    v = rs.validate_answer(_answer(), SHOWN)
    out = rs.apply_verify(
        v,
        {
            "verdicts": [
                {"i": 0, "entailed": "full"},
                {"i": 1, "entailed": "partial", "text": "The target was 1,6 s on the VPS."},
            ],
            "answer": "The target is 1.2 s now; it was 1.6 s on the VPS.",
        },
    )
    assert [c.text for c in out.kept] == [
        "The retrieval p95 target is now 1.2 s.",
        "The target was 1,6 s on the VPS.",
    ]
    assert out.answer == "The target is 1.2 s now; it was 1.6 s on the VPS." and out.confidence == "medium"
    bad_narrowing = rs.apply_verify(
        v, {"verdicts": [{"i": 1, "entailed": "partial", "text": "It was 9.9 s."}], "answer": ""}
    )
    assert len(bad_narrowing.kept) == 1  # a narrowing its quotes do not support is dropped
    none = rs.apply_verify(
        v, {"verdicts": [{"i": 0, "entailed": "none"}, {"i": 1, "entailed": "none"}], "answer": "x"}
    )
    assert not none.answered
    assert rs.apply_verify(v, None) is v and rs.apply_verify(v, {"answer": "x"}) is v


def test_merge_check_only_adds_verified_evidence() -> None:
    draft = rs.validate_answer(_answer(), SHOWN)
    checked = rs.validate_answer(
        {
            **_answer(
                claims=_answer()["claims"][:1], answer="The target is 1.2 s, measured on the dev replica."
            ),
            "missing": ["dev replica"],
            "sub_asks": [
                {"ask": "current target", "covered": True},
                {"ask": "earlier target", "covered": False},
            ],
        },
        SHOWN,
    )
    merged = rs.merge_check(draft, checked)
    quotes = {q for c in merged.kept for _h, q in c.support}
    assert "The retrieval p95 target is 1,6 s on the VPS" in quotes  # the draft's claim survives
    assert merged.missing == ["dev replica"] and merged.sub_asks[1] == {
        "ask": "earlier target",
        "covered": False,
    }
    failed = rs.validate_answer({"status": "insufficient_evidence", "answer": "", "claims": []}, SHOWN)
    assert rs.merge_check(draft, failed) is draft


def test_job_validator_and_parse_plan() -> None:
    assert rs.job_validator("plan")({"queries": []}) is None
    assert rs.job_validator("refine")({"sections": []}) == "queries missing"
    assert rs.job_validator("verify")({"verdicts": [], "answer": ""}) is None
    assert rs.job_validator("verify")({"answer": ""}) == "verdicts/answer missing"
    ans = rs.job_validator("answer")
    assert (
        ans({"status": "answered", "answer": "x", "claims": [], "confidence": "low"})
        == "answered without claims"
    )
    assert ans({"status": "insufficient_evidence", "answer": "", "claims": []}) is None
    q, s = rs.parse_plan(
        {
            "queries": ["What is X?", "x  latency", "x latency", "", "a", "b", "c"],
            "sections": ["v1.2", "bogus", "v0.1", "v3"],
        },
        "what is x?",
    )
    assert q == ["x latency", "a", "b", "c"] and s == ["v1.2", "v3"]


# --------------------------------------------------------------------------- service helpers
def test_rrf_and_collapse() -> None:
    lists = [[{"clue": "v1.0"}, {"clue": "v2.0"}], [{"clue": "v2.0"}, {"clue": "v3.4"}]]
    assert rsv.rrf(lists) == ["v2.0", "v1.0", "v3.4"]
    assert rsv.collapse(["v5.3", "v5.4", "v5.2", "v5.6", "v7", "v7.1", "bad", "v8.0"], 4) == [
        "v5.3",
        "v5.6",
        "v7",
        "v8.0",
    ]


def test_parse_request_validation() -> None:
    req = rsv.parse_request({"question": "  what   is x ", "project": "hlmemo"})
    assert (req.question, req.project, req.token_budget) == ("what is x", "hlmemo", rsv.DEFAULT_BUDGET)
    for bad, code in (
        ({"question": ""}, "E_INVALID_ARG"),
        ({"question": "x" * 2001}, "E_INVALID_ARG"),
        ({"question": "x", "project": "Bad Slug"}, "E_INVALID_ARG"),
        ({"question": "x", "extra": 1}, "E_INVALID_ARG"),
        ({"question": "x", "token_budget": 100}, "E_BUDGET_TOO_SMALL"),
    ):
        with pytest.raises(ToolError) as exc:
            rsv.parse_request(bad)
        assert exc.value.code == code


def test_pack_fits_the_token_budget_exactly() -> None:
    out = {
        "project": "p",
        "answer": "The answer. " * 20,
        "abstained": False,
        "confidence": "high",
        "claims": [
            {"text": f"Claim {i}.", "support": [{"handle": f"v{i}.0", "quote": "q " * 20}]} for i in range(4)
        ],
        "primary": [{"handle": f"v{i}.0", "path": f"docs/{i}.md", "quote": "q " * 30} for i in range(3)],
        "related": [{"handle": f"v{i}.1", "path": f"docs/r{i}.md"} for i in range(5)],
        "meta": {"queries": ["a question"] * 5, "calls": 3},
    }
    full = rsv._pack(METER, json.loads(json.dumps(out)), 4000)
    assert full["meta"]["queries"] == ["a question"] * 5 and len(full["related"]) == 5
    budget = full["budget"]["used"] - 60
    packed = rsv._pack(METER, json.loads(json.dumps(out)), budget)
    assert packed["budget"]["used"] == METER.count(packed) <= budget
    assert isinstance(packed["meta"]["queries"], int) and len(packed["related"]) < 5
    assert packed["answer"] == out["answer"] and len(packed["primary"]) >= 1  # the answer is never cut
    with pytest.raises(ToolError) as exc:
        rsv._pack(METER, {**json.loads(json.dumps(out)), "answer": "The answer is long. " * 80}, 256)
    assert exc.value.code == "E_BUDGET_TOO_SMALL" and exc.value.details["min"] > 256
