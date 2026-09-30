"""D-210 (GOAL-PLAN A2 item 2): when the literal validator drops a numbered-list item the remaining
items are renumbered (the list starts at 1 and stays consecutive); a bullet list is unaffected; a
lead-in whose items all went is dropped too (D-187)."""

from __future__ import annotations

from hlmemo.librarian.tasks import research as rs

EXCERPT = rs.Excerpt(
    "v1.0",
    1,
    "Deploy",
    "docs/deploy.md",
    "2026-09-26",
    "Deploy with step one using port 8080. Then run step two. Finally verify step three.",
)
SHOWN = {"v1.0": EXCERPT}
BAD = "Run the gate on port 9999 first."  # port 9999 is in no excerpt: dropped by the literal check


def _answer(text: str) -> rs.Validated:
    obj = {"status": "answered", "answer": text, "sources": ["v1.0"], "related": [], "confidence": "high"}
    return rs.validate_prose(obj, SHOWN)


def test_dropping_the_first_item_renumbers_from_1() -> None:
    v = _answer(f"Procedure:\n1. {BAD}\n2. Deploy using port 8080.\n3. Verify step three.\n")
    assert v.drop_reasons["literal"] == 1
    assert v.answer == "Procedure:\n1. Deploy using port 8080.\n2. Verify step three."
    assert [c.text for c in v.kept] == ["Procedure:", "1. Deploy using port 8080.", "2. Verify step three."]


def test_dropping_a_middle_item_closes_the_gap() -> None:
    v = _answer(f"Procedure:\n1. Deploy using port 8080.\n2. {BAD}\n3. Verify step three.\n")
    assert v.answer == "Procedure:\n1. Deploy using port 8080.\n2. Verify step three."


def test_dropping_the_last_item_changes_nothing_else() -> None:
    v = _answer(f"Procedure:\n1. Deploy using port 8080.\n2. Verify step three.\n3. {BAD}\n")
    assert v.answer == "Procedure:\n1. Deploy using port 8080.\n2. Verify step three."


def test_parenthesis_markers_and_a_second_list_are_renumbered_separately() -> None:
    v = _answer(
        f"First:\n1) {BAD}\n2) Deploy using port 8080.\n3) Verify step three.\n"
        "Again:\n1. Deploy using port 8080.\n2. Verify step three.\n"
    )
    assert v.answer == (
        "First:\n1) Deploy using port 8080.\n2) Verify step three.\nAgain:\n1. Deploy using port 8080.\n"
        "2. Verify step three."
    )


def test_bullet_list_is_unaffected() -> None:
    v = _answer(f"Procedure:\n- {BAD}\n- Deploy using port 8080.\n- Verify step three.\n")
    assert v.answer == "Procedure:\n- Deploy using port 8080.\n- Verify step three."


def test_all_items_dropped_leaves_no_dangling_lead_in() -> None:
    v = _answer(f"Deploy using port 8080. Then:\n1. {BAD}\n2. Open port 9998 too.\n")
    assert v.answer == "Deploy using port 8080."
    assert v.drop_reasons["dangling"] == 1


def test_renumbering_is_idempotent_for_the_final_recheck() -> None:
    v = _answer(f"Procedure:\n1. {BAD}\n2. Deploy using port 8080.\n3. Verify step three.\n")
    again, _reasons = rs.prose_check([(c.text, c.line_end) for c in v.kept], ["v1.0"], SHOWN, "sources")
    assert rs.assemble_prose(rs.ANSWERED, again, ["v1.0"], [], "high", SHOWN, lambda s: s).answer == v.answer
