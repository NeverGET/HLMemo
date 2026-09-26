"""D-184 (A): explicit supersession declarations -> link proposals (``core/explicit_supersession``).

Pure: marker parsing, target resolution (exact path / unique D-row), direction, scope, verbatim
quotes and the conservative drops (unresolvable, self, same document, negation, hypothetical,
question, code, citation, range, ambiguity, cycles)."""

from __future__ import annotations

import pytest

from hlmemo.core.explicit_supersession import QUOTE_MAX, Doc, find_declarations, propose

SPEC_HEADER = (
    "# HLMemo — Phase-0 Specification (authoritative)\n\n"
    "Status: MERGED 2026-09-22 from `docs/consults/03-claude-phase0-spec.md` (Claude Plan agent) and "
    "`docs/consults/03-codex-phase0-design.md` (codex/gpt-6-astra), both written before D-022/D-023. "
    "D-001..D-024 are binding; D-023 (device identity) is applied here directly.\n\n"
    "## 0. Scope\n\nThe MCP server exposes five tools and uses a low-level Server.\n"
)
SPEC_TAIL = "## 4. Retrieval algorithm (`memory.query`)\n\nThe guidance names the top 5 hits.\n"
DRAFT = "# HLMemo Phase-0 Implementation Spec (2026-09-22)\n\nTools are MCPServer decorators; drill 3 hits.\n"
CODEX = "1. **POSTGRES DDL**\n\nHalf-open intervals; infinity ends.\n"

ROW_36 = (
    "D-036 | 2026-09-22 | ACCEPTED | Bake-off verdict: gpt-6-astra = implementer; Claude (Opus 5.5) = "
    "orchestrator + adversarial reviewer + neutral judge."
)
ROW_48 = "D-048 | 2026-09-23 | ACCEPTED | E2E COMPLETE on production; gpt-6-astra is implementer again."
ROW_50 = (
    "D-050 | 2026-09-23 | ACCEPTED (owner) | Coworker model: codex `gpt-6-sol` replaces `gpt-6-astra`. "
    "Default implementers are now Claude subagents. Supersedes the implementer role in D-036/D-048. "
    "Invocation in CLAUDE.md."
)
LOG_HEAD = "# HLMemo — Decision Log (append-only)\n\nFormat: `D-NNN | date | status | decision`\n\n"


def doc(vid: int, path: str | None, body: str, lid: int | None = None) -> Doc:
    return Doc(version_id=vid, logical_id=lid or vid + 1000, path=path, body=body)


def pairs(props) -> set[tuple[str | None, str | None, str, str]]:  # noqa: ANN001
    return {(p.source_path, p.target_path, p.scope, p.marker) for p in props}


def assert_verbatim(props, docs: list[Doc]) -> None:  # noqa: ANN001
    bodies = [d.body for d in docs]
    for p in props:
        assert 0 < len(p.quote) <= QUOTE_MAX and any(p.quote in b for b in bodies), p.quote
        assert 0 < len(p.declaration) <= QUOTE_MAX and any(p.declaration in b for b in bodies)


# --------------------------------------------------------------------------- the D-184 cases
def test_phase0_spec_merged_from_draft_links_every_spec_item_to_both_drafts() -> None:
    docs = [
        doc(67, "docs/decisions/PHASE0-SPEC.md#0", SPEC_HEADER),
        doc(68, "docs/decisions/PHASE0-SPEC.md#1", SPEC_TAIL),
        doc(10, "docs/consults/03-claude-phase0-spec.md#0", DRAFT),
        doc(11, "docs/consults/03-codex-phase0-design.md#0", CODEX),
    ]
    out = propose(docs)
    assert pairs(out) == {
        (
            "docs/decisions/PHASE0-SPEC.md#0",
            "docs/consults/03-claude-phase0-spec.md#0",
            "whole",
            "merged_from",
        ),
        (
            "docs/decisions/PHASE0-SPEC.md#0",
            "docs/consults/03-codex-phase0-design.md#0",
            "whole",
            "merged_from",
        ),
        (
            "docs/decisions/PHASE0-SPEC.md#1",
            "docs/consults/03-claude-phase0-spec.md#0",
            "whole",
            "merged_from",
        ),
        (
            "docs/decisions/PHASE0-SPEC.md#1",
            "docs/consults/03-codex-phase0-design.md#0",
            "whole",
            "merged_from",
        ),
    }
    assert {p.declared_in for p in out} == {67}
    assert all(p.quote.startswith("Status: MERGED 2026-09-22 from") for p in out)
    assert all(
        p.props() == {"by": "explicit", "scope": "whole", "quote": p.quote, "marker": "merged_from"}
        for p in out
    )
    assert_verbatim(out, docs)


