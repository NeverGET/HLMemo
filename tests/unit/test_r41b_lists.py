"""R4.1 review F-2: list renumbering stays inside one contiguous numbered block."""

from __future__ import annotations

from hlmemo.librarian.tasks import research as rs

EXCERPT = rs.Excerpt(
    "v1.0",
    1,
    "Deploy",
    "docs/deploy.md",
    "2026-09-26",
    "Deploy with step one using port 8080. Then run step two. Finally verify step three. 3. Ekim 2026.",
)
SHOWN = {"v1.0": EXCERPT}
BAD = "Run the gate on port 9999 first."


def _answer(text: str) -> rs.Validated:
    obj = {"status": "answered", "answer": text, "sources": ["v1.0"], "related": [], "confidence": "high"}
    return rs.validate_prose(obj, SHOWN)


def test_a_date_after_a_list_is_not_renumbered() -> None:
    v = _answer(f"1. {BAD}\n2. Deploy using port 8080.\nTarih\n3. Ekim")
    assert v.answer.endswith("3. Ekim")
    assert "1. Deploy using port 8080." in v.answer


def test_text_between_two_lists_separates_them() -> None:
    v = _answer(f"1. {BAD}\n2. Deploy using port 8080.\nAnd again\n3. Verify step three.")
    # the second list is its own block: its "3." is not shifted by the first list's drop
    assert v.answer == "1. Deploy using port 8080.\nAnd again\n3. Verify step three."


def test_list_inside_a_fenced_block_is_untouched() -> None:
    v = _answer(f"1. {BAD}\n2. Deploy using port 8080.\n```\n3. port 8080\n3. Ekim 2026\n```\n")
    assert "```\n3. port 8080\n3. Ekim 2026\n```" in v.answer
    assert v.answer.startswith("1. Deploy using port 8080.")


def test_a_contiguous_list_is_still_renumbered() -> None:
    v = _answer(f"1. {BAD}\n2. Deploy using port 8080.\n3. Verify step three.")
    assert v.answer == "1. Deploy using port 8080.\n2. Verify step three."


def test_a_month_date_right_after_a_list_is_left_alone() -> None:
    v = _answer(f"1. {BAD}\n2. Deploy using port 8080.\n3. Ekim")
    assert v.answer.endswith("3. Ekim")
