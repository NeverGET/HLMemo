"""AL5 brief: assembly, section order, token budget and truncation, skeleton skip. Pure; no network."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

from hlmemo.brief import assemble as A
from hlmemo.brief.fetch import Item, Snapshot
from hlmemo.core.budget import Meter

asm = partial(
    A.assemble, include_auto=True, decisions_max_lines=4, lesson_body_chars=130
)  # most tests use auto-captured notes
SKELETON = "# Proj\n\nSkeleton card (D-015) of project `proj`: no summary yet."
NOTE = (
    "Session 2026-09-30\n\nAUTO-CAPTURED session note (claude-code capture hook); not reviewed.\n\n"
    "Free text that must never appear.\n\n## Uncertain / unverified\n- unsure one\n- unsure two\n\n"
    "## Decisions\n- decide A\n- decide B\n"
)


def note(vid: int, body: str = NOTE) -> Item:
    return Item(
        vid,
        "session_note",
        f"Session {vid}",
        None,
        [],
        body=body,
        logical_id=vid,
        verified=True,
        current=True,
        recorded_at=datetime(2026, 9, 30, 11, 0, tzinfo=UTC),
    )


def lesson(vid: int, title: str = "A lesson", body: str = "The rule.", tags: list[str] | None = None) -> Item:
    return Item(
        vid, "lesson", title, None, tags or [], body=body, logical_id=vid, verified=True, current=True
    )


def snap(**kw) -> Snapshot:
    base = {
        "project": "proj",
        "card": {"clue": "v1", "text": "# Proj\n\nReal card text.", "stale": False},
        "as_of": datetime(2026, 9, 30, 19, 0, tzinfo=UTC),
        "now": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    }
    base.update(kw)
    return Snapshot(**base)


def test_section_order_and_footer() -> None:
    s = snap(
        sessions=[note(5)],
        lessons=[lesson(7)],
        pending=3,
        notices=[{"text": "refines: v1 ~ v2", "clues": ["v1", "v2"]}],
    )
    b = asm(s)
    assert b is not None
    assert b.sections == ["Now", A.DECISIONS_TITLE, "Open", "Lessons", "Pending review"]
    heads = [ln for ln in b.text.splitlines() if ln.startswith("## ")]
    assert [h.split(" (project")[0] for h in heads] == [
        "## Now", "## " + A.DECISIONS_TITLE, "## Open", "## Lessons", "## Pending review",
    ]  # fmt: skip
    assert (
        b.text.rstrip()
        .splitlines()[-1]
        .startswith("as of 2026-09-30; auto-captured items (auto) are unreviewed")
    )
    assert "project card v1" in b.text  # the card handle


def test_every_item_line_has_its_handle_and_auto_marker() -> None:
    s = snap(sessions=[note(5)], lessons=[lesson(7), lesson(8, tags=["auto-capture"])])
    b = asm(s)
    assert b is not None
    lines = [ln for ln in b.text.splitlines() if ln.startswith("- ")]
    assert lines and all(ln.startswith(("- [v", "- [2026-")) or ln[2].isdigit() for ln in lines)
    assert "- [2026-09-30 v5 auto] decide A" in b.text
    assert "- [2026-09-30 v5 auto] unsure one" in b.text
    assert "- [v7] A lesson — The rule." in b.text
    assert "- [v8 auto] A lesson" in b.text
    assert "Free text that must never appear" not in b.text  # only Decisions/Open bullets, no prose


def test_empty_sections_are_skipped() -> None:
    b = asm(snap(lessons=[lesson(7)]))
    assert b is not None and b.sections == ["Now", "Lessons"]
    assert "## " + A.DECISIONS_TITLE not in b.text and "## Pending review" not in b.text


def test_skeleton_card_with_nothing_else_skips_the_whole_brief() -> None:
    assert asm(snap(card={"clue": "v1", "text": SKELETON, "stale": False})) is None
    assert asm(snap(card={"clue": "v1", "text": SKELETON, "stale": False}, pending=5)) is None
    assert asm(snap(card=None)) is None


def test_skeleton_card_is_omitted_but_other_sections_stay() -> None:
    b = asm(snap(card={"clue": "v1", "text": SKELETON, "stale": False}, lessons=[lesson(7)]))
    assert b is not None and b.sections == ["Lessons"] and "Skeleton" not in b.text


def test_stale_card_is_flagged() -> None:
    b = asm(snap(card={"clue": "v1", "text": "# P\n\nreal", "stale": True}))
    assert b is not None and "flagged stale" in b.text


def test_only_the_newest_qualifying_note_and_the_line_cap() -> None:
    body = (
        "## Decisions\n"
        + "\n".join(f"- d{i}" for i in range(10))
        + "\n## Open\n"
        + "\n".join(f"- o{i}" for i in range(10))
    )
    b = asm(snap(sessions=[note(5, body), note(6, "## Decisions\n- from the older note")]))
    assert b is not None
    assert b.text.count("v5] d") == 4 and b.text.count("v5] o") == 4  # capped, in note order
    assert "from the older note" not in b.text  # never mixed with an older note


def test_only_five_lessons_and_only_the_newest_note() -> None:
    notes = [note(i, f"## Decisions\n- decision {i}") for i in range(10, 16)]
    b = asm(snap(sessions=notes, lessons=[lesson(i, f"L{i}") for i in range(30, 40)]))
    assert b is not None
    assert "decision 10" in b.text and "decision 11" not in b.text
    assert b.text.count("- [v3") == A.MAX_LESSONS


def test_long_lines_are_cut_with_an_ellipsis_never_rewritten() -> None:
    long = "word " * 200
    b = asm(snap(sessions=[note(5, f"## Decisions\n- {long}")]))
    assert b is not None
    line = next(ln for ln in b.text.splitlines() if ln.startswith("- [2026-09-30 v5"))
    assert line.endswith("…") and len(line) < A.LINE_CHARS + 20
    assert line[len("- [2026-09-30 v5] ") : -1].strip() in long  # a verbatim prefix


def test_token_budget_is_held_with_the_repo_meter() -> None:
    meter = Meter()
    big_card = "# P\n\n" + "Card sentence number one. " * 400
    notes = [
        note(i, "## Decisions\n" + "\n".join(f"- {'decision ' * 40}{j}" for j in range(6))) for i in range(3)
    ]
    s = snap(card={"clue": "v1", "text": big_card, "stale": False}, sessions=notes,
             lessons=[lesson(i, "T " * 60, "body " * 80) for i in range(20, 26)], pending=2)  # fmt: skip
    for budget in (1500, 900, 400):
        b = asm(s, budget=budget)
        assert b is not None
        assert meter.count_text(b.text) <= budget
        assert b.tokens == meter.count_text(b.text)


def test_default_budget_is_1500_and_card_is_cut_first_cap() -> None:
    big_card = "# P\n\n" + "Card sentence. " * 400
    b = asm(snap(card={"clue": "v1", "text": big_card, "stale": False}))
    assert b is not None and b.tokens <= 1500 and "…" in b.text


def test_drop_order_lessons_then_open_then_decisions() -> None:
    notes = [note(5, "## Decisions\n- " + "keep decision " * 15 + "\n## Open\n- " + "open item " * 15)]
    s = snap(card={"clue": "v1", "text": "# P\n\nshort", "stale": False}, sessions=notes,
             lessons=[lesson(i, "T " * 40, "x " * 80) for i in range(20, 25)])  # fmt: skip
    no_lessons = asm(snap(**{**s.__dict__, "lessons": []}))
    no_open = asm(
        snap(
            **{**s.__dict__, "lessons": [], "sessions": [note(5, "## Decisions\n- " + "keep decision " * 15)]}
        )
    )
    assert no_lessons is not None and no_open is not None
    b = asm(s, budget=no_lessons.tokens + 3)
    assert b is not None
    assert "## Lessons" not in b.text and "## Open" in b.text and "## " + A.DECISIONS_TITLE in b.text
    b2 = asm(s, budget=no_open.tokens + 3)
    assert (
        b2 is not None
        and "## Open" not in b2.text
        and "## " + A.DECISIONS_TITLE in b2.text
        and b2.tokens <= no_open.tokens + 3
    )


def test_hard_cut_last_resort_still_within_budget() -> None:
    b = asm(snap(), budget=20, counter=lambda t: len(t) // 4)
    assert b is None or b.tokens <= 20 or b.truncated


def test_counter_falls_back_to_a_pessimistic_estimate(monkeypatch) -> None:
    def boom():
        raise OSError("no vocab offline")

    monkeypatch.setattr(A, "_encoder", boom)
    assert A.count_tokens("x" * 300) == 101
    assert A.truncate_tokens("x" * 300, 10).endswith("…")
    b = asm(snap(lessons=[lesson(7)]))
    assert b is not None


def test_note_sections_parser() -> None:
    d, o = A.note_sections(
        "# T\n- ignored\n## Decisions\n- a\n* b\n1. c\n### Open questions\n- q\n## Other\n- z\n"
    )
    assert d == ["a", "b", "c"] and o == ["q"]


def test_old_notes_contribute_nothing_and_the_sections_vanish() -> None:
    old = note(5)
    old.recorded_at = datetime(2026, 9, 20, tzinfo=UTC)  # 11 days before "now"
    b = asm(snap(sessions=[old], lessons=[lesson(7)]))
    assert b is not None and b.sections == ["Now", "Lessons"]
    assert "decide A" not in b.text and "unsure one" not in b.text


def test_age_window_is_configurable() -> None:
    old = note(5, "## Decisions\n- older decision")
    old.recorded_at = datetime(2026, 9, 20, tzinfo=UTC)
    b7 = asm(snap(sessions=[old]))
    b30 = asm(snap(sessions=[old]), max_age_days=30)
    assert b7 is not None and b30 is not None
    assert "older decision" not in b7.text
    assert "2026-09-20 v5] older decision" in b30.text


def test_undated_note_is_skipped() -> None:
    n = note(5)
    n.recorded_at = None
    b = asm(snap(sessions=[n], lessons=[lesson(7)]))
    assert b is not None and "decide A" not in b.text


# --------------------------------------------------------------------------- D-206: reviewed items only
def test_auto_items_are_excluded_by_default_but_counted() -> None:
    manual = note(6, "Session 2026-09-30\n\nhand written\n\n## Decisions\n- curated decision\n")
    s = snap(
        sessions=[note(5), manual],
        lessons=[lesson(7, "Curated"), lesson(8, "Auto one", tags=["auto-capture"])],
        pending=4,
        notices=[{"text": "refines: v1 ~ v2", "clues": ["v1", "v2"]}],
    )
    b = A.assemble(s, decisions_max_lines=4, lesson_body_chars=0)
    assert b is not None
    assert "decide A" not in b.text and "unsure one" not in b.text and "Auto one" not in b.text
    assert "curated decision" in b.text and "Curated" in b.text
    assert "- 4 librarian questions + at least 2 auto-captured items await review (memory.answer)" in b.text
    assert "Open" not in b.sections  # only auto notes had open items


def test_include_auto_true_shows_them() -> None:
    b = A.assemble(
        snap(sessions=[note(5)], lessons=[lesson(8, "Auto one", tags=["auto-capture"])]),
        include_auto=True,
        decisions_max_lines=4,
    )
    assert b is not None and "decide A" in b.text and "Auto one" in b.text


def test_only_auto_content_means_card_plus_pending_count() -> None:
    b = A.assemble(snap(sessions=[note(5)]))
    assert b is not None and b.sections == ["Now", "Pending review"]
    assert "at least 1 auto-captured item await review" in b.text and "librarian question" not in b.text


def test_only_auto_content_and_skeleton_card_skips_the_brief() -> None:
    assert A.assemble(snap(card={"clue": "v1", "text": SKELETON, "stale": False}, sessions=[note(5)])) is None


def test_non_auto_note_still_obeys_the_age_window() -> None:
    manual = note(6, "hand written\n\n## Decisions\n- curated decision\n")
    manual.recorded_at = datetime(2026, 9, 1, tzinfo=UTC)
    b = A.assemble(snap(sessions=[manual]), decisions_max_lines=4)
    assert b is not None and "curated decision" not in b.text


def test_pending_count_singular_and_questions_only() -> None:
    b = A.assemble(snap(lessons=[lesson(7)], pending=1))
    assert b is not None and "- 1 librarian question await review" in b.text


def test_card_heading_shows_its_date_and_staleness() -> None:
    fresh = A.assemble(snap(card_date=datetime(2026, 9, 30, tzinfo=UTC), lessons=[lesson(7)]))
    old = A.assemble(snap(card_date=datetime(2026, 9, 20, tzinfo=UTC), lessons=[lesson(7)]))
    edge = A.assemble(snap(card_date=datetime(2026, 9, 28, 12, tzinfo=UTC), lessons=[lesson(7)]))
    unknown = A.assemble(snap(lessons=[lesson(7)]))
    assert fresh is not None and old is not None and edge is not None and unknown is not None
    assert "## Now (project card v1, updated 2026-09-30)" in fresh.text
    assert "## Now (project card v1, updated 2026-09-20, may be stale)" in old.text
    assert "may be stale" not in edge.text  # 3 days exactly is not older than 3 days
    assert "## Now (project card v1)" in unknown.text


def test_decisions_heading_says_auto_only_when_auto_is_included() -> None:
    manual = note(6, "hand written\n\n## Decisions\n- curated decision\n")
    b = A.assemble(snap(sessions=[manual]), decisions_max_lines=4)
    assert (
        b is not None
        and "## Recent session decisions\n" in b.text
        and "auto-captured, unreviewed)" not in b.text
    )
    b2 = A.assemble(snap(sessions=[manual]), include_auto=True, decisions_max_lines=4)
    assert b2 is not None and "## " + A.DECISIONS_TITLE in b2.text


def test_defaults_show_no_history_and_lesson_titles_only() -> None:
    manual = note(6, "hand written\n\n## Decisions\n- curated decision\n\n## Open\n- open one\n")
    b = A.assemble(snap(sessions=[manual], lessons=[lesson(7, "Only the title", "BODY LINE")]))
    assert b is not None
    assert b.sections == ["Now", "Lessons"]  # decision/open history is off by default
    assert (
        "- [v7] Only the title" in b.text
        and "BODY LINE" not in b.text
        and "—" not in b.text.split("## Lessons")[1]
    )
    b2 = A.assemble(snap(lessons=[lesson(7, "T", "BODY LINE")]), lesson_body_chars=9)
    assert b2 is not None and "- [v7] T — BODY" in b2.text


def test_open_section_follows_the_newest_note_rule() -> None:
    older = note(5, "hand\n\n## Open\n- stale open item\n")
    newer = note(6, "hand\n\n## Decisions\n- fresh decision\n")
    b = A.assemble(snap(sessions=[newer, older]), decisions_max_lines=3, include_auto=True)
    assert (
        b is not None
        and "fresh decision" in b.text
        and "stale open item" not in b.text
        and "Open" not in b.sections
    )