def test_decisions_d050_supersedes_d036_is_a_part_link_quoting_the_row() -> None:
    """The log split into two items: D-050 (in #1) supersedes the rows D-036 and D-048 (in #0)."""
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", ROW_50 + "\nD-051 | 2026-09-23 | ACCEPTED | GOAL: phases.\n")
    out = propose([d0, d1])
    assert len(out) == 1  # one link per item pair (the links exclusion): the first target row
    (p,) = out
    assert (p.source_version_id, p.target_version_id, p.scope, p.marker) == (2, 1, "part", "supersedes")
    assert (p.source_ref, p.target_ref) == ("D-050", "D-036")
    assert p.quote == ROW_36 and p.quote in d0.body  # the superseded row: the D-057 rule-3 span
    assert "Supersedes the implementer role in D-036/D-048" in p.declaration and p.declaration in d1.body
    assert p.props()["declaration"] == p.declaration


def test_decisions_rows_in_one_item_are_self_links_and_dropped() -> None:
    log = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n" + ROW_50 + "\n")
    decls = find_declarations(log.body)
    assert [(d.marker, d.unit_row, [t.value for t in d.targets]) for d in decls] == [
        ("supersedes", "D-050", ["D-036", "D-048"])
    ]
    assert propose([log]) == []


def test_explicit_subject_in_prose_d050_supersedes_d036() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", "D-050 | x | ACCEPTED | sol.\nD-052 | y | ACCEPTED | z.\n")
    status = doc(
        3, "docs/status/STATUS.md", "# Status\n\n- D-050 supersedes D-036 (Sol reviews, Claude implements).\n"
    )
    (p,) = propose([d0, d1, status])
    assert (p.source_version_id, p.target_version_id, p.scope, p.marker) == (2, 1, "part", "supersedes")
    assert p.declared_in == 3 and p.quote == ROW_36


@pytest.mark.parametrize(
    "body",
    [
        "Status: supersedes `docs/consults/99-missing-draft.md`.\n",  # no such item
        "Status: supersedes D-999.\n",  # no such decision row
        "Status: supersedes the old draft (the Claude one).\n",  # no ref at all
        "Status: merged from your round-3 design and an independent Claude spec.\n",
    ],
)
def test_unresolvable_target_is_dropped(body: str) -> None:
    docs = [doc(1, "docs/decisions/NEW.md", body), doc(2, "docs/consults/03-claude-phase0-spec.md", DRAFT)]
    assert propose(docs) == []


def test_reverse_superseded_by_path_the_declaring_draft_is_the_superseded_side() -> None:
    draft = doc(
        10,
        "docs/consults/03-claude-phase0-spec.md#0",
        "Status: superseded by `docs/decisions/PHASE0-SPEC.md`.\n" + DRAFT,
    )
    spec0 = doc(67, "docs/decisions/PHASE0-SPEC.md#0", "# Spec\n\nThe authoritative spec.\n")
    spec1 = doc(68, "docs/decisions/PHASE0-SPEC.md#1", SPEC_TAIL)
    out = propose([draft, spec0, spec1])
    assert pairs(out) == {
        (
            "docs/decisions/PHASE0-SPEC.md#0",
            "docs/consults/03-claude-phase0-spec.md#0",
            "whole",
            "superseded_by",
        ),
        (
            "docs/decisions/PHASE0-SPEC.md#1",
            "docs/consults/03-claude-phase0-spec.md#0",
            "whole",
            "superseded_by",
        ),
    }
    assert {p.declared_in for p in out} == {10}
    assert_verbatim(out, [draft])


def test_reverse_superseded_by_in_a_decision_row() -> None:
    row_40 = "D-040 | 2026-09-22 | SUPERSEDED by D-042 | Embeddings move to a cloud API."
    row_41 = "D-041 | 2026-09-22 | ACCEPTED | Something else."
    row_42 = "D-042 | 2026-09-23 | ACCEPTED | Launch with the LOCAL e5-small embedder."
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + row_40 + "\n" + row_41 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", row_42 + "\nD-043 | x | ACCEPTED | y.\n")
    (p,) = propose([d0, d1])
    assert (p.source_version_id, p.target_version_id, p.scope, p.marker) == (2, 1, "part", "superseded_by")
    assert (p.source_ref, p.target_ref) == ("D-042", "D-040") and p.quote == row_40


