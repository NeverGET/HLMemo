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


def test_quote_matching_normalises_markup_unicode_and_dashes() -> None:
    """Addendum 2: NFC, dash and quote variants, list markers, table pipes, ** and backticks are
    not text; the RAW span is returned for display."""
    text = (
        "| D-7 | ACCEPTED | **Use** `pgvector` — never SQLite |\n- first item\n- second item\n"
        "Cafe\u0301 opens at noon daily"
    )
    assert rs.find_verbatim("D-7 ACCEPTED Use pgvector - never SQLite", text) == (
        "D-7 | ACCEPTED | **Use** `pgvector` — never SQLite"
    )
    assert rs.find_verbatim("first item second item", text) == "first item\n- second item"
    # NFC: the decomposed é matches
    assert rs.find_verbatim("Café opens at noon", text) == "Café opens at noon"


def test_quote_match_is_contiguous_no_word_is_skipped() -> None:
    """Review 79 T6: the quote check is a CONTIGUOUS normalised match (whitespace, markup, NFC,
    dashes and quote variants normalised) — a quote that drops or inserts a word never verifies."""
    text = "The retrieval p95 target is now, after the R3 release, 1.2 s on the VPS."
    assert rs.locate_quote("The retrieval p95 target is now 1.2 s on the VPS", text) is None  # skips words
    assert rs.locate_quote("the p95 target is now, after", text) is None  # "retrieval" skipped
    assert rs.locate_quote("p95 target is now, after the R3 release", text) == (
        "p95 target is now, after the R3 release"
    )
    assert not hasattr(rs, "find_in_order")
    gate = "The release gate is **not** enabled by default."
    assert rs.locate_quote("The release gate is enabled by default.", gate) is None
    assert rs.locate_quote("The release gate is not enabled by default.", gate) == gate


RELEASE_GATE = {
    "v20.0": rs.Excerpt(
        "v20.0",
        20,
        "Gates",
        "docs/gates.md",
        "2026-09-26",
        "## Gates\nThe release gate is **not** enabled by default. It runs weekly.",
    )
}


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        # the exact review 79 T6 case: the claim drops "not", the quote is the full verbatim sentence
        ("The release gate is enabled by default.", "The release gate is **not** enabled by default."),
        # the quote drops "not" (a word skipped): it never verifies; a re-quote from the line brings
        # the "not" back, and the claim without it still fails
        ("The release gate is enabled by default.", "The release gate is enabled by default."),
        # the claim inserts a negation the quote does not state
        ("The gate does not run weekly.", "It runs weekly."),
        # the claim drops the hedge of a modal
        ("The flag is enabled in production.", "The flag may be enabled in production."),
    ],
)
def test_a_claim_or_quote_without_the_sources_not_fails(claim: str, quote: str) -> None:
    shown = {
        **RELEASE_GATE,
        "v21.0": rs.Excerpt(
            "v21.0",
            21,
            "Flag",
            "docs/flag.md",
            "2026-09-26",
            "The flag may be enabled in production. It is reviewed monthly.",
        ),
    }
    hid = "v21.0" if "flag" in claim else "v20.0"
    v = rs.validate_answer(
        {
            "status": "answered",
            "answer": claim,
            "claims": [_claim(claim, (hid, quote))],
            "confidence": "high",
        },
        shown,
    )
    assert not v.answered and v.guard and v.primary == [], (claim, quote)
    assert all(c.state != "kept" for c in v.claims)


def test_polarity_keeps_true_claims_and_unrelated_negations() -> None:
    assert rs.polarity_ok(
        "The release gate is not enabled by default.", ["The release gate is **not** enabled by default."]
    )
    assert rs.polarity_ok("The release gate isn't enabled by default.", ["The release gate is not enabled."])
    # a negation in the quote about OTHER words does not bind the claim
    assert rs.polarity_ok(
        "SQLite was rejected.", ["SQLite was rejected because it has no concurrent writers."]
    )
    assert not rs.polarity_ok(
        "SQLite has concurrent writers.", ["SQLite was rejected because it has no concurrent writers."]
    )
    # TR / DE negation words
    assert not rs.polarity_ok(
        "Sürüm kapısı varsayılan olarak etkin.", ["Sürüm kapısı varsayılan olarak etkin değil."]
    )
    assert not rs.polarity_ok(
        "Das Gate ist standardmäßig aktiviert.", ["Das Gate ist standardmäßig nicht aktiviert."]
    )
    # an answer sentence that contradicts the kept claim's quote is dropped from the answer text
    v = rs.validate_answer(
        {
            "status": "answered",
            "answer": "The release gate is not enabled by default. So the release gate is enabled"
            " by default.",
            "claims": [
                _claim(
                    "The release gate is not enabled by default.",
                    ("v20.0", "The release gate is **not** enabled by default."),
                )
            ],
            "confidence": "high",
        },
        RELEASE_GATE,
    )
    assert (
        v.answered and v.answer == "The release gate is not enabled by default." and v.dropped_sentences == 1
    )


