"""R4.1 review round 2 N-3: the capture summarizer must not invent decision labels ("D-1") and a lesson body
restates only what its verbatim evidence supports. Fixture LLM outputs only, no LLM calls."""

from __future__ import annotations

import re

from hlmemo.capture import summarize as sm

TRANSCRIPT = (
    "owner: the migration failed because the lock timeout was 3s and a reader held the table. "
    "assistant: we decided per D-217 to stage the constraint first. commit 6ac9b18 pushed."
)
EVIDENCE = "the migration failed because the lock timeout was 3s and a reader held the table"


def _obj(notes: str, decisions: list[str] | None = None, lessons: list[dict] | None = None) -> dict:
    return {"notes": notes, "decisions": decisions or [], "lessons": lessons or [], "uncertain": []}


def test_prompt_forbids_invented_labels_and_lesson_generalisation() -> None:
    assert '"D-1"' in sm.SYSTEM_PROMPT and "ONLY if that exact id" in sm.SYSTEM_PROMPT
    assert "ONLY what its verbatim" in sm.SYSTEM_PROMPT


def test_invented_short_decision_labels_are_scrubbed_everywhere() -> None:
    v = sm.validate_summary(
        _obj(
            "Decision (D-1): stage the constraint. Also D-2 was taken, per D-217.",
            decisions=["D-3 use a staged swap"],
            lessons=[
                {"title": "Lock (D-4)", "body": "The lock timeout was 3s (D-5).", "tags": [], "evidence": EVIDENCE}
            ],
        ),
        TRANSCRIPT,
    )
    blob = "\n".join([v.notes, *v.decisions, *(x["title"] + x["body"] for x in v.lessons)])
    for bad in ("D-1", "D-2", "D-3", "D-4", "D-5"):
        assert not re.search(rf"\b{bad}\b", blob)
    assert "D-217" in v.notes  # an id that is verbatim in the transcript stays
    assert v.notes.startswith("Decision: stage the constraint.")
    assert v.dropped["invented_ids_scrubbed"] == 4  # D-5 went with its unsupported lesson sentence


def test_a_lesson_body_keeps_only_evidence_supported_sentences() -> None:
    body = (
        "The migration failed because the lock timeout was 3s and a reader held the table. "
        "Always run every migration in a maintenance window with pgbouncer paused and a replica promoted."
    )
    v = sm.validate_summary(
        _obj("note", lessons=[{"title": "Lock timeout", "body": body, "tags": ["db"], "evidence": EVIDENCE}]),
        TRANSCRIPT,
    )
    assert v.lessons[0]["body"] == "The migration failed because the lock timeout was 3s and a reader held the table."
    assert v.dropped["lessons_body_trimmed"] == 1


def test_a_lesson_body_with_no_supported_sentence_becomes_the_evidence() -> None:
    v = sm.validate_summary(
        _obj(
            "note",
            lessons=[
                {
                    "title": "Lock timeout",
                    "body": "Migrations in general should always be wrapped in retries with exponential backoff.",
                    "tags": [],
                    "evidence": EVIDENCE,
                }
            ],
        ),
        TRANSCRIPT,
    )
    assert v.lessons[0]["body"] == EVIDENCE


def test_an_invented_number_in_a_lesson_sentence_drops_it() -> None:
    v = sm.validate_summary(
        _obj(
            "note",
            lessons=[
                {
                    "title": "Lock timeout",
                    "body": "The lock timeout was 30s and a reader held the table.",
                    "tags": [],
                    "evidence": EVIDENCE,
                }
            ],
        ),
        TRANSCRIPT,
    )
    assert "30s" not in v.lessons[0]["body"]


def test_a_supported_short_body_is_untouched() -> None:
    body = "Lock timeout was 3s and a reader held the table."
    v = sm.validate_summary(
        _obj("note", lessons=[{"title": "Lock timeout", "body": body, "tags": [], "evidence": EVIDENCE}]),
        TRANSCRIPT,
    )
    assert v.lessons[0]["body"] == body and "lessons_body_trimmed" not in v.dropped