# --------------------------------------------------------------------------- precision guards
@pytest.mark.parametrize(
    "line",
    [
        "Status: this spec does not supersede `docs/consults/03-claude-phase0-spec.md`.",
        "Status: it would replace `docs/consults/03-claude-phase0-spec.md` once accepted.",
        "Status: supersedes `docs/consults/03-claude-phase0-spec.md`?",
        "The librarian proposes `supersedes` links (`docs/consults/03-claude-phase0-spec.md`).",
        "Read docs/decisions/NEW.md (authoritative, merged from `docs/consults/03-claude-phase0-spec.md`).",
        "We now use NEW instead of `docs/consults/03-claude-phase0-spec.md`.",  # instead_of: rows only
        "```\nStatus: supersedes `docs/consults/03-claude-phase0-spec.md`\n```",
        "Status: supersedes link to `docs/consults/03-claude-phase0-spec.md`.",
        'Status: X says: "MERGED 2026-09-22 from `docs/consults/03-claude-phase0-spec.md`".',  # a quotation
        "Status: X declares „supersedes `docs/consults/03-claude-phase0-spec.md`“ in its header.",
    ],
)
def test_no_link_from_negated_hypothetical_question_code_or_non_self_position(line: str) -> None:
    docs = [
        doc(1, "docs/decisions/NEW.md", "# New\n\n" + line + "\n"),
        doc(2, "docs/consults/03-claude-phase0-spec.md", DRAFT),
    ]
    assert propose(docs) == []


def test_a_decision_row_quoting_another_documents_declaration_declares_nothing() -> None:
    """D-184's own row quotes PHASE0-SPEC's header; the quotation is not D-184's declaration."""
    row = (
        "D-184 | 2026-09-26 | ACCEPTED | In 3 of them the old fact comes from the draft, which "
        'PHASE0-SPEC declares explicitly: "MERGED 2026-09-22 from docs/consults/03-claude-phase0-spec.md". '
        "The build starts."
    )
    log = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + row + "\n")
    draft = doc(2, "docs/consults/03-claude-phase0-spec.md#0", DRAFT)
    assert find_declarations(log.body) == [] and propose([log, draft]) == []


def test_a_document_never_retires_a_decision_row_by_an_implicit_subject() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    consult = doc(2, "docs/consults/40-proposal.md", "Status: supersedes D-036.\n")
    assert propose([d0, consult]) == []


def test_instead_of_in_a_row_needs_the_ref_right_after_the_marker() -> None:
    row_60 = "D-060 | 2026-09-24 | ACCEPTED | Use the local store instead of D-036 for reviews."
    row_61 = "D-061 | 2026-09-24 | ACCEPTED | memory.raw pages via cursor instead of cursor paging (D-036)."
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", row_60 + "\n" + row_61 + "\n")
    (p,) = propose([d0, d1])
    assert (p.source_ref, p.target_ref, p.marker, p.scope) == ("D-060", "D-036", "instead_of", "part")


def test_d_id_range_is_never_resolved() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(
        2,
        "docs/decisions/DECISIONS.md#1",
        "D-070 | x | ACCEPTED | Supersedes D-036..D-048.\nD-071 | y | A | z.\n",
    )
    assert propose([d0, d1]) == []