WEEKLY_GATE = {
    "v22.0": rs.Excerpt(
        "v22.0",
        22,
        "Gates",
        "docs/gates.md",
        "2026-09-26",
        "The release gate is not enabled by default. It runs weekly, not daily.",
    )
}


@pytest.mark.parametrize(
    "claim",
    [
        # review 80 (astra): an unrelated "not" elsewhere in the claim hid the dropped one
        "The release gate is enabled by default and runs weekly, not daily.",
        # review 80 (sol): a "not" about other words ("disabled") hid it too
        "The release gate is enabled by default and not disabled.",
    ],
)
def test_an_unrelated_negation_does_not_excuse_a_dropped_one(claim: str) -> None:
    quote = "The release gate is not enabled by default. It runs weekly, not daily."
    assert not rs.polarity_ok(claim, [quote])
    v = rs.validate_answer(
        {
            "status": "answered",
            "answer": claim,
            "claims": [_claim(claim, ("v22.0", quote))],
            "confidence": "high",
        },
        WEEKLY_GATE,
    )
    assert not v.answered and v.primary == [], claim
    assert all(c.state != "kept" for c in v.claims)


def test_per_proposition_polarity_keeps_true_claims() -> None:
    quote = "The release gate is not enabled by default. It runs weekly, not daily."
    for claim in (
        "The release gate is not enabled by default and runs weekly, not daily.",
        "By default the release gate isn't enabled.",
        "The release gate runs weekly, not daily.",
    ):
        assert rs.polarity_ok(claim, [quote]), claim
    # a "not" inserted about one proposition while the other keeps its own
    assert not rs.polarity_ok("The release gate is not enabled by default and does not run weekly.", [quote])
    # a window never crosses a sentence end: the second quote sentence does not bind the first's words
    assert rs.polarity_ok("The gate runs weekly.", ["The gate is not enabled. It runs weekly."])


def test_failed_quote_is_requoted_from_its_line_before_dropping() -> None:
    """Addendum 2: a claim whose quotes are all wrong is re-quoted from the cited item's line that
    holds its literals and most of its words; dropped only when no line qualifies; flags recorded."""
    obj = {
        "status": "answered",
        "answer": "Postgres 17 with pgvector is the only store.",
        "claims": [
            _claim("Postgres 17 with pgvector is the only store.", ("v11.0", "we use postgres seventeen")),
            _claim("The store is Oracle 9.", ("v11.0", "nothing like this")),
        ],
        "related": [],
        "confidence": "high",
    }
    v = rs.validate_answer(obj, SHOWN)
    assert [c.state for c in v.claims] == ["kept", "dropped"]
    assert (
        v.claims[0].support[0][0] == "v11.0" and "Use Postgres 17 with pgvector" in v.claims[0].support[0][1]
    )
    assert (v.requoted, v.dropped_claims, v.main_dropped) == (1, 1, False)
    lost_main = rs.validate_answer({**obj, "claims": list(reversed(obj["claims"]))}, SHOWN)
    assert lost_main.main_dropped is True  # the FIRST claim (the main fact) was not kept


