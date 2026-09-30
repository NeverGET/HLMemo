"""R4.1 review F-5: ``truncated`` is true when the FIRST sentence/block alone is cut at the answer limit."""

from __future__ import annotations

from hlmemo.librarian.tasks import research as rs

EXCERPT = rs.Excerpt(
    "v1.0",
    1,
    "Deploy",
    "docs/deploy.md",
    "2026-09-26",
    "Deploy with step one using port 8080.\ndeploy using port 8080 now",
)
SHOWN = {"v1.0": EXCERPT}


def _answer(text: str) -> rs.Validated:
    obj = {"status": "answered", "answer": text, "sources": ["v1.0"], "related": [], "confidence": "high"}
    return rs.validate_prose(obj, SHOWN)


def test_a_first_sentence_longer_than_the_limit_sets_truncated() -> None:
    v = _answer("Deploy " + "with step one " * 300 + "done.")  # one sentence, > 4000 characters
    assert len(v.answer) == rs.ANSWER_MAX_CHARS
    assert v.truncated is True


def test_a_first_long_code_block_sets_truncated() -> None:
    v = _answer("```\n" + "deploy using port 8080 now\n" * 200 + "```")
    assert len(v.answer) == rs.ANSWER_MAX_CHARS
    assert v.truncated is True


def test_a_later_sentence_over_the_limit_still_sets_truncated() -> None:
    v = _answer("Deploy using port 8080. " + "Deploy with step one " * 400 + "now.")
    assert v.truncated is True


def test_a_short_answer_is_not_truncated() -> None:
    v = _answer("Deploy using port 8080.")
    assert v.truncated is False and v.answer == "Deploy using port 8080."
