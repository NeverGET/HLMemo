"""AL5 brief: assembly, section order, token budget and truncation, skeleton skip. Pure; no network."""

from __future__ import annotations

from datetime import UTC, datetime

from hlmemo.brief import assemble as A
from hlmemo.brief.fetch import Item, Snapshot
from hlmemo.core.budget import Meter

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
    b = A.assemble(s)
    assert b is not None
    assert b.sections == ["Now", "Decisions in force", "Open", "Lessons", "Pending review"]
    heads = [ln for ln in b.text.splitlines() if ln.startswith("## ")]
    assert [h.split(" (")[0] for h in heads] == [
        "## Now", "## Decisions in force", "## Open", "## Lessons", "## Pending review",
    ]  # fmt: skip
    assert (
        b.text.rstrip()
        .splitlines()[-1]
        .startswith("as of 2026-09-30; auto-captured items (auto) are unreviewed")
    )
    assert "[v1]" in b.text  # the card handle


def test_every_item_line_has_its_handle_and_auto_marker() -> None:
    s = snap(sessions=[note(5)], lessons=[lesson(7), lesson(8, tags=["auto-capture"])])
    b = A.assemble(s)
    assert b is not None
    lines = [ln for ln in b.text.splitlines() if ln.startswith("- ")]
    assert lines and all(ln.startswith("- [v") or ln[2].isdigit() for ln in lines)
    assert "- [v5 auto] decide A" in b.text
    assert "- [v5 auto] unsure one" in b.text
    assert "- [v7] A lesson — The rule." in b.text
    assert "- [v8 auto] A lesson" in b.text
    assert "Free text that must never appear" not in b.text  # only Decisions/Open bullets, no prose


def test_empty_sections_are_skipped() -> None:
    b = A.assemble(snap(lessons=[lesson(7)]))
    assert b is not None and b.sections == ["Now", "Lessons"]
    assert "## Decisions in force" not in b.text and "## Pending review" not in b.text


def test_skeleton_card_with_nothing_else_skips_the_whole_brief() -> None:
    assert A.assemble(snap(card={"clue": "v1", "text": SKELETON, "stale": False})) is None
    assert A.assemble(snap(card={"clue": "v1", "text": SKELETON, "stale": False}, pending=5)) is None
    assert A.assemble(snap(card=None)) is None


def test_skeleton_card_is_omitted_but_other_sections_stay() -> None:
    b = A.assemble(snap(card={"clue": "v1", "text": SKELETON, "stale": False}, lessons=[lesson(7)]))
    assert b is not None and b.sections == ["Lessons"] and "Skeleton" not in b.text


def test_stale_card_is_flagged() -> None:
    b = A.assemble(snap(card={"clue": "v1", "text": "# P\n\nreal", "stale": True}))
    assert b is not None and "flagged stale" in b.text


def test_dedup_and_per_note_caps() -> None:
    body = (
        "## Decisions\n"
        + "\n".join(f"- d{i}" for i in range(10))
        + "\n## Open\n"
        + "\n".join(f"- o{i}" for i in range(10))
    )
    b = A.assemble(snap(sessions=[note(5, body), note(6, body)]))
    assert b is not None
    assert b.text.count("] d0") == 1  # the same line from an older note is not repeated
    assert b.text.count("[v5] d") == A.DECISIONS_PER_NOTE and b.text.count("[v5] o") == A.OPEN_PER_NOTE


def test_only_three_newest_sessions_and_five_lessons() -> None:
    notes = [note(i, f"## Decisions\n- decision {i}") for i in range(10, 16)]
    b = A.assemble(snap(sessions=notes, lessons=[lesson(i, f"L{i}") for i in range(30, 40)]))
    assert b is not None
    assert "decision 12" in b.text and "decision 13" not in b.text
    assert b.text.count("- [v3") == A.MAX_LESSONS


def test_long_lines_are_cut_with_an_ellipsis_never_rewritten() -> None:
    long = "word " * 200
    b = A.assemble(snap(sessions=[note(5, f"## Decisions\n- {long}")]))
    assert b is not None
    line = next(ln for ln in b.text.splitlines() if ln.startswith("- [v5"))
    assert line.endswith("…") and len(line) < A.LINE_CHARS + 20
    assert line[len("- [v5] ") : -1].strip() in long  # a verbatim prefix


def test_token_budget_is_held_with_the_repo_meter() -> None:
    meter = Meter()
    big_card = "# P\n\n" + "Card sentence number one. " * 400
    notes = [
        note(i, "## Decisions\n" + "\n".join(f"- {'decision ' * 40}{j}" for j in range(6))) for i in range(3)
    ]
    s = snap(card={"clue": "v1", "text": big_card, "stale": False}, sessions=notes,
             lessons=[lesson(i, "T " * 60, "body " * 80) for i in range(20, 26)], pending=2)  # fmt: skip
    for budget in (1500, 900, 400):
        b = A.assemble(s, budget=budget)
        assert b is not None
        assert meter.count_text(b.text) <= budget
        assert b.tokens == meter.count_text(b.text)


def test_default_budget_is_1500_and_card_is_cut_first_cap() -> None:
    big_card = "# P\n\n" + "Card sentence. " * 400
    b = A.assemble(snap(card={"clue": "v1", "text": big_card, "stale": False}))
    assert b is not None and b.tokens <= 1500 and "…" in b.text


def test_drop_order_lessons_then_open_then_decisions() -> None:
    notes = [note(5, "## Decisions\n- " + "keep decision " * 15 + "\n## Open\n- " + "open item " * 15)]
    s = snap(card={"clue": "v1", "text": "# P\n\nshort", "stale": False}, sessions=notes,
             lessons=[lesson(i, "T " * 40, "x " * 80) for i in range(20, 25)])  # fmt: skip
    no_lessons = A.assemble(snap(**{**s.__dict__, "lessons": []}))
    no_open = A.assemble(
        snap(
            **{**s.__dict__, "lessons": [], "sessions": [note(5, "## Decisions\n- " + "keep decision " * 15)]}
        )
    )
    assert no_lessons is not None and no_open is not None
    b = A.assemble(s, budget=no_lessons.tokens + 3)
    assert b is not None
    assert "## Lessons" not in b.text and "## Open" in b.text and "## Decisions in force" in b.text
    b2 = A.assemble(s, budget=no_open.tokens + 3)
    assert (
        b2 is not None
        and "## Open" not in b2.text
        and "## Decisions in force" in b2.text
        and b2.tokens <= no_open.tokens + 3
    )


def test_hard_cut_last_resort_still_within_budget() -> None:
    b = A.assemble(snap(), budget=20, counter=lambda t: len(t) // 4)
    assert b is None or b.tokens <= 20 or b.truncated


def test_counter_falls_back_to_a_pessimistic_estimate(monkeypatch) -> None:
    def boom():
        raise OSError("no vocab offline")

    monkeypatch.setattr(A, "_encoder", boom)
    assert A.count_tokens("x" * 300) == 101
    assert A.truncate_tokens("x" * 300, 10).endswith("…")
    b = A.assemble(snap(lessons=[lesson(7)]))
    assert b is not None


def test_note_sections_parser() -> None:
    d, o = A.note_sections(
        "# T\n- ignored\n## Decisions\n- a\n* b\n1. c\n### Open questions\n- q\n## Other\n- z\n"
    )
    assert d == ["a", "b", "c"] and o == ["q"]