def test_prompt_puts_the_main_fact_first() -> None:
    system = load_task("research").system
    assert "The FIRST claim is the direct answer to the question's MAIN ask" in system
    assert "sub_asks[0] is the question's MAIN ask" in system
    assert "(or the table row)" in system


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
    """Gate v1 finding (b): one quote covering only PART of a claim does not support it; a literal
    the quotes lack is re-quoted from the cited excerpt's own sentence (deterministic), and a claim
    whose literal no cited excerpt states is never kept."""
    partial = _answer(
        claims=[
            # "D-001" is not in the quote: its own sentence of v11.0 is added as a second quote
            _claim(
                "The target was 1,6 s in D-001.", ("v11.0", "The retrieval p95 target is 1.6 s on the VPS")
            ),
            # "D-009" is in no excerpt: never kept
            _claim(
                "The target was 1,6 s in D-009.", ("v11.0", "The retrieval p95 target is 1.6 s on the VPS")
            ),
            _claim(
                "The retrieval p95 target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")
            ),
        ],
        answer="The p95 target is now 1.2 s.",
    )
    v = rs.validate_answer(partial, SHOWN)
    assert [c.state for c in v.claims] == ["kept", "dropped", "kept"]
    assert v.claims[0].support == [
        ("v11.0", "The retrieval p95 target is 1,6 s on the VPS"),
        ("v11.0", "D-001 | ACCEPTED | Use Postgres 17 with pgvector."),
    ]
    for h, q in v.claims[0].support:
        assert rs.find_verbatim(q, SHOWN[h].text) == q  # re-quoted spans are verbatim too


def test_requote_respects_the_support_cap_and_long_sentences() -> None:
    long_text = (
        "Intro. " + " ".join(["filler words here"] * 80) + " the flag HLM_X_42 is set " + " tail" * 80 + "."
    )
    shown = {"v1.0": _ex("v1.0", long_text)}
    support = [("v1.0", "Intro.")] * 0 + [("v1.0", "filler words here filler words here")]
    out = rs.requote("HLM_X_42 is set.", support, ["v1.0"], shown)
    assert len(out) == 2 and "HLM_X_42" in out[1][1] and len(out[1][1]) <= rs.QUOTE_MAX_CHARS
    assert rs.find_verbatim(out[1][1], long_text) is not None
    full = [("v1.0", "a"), ("v1.0", "b"), ("v1.0", "c")]
    assert rs.requote("HLM_X_42 is set.", full, ["v1.0"], shown) == full  # the cap holds


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


def test_answer_states_only_what_the_cited_sources_state() -> None:
    """A sentence of the answer whose value is in no kept claim, quote or cited excerpt is dropped
    (addendum 3 #2c: values are checked against the cited excerpts, not only the quote strings)."""
    obj = _answer(
        answer="The target is now 1.2 s. The owner decided it after R3. The store is Postgres 17.",
        claims=[_claim("The target is now 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s"))],
    )
    v = rs.validate_answer(obj, SHOWN)
    # R3 is in the cited excerpt v10.0; Postgres 17 only in v11.0, which no kept claim cites
    assert v.answer == "The target is now 1.2 s. The owner decided it after R3." and v.dropped_sentences == 1


# --------------------------------------------------------------------------- addendum 3 (matcher)
def test_a3_markup_is_stripped_without_inserting_spaces() -> None:
    text = "**HLM_RESEARCH_ENABLED**: true by default; `hlm doctor`: checks the models."
    assert (
        rs.find_verbatim("HLM_RESEARCH_ENABLED: true by default", text)
        == "**HLM_RESEARCH_ENABLED**: true by default"
    )
    assert rs.find_verbatim("hlm doctor: checks the models", text) == "`hlm doctor`: checks the models"
    assert rs.qnorm("**X**:") == rs.qnorm("X:") == "x:"
    assert rs.find_verbatim("the D–116 decision — final", "The D-116 decision - final.") is not None  # dashes


def test_a3_suffix_tolerant_values() -> None:
    hay = rs._lit_norm("The target was 1.6 s. D-116 decided it; the v3 plan ships in R3 in 2026.")
    for claim in (
        "Hedef 1,6'dır.",  # apostrophe suffix on a number
        "D-116'da karar verildi.",  # on an identifier
        "The v3's plan.",  # English possessive
        "R3'te çıkacak.",
        "2026da çıkacak.",  # a suffix glued to a number
        "D-116da karar verildi.",  # glued to an identifier
        "D–116 decided it.",  # en dash
    ):
        assert rs.literals_ok(claim, hay), claim
    assert not rs.literals_ok("D-117'de karar verildi.", hay)