def test_ambiguous_decision_row_is_unresolvable() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    copy = doc(3, "docs/consults/quoted-log.md", ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", ROW_50 + "\nD-051 | y | A | z.\n")
    out = propose([d0, copy, d1])
    assert [(p.source_ref, p.target_ref) for p in out] == []  # D-036 and D-048 each exist twice


def test_cycles_are_dropped_including_against_existing_links() -> None:
    a = doc(1, "docs/a.md", "Status: supersedes `docs/b.md`.\n")
    b = doc(2, "docs/b.md", "Status: supersedes `docs/a.md`.\n")
    c = doc(3, "docs/c.md", "Status: supersedes `docs/d.md`.\n")
    d = doc(4, "docs/d.md", "Plain text.\n")
    out = propose([a, b, c, d])
    assert pairs(out) == {("docs/c.md", "docs/d.md", "whole", "supersedes")}
    assert propose([c, d], existing=[(d.logical_id, c.logical_id)]) == []


def test_relative_path_and_section_qualifier() -> None:
    new = doc(1, "docs/decisions/NEW.md", "Status: supersedes `OLD.md` §4 (the retrieval section).\n")
    old = doc(2, "docs/decisions/OLD.md", "# Old\n\n## 4. Retrieval\n")
    (p,) = propose([new, old])
    assert (p.source_path, p.target_path, p.scope) == (
        "docs/decisions/NEW.md",
        "docs/decisions/OLD.md",
        "part",
    )


def test_a_document_never_supersedes_its_own_chunk_items() -> None:
    d0 = doc(1, "docs/x.md#0", "Status: merged from `docs/x.md` and `docs/y.md`.\n")
    d1 = doc(2, "docs/x.md#1", "Tail.\n")
    y = doc(3, "docs/y.md", "Y.\n")
    assert pairs(propose([d0, d1, y])) == {
        ("docs/x.md#0", "docs/y.md", "whole", "merged_from"),
        ("docs/x.md#1", "docs/y.md", "whole", "merged_from"),
    }


def test_update_names_the_superseding_decision() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", "D-050 | x | ACCEPTED | sol.\nD-052 | y | ACCEPTED | z.\n")
    note = doc(
        3,
        "automem/model-strategy.md",
        "Astra implements.\n**Update 2026-09-23 (D-050):** supersedes D-036.\n",
    )
    (p,) = propose([d0, d1, note])
    assert (p.source_ref, p.target_ref, p.marker, p.declared_in) == ("D-050", "D-036", "update", 3)
    # an update without an explicit target is an in-item revision: no link
    plain = doc(
        3,
        "automem/model-strategy.md",
        "**Update 2026-09-23 (D-050):** Astra has been replaced by gpt-6-sol.\n",
    )
    assert propose([d0, d1, plain]) == []


# --------------------------------------------------------------------------- DE / TR
def test_german_markers() -> None:
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(2, "docs/decisions/DECISIONS.md#1", "D-050 | x | ACCEPTED | sol.\nD-052 | y | ACCEPTED | z.\n")
    notiz = doc(3, "docs/notiz.md", "- D-050 ersetzt D-036.\n")
    (p,) = propose([d0, d1, notiz])
    assert (p.source_ref, p.target_ref, p.marker) == ("D-050", "D-036", "supersedes")
    entwurf = doc(4, "docs/entwurf.md", "Status: ersetzt durch `docs/decisions/NEW.md`.\n")
    new = doc(5, "docs/decisions/NEW.md", "Neu.\n")
    assert pairs(propose([entwurf, new])) == {
        ("docs/decisions/NEW.md", "docs/entwurf.md", "whole", "superseded_by")
    }


def test_turkish_markers_targets_before_the_marker() -> None:
    taslak = doc(1, "docs/taslak.md", "Bu taslak `docs/decisions/NEW.md` tarafından geçersiz kılındı.\n")
    new = doc(2, "docs/decisions/NEW.md", "Yeni.\n")
    assert pairs(propose([taslak, new])) == {
        ("docs/decisions/NEW.md", "docs/taslak.md", "whole", "superseded_by")
    }
    spec = doc(3, "docs/decisions/SPEC.md", "Durum: `docs/a.md` ve `docs/b.md`'den birleştirildi.\n")
    a, b = doc(4, "docs/a.md", "A.\n"), doc(5, "docs/b.md", "B.\n")
    assert pairs(propose([spec, a, b])) == {
        ("docs/decisions/SPEC.md", "docs/a.md", "whole", "merged_from"),
        ("docs/decisions/SPEC.md", "docs/b.md", "whole", "merged_from"),
    }
    d0 = doc(6, "docs/decisions/DECISIONS.md#0", LOG_HEAD + ROW_36 + "\n" + ROW_48 + "\n")
    d1 = doc(
        7,
        "docs/decisions/DECISIONS.md#1",
        "D-050 | x | KABUL | Sol, D-036'nın yerini alır.\nD-051 | y | A | z.\n",
    )
    (p,) = propose([d0, d1])
    assert (p.source_ref, p.target_ref, p.marker) == ("D-050", "D-036", "replaces")


def test_quotes_are_verbatim_and_bounded_for_long_rows() -> None:
    long_row = "D-036 | 2026-09-22 | ACCEPTED | " + "word " * 200
    d0 = doc(1, "docs/decisions/DECISIONS.md#0", LOG_HEAD + long_row + "\n" + ROW_48 + "\n")
    d1 = doc(
        2, "docs/decisions/DECISIONS.md#1", "D-050 | x | ACCEPTED | Supersedes D-036.\nD-051 | y | A | z.\n"
    )
    (p,) = propose([d0, d1])
    assert len(p.quote) <= QUOTE_MAX and p.quote in d0.body and p.quote.startswith("D-036 |")