def test_a3_word_pairs_are_not_identifiers() -> None:
    assert rs.literals("It supports read/write and TR/EN questions.") == []
    assert rs.literals("See docs/USAGE.md and v12.3.") == ["docs/USAGE.md", "v12.3."] or rs.literals(
        "See docs/USAGE.md and v12.3."
    ) == ["docs/USAGE.md", "v12.3"]


def test_a3_value_checked_against_the_cited_excerpt() -> None:
    """The quote is a subject-less fragment; the claim's identifier is elsewhere in the SAME cited
    excerpt: the claim is kept (and the flag's own sentence is added as a second quote)."""
    shown = {
        "v5.0": _ex(
            "v5.0",
            "D-002 | ACCEPTED | **HLM_RESEARCH_ENABLED** turns the librarian on."
            " It defaults to true on the branch.",
        )
    }
    obj = {
        "status": "answered",
        "answer": "HLM_RESEARCH_ENABLED defaults to true on the branch.",
        "claims": [
            _claim(
                "HLM_RESEARCH_ENABLED defaults to true on the branch.",
                ("v5.0", "It defaults to true on the branch"),
            )
        ],
        "related": [],
        "confidence": "high",
    }
    v = rs.validate_answer(obj, shown)
    assert v.answered and v.kept and v.kept[0].support[0] == ("v5.0", "It defaults to true on the branch")
    assert any("HLM_RESEARCH_ENABLED" in q for _h, q in v.kept[0].support)  # self-contained after re-quote


def test_a3_value_reformatting() -> None:
    hay = rs._lit_norm("It costs $10 per month; 1.600 users; launched 26.09.2026; the ratio is 1,6.")
    for claim in (
        "It costs 10 USD.",
        "1600 users.",
        "1,600 users.",
        "Launched 2026-09-26.",
        "The ratio is 1.6.",
    ):
        assert rs.literals_ok(claim, hay), claim
    iso = rs._lit_norm("Released 2026-09-26.")
    assert rs.literals_ok("Released 26.09.2026.", iso) and rs.literals_ok("Released 26/09/2026.", iso)
    assert not rs.literals_ok("Released 27.09.2026.", iso)
    assert not rs.literals_ok("It costs 11 USD.", hay)


def test_a6_named_subjects_and_attribution() -> None:
    assert rs.named_subjects("D-004 says the target is 1.2 s in STATUS.md, per the G4 gate.") == [
        "D-004",
        "STATUS.md",
        "G4",
    ]
    assert rs.named_subjects("The target is 1.2 s. It was lower before.") == []  # sentence starts
    claim = rs.Claim(
        "D-004 sets the target to 1.2 s.", [("v10.0", "The retrieval p95 target is now 1.2 s")], "kept"
    )
    assert rs.unattributed(claim) == ["D-004"]  # the quote does not name it
    ok = rs.Claim(
        "D-004 sets the target to 1.2 s.",
        [("v10.0", "D-004 | ACCEPTED | The retrieval p95 target is now 1.2 s")],
        "kept",
    )
    assert rs.unattributed(ok) == []


def test_a6_copy_through_finds_the_values_the_claim_lacks() -> None:
    c = rs.Claim("The target was changed.", [("v10.0", "The retrieval p95 target is now 1.2 s")], "kept")
    assert rs.uncopied(c, "What is the p95 target?", main=True) == ["p95", "1.2"]
    full = rs.Claim("The retrieval p95 target is now 1.2 s.", c.support, "kept")
    assert rs.uncopied(full, "What is the p95 target?", main=True) == []
    v = rs.validate_answer(
        _answer(
            claims=[
                _claim(
                    "The target was changed by the owner.", ("v10.0", "The retrieval p95 target is now 1.2 s")
                ),
                _claim(
                    "The target was set by Cemal to 1.2 s.",
                    ("v10.0", "The retrieval p95 target is now 1.2 s"),
                ),
                # an id that IS in the cited excerpt is re-quoted, so it needs no fix
                _claim("D-004 made it 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
            ],
            answer="The target was changed.",
        ),
        SHOWN,
    )
    fixes = rs.claim_fixes(v, "What is the p95 target?")
    assert "copy into the claim" in fixes[0][0] and "1.2" in fixes[0][0]
    assert any("do not name: Cemal" in n for n in fixes[1])
    assert not any("do not name" in n for n in fixes.get(2, []))  # D-004 is quoted now
    assert any("D-004" in q for _h, q in v.kept[2].support)
    msg = rs.check_user("q", v.draft(), list(SHOWN.values()), fixes)
    payload = json.loads(msg.partition("\n")[2].removeprefix("INPUT: "))
    assert payload["draft"]["claims"][1]["fix"] == fixes[1]  # the repair rides on the check call
    assert "fix" not in payload["draft"]["claims"][2] if 2 not in fixes else True


def test_a6_repaired_claims_are_accepted_only_when_they_stay_grounded() -> None:
    """The completeness+repair output goes through the same validation: a copy-through repair is
    kept, a rewrite that adds a value no source states is dropped, and a claim still naming a
    subject that neither its quotes nor its sources state is dropped by the attribution check."""
    checked = rs.validate_answer(
        _answer(
            claims=[
                _claim(
                    "The retrieval p95 target is now 1.2 s.",
                    ("v10.0", "The retrieval p95 target is now 1.2 s"),
                ),
                _claim("The target is 1.2 s per D-777.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
                _claim(
                    "The target was set by Cemal to 1.2 s.",
                    ("v10.0", "The retrieval p95 target is now 1.2 s"),
                ),
            ],
            answer="The retrieval p95 target is now 1.2 s.",
        ),
        SHOWN,
    )
    assert [c.state for c in checked.claims][:2] == ["kept", "dropped"]  # D-777: in no source
    out = rs.enforce_attribution(checked, SHOWN)
    assert [c.text for c in out.kept] == ["The retrieval p95 target is now 1.2 s."]


def test_a6_enforce_attribution_without_a_self_check() -> None:
    v = rs.validate_answer(
        _answer(
            claims=[
                _claim("D-004 made the target 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
                _claim("D-999 made the target 1.2 s.", ("v10.0", "The retrieval p95 target is now 1.2 s")),
            ],
            answer="The target is now 1.2 s.",
        ),
        SHOWN,
    )
    out = rs.enforce_attribution(v, SHOWN)
    # D-004 is in the cited excerpt (only not in the quote): kept; D-999 is nowhere: dropped
    assert [c.text for c in out.kept] == ["D-004 made the target 1.2 s."]


def test_a3_prompt_asks_for_self_contained_quotes() -> None:
    system = load_task("research").system
    assert "self-contained" in system and "row's key" in system


def test_a4_prompt_asks_for_specifics_and_full_sentence_quotes() -> None:
    system = load_task("research").system
    assert "Never generalise a specific" in system and "Brevity is not a goal" in system
    assert "ONE fact per claim" in system and "the FULL sentence (or the table row)" in system
    assert "upgrade every general wording of the draft to the most specific form" in system


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


# --------------------------------------------------------------------------- review 79 T2
def test_prompt_values_are_redacted_before_json_serialisation() -> None:
    """Review 79 T2: ``password = "SuperSecret123456"`` survives a redactor run over the JSON text
    (the escaped quote hides the assignment); every builder redacts the values first."""
    from hlmemo.librarian.redact import Redactor
    from hlmemo.librarian.tasks import map_summary as ms

    secret = "SuperSecret123456"
    text = f'The staging password = "{secret}" is rotated monthly.'
    assert secret in Redactor().text(json.dumps({"q": text}))  # the reviewed failure mode
    ex = rs.Excerpt("v30.0", 30, text, "docs/c.md", "2026-09-26", text)
    q = f'Is password = "{secret}" valid?'
    draft = {"answer": text, "claims": [{"text": text, "support": [{"id": "v30.0", "quote": text}]}]}
    for msg in (
        rs.plan_user(q, "ctx", f"MEMORY MAP\n- {text}"),
        rs.answer_user(q, [ex]),
        rs.check_user(q, draft, [ex], {0: [text]}),
        rs.refine_user(q, [q], [ex], "MEMORY MAP"),
        ms.user_message("c.md", "markdown:docs/c.md", [{"title": text, "text": text}], 1),
    ):
        assert secret not in msg and "⟦REDACTED:assignment:" in msg
