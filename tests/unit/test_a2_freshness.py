"""D-209 (GOAL-PLAN A2 item 3): ``meta.memory_as_of`` and the "memory ends on <date>" last line of a
stale answer (freshness line first, truncation marker last)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hlmemo.core import memory_map as mm
from hlmemo.core import research_service as rsv
from hlmemo.core.budget import Meter

METER = Meter()
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _item(vid: int, recorded_at: datetime | None) -> mm.ViewItem:
    return mm.ViewItem(vid, "t", "fact", None, None, 1, [1], None, recorded_at)


def test_memory_as_of_is_the_newest_recorded_at() -> None:
    items = [_item(1, NOW - timedelta(days=5)), _item(2, NOW - timedelta(days=2)), _item(3, None)]
    assert rsv.memory_as_of(items) == NOW - timedelta(days=2)
    assert rsv.memory_as_of([_item(1, None)]) is None and rsv.memory_as_of([]) is None


def test_fresh_memory_gets_no_line() -> None:
    assert rsv.freshness_line(NOW - timedelta(hours=23, minutes=59), NOW, "An answer.") == ""
    assert rsv.freshness_line(NOW - timedelta(hours=24), NOW, "An answer.") == ""
    assert rsv.freshness_line(None, NOW, "An answer.") == ""


def test_stale_memory_gets_the_english_line() -> None:
    as_of = datetime(2026, 9, 26, 23, 30, tzinfo=UTC)
    assert rsv.freshness_line(as_of, NOW, "The p95 target is 1.2 s.") == (
        "(Memory records for this project end on 2026-09-26.)"
    )
    assert rsv.freshness_line(NOW - timedelta(hours=24, seconds=1), NOW, "x") != ""


def test_stale_memory_line_follows_the_turkish_answer() -> None:
    as_of = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    tr = "Hedef şimdi 1,2 s; önceden 1,6 s idi. Karar sahibi verdi."
    assert (
        rsv.freshness_line(as_of, NOW, tr) == "(Bu projenin bellek kayıtları 2026-09-26 tarihinde bitiyor.)"
    )
    de = "Schließen Sie die Prüfung für Größe ab."  # ö/ü/ß are not Turkish markers
    assert rsv.freshness_line(as_of, NOW, de).startswith("(Memory records")


def test_freshness_line_comes_before_the_truncation_marker() -> None:
    line = rsv.freshness_line(datetime(2026, 9, 26, tzinfo=UTC), NOW, "x")
    out = {
        "project": "p",
        "answer": "The answer. " * 20 + f"\n{line}",
        "abstained": False,
        "confidence": "high",
        "claims": [
            {"text": f"Claim {i}.", "support": [{"handle": f"v{i}.0", "quote": "q " * 40}]} for i in range(6)
        ],
        "primary": [{"handle": f"v{i}.0", "path": f"docs/{i}.md", "quote": "q " * 60} for i in range(4)],
        "related": [],
        "meta": {"flags": {"truncated": False}, "memory_as_of": "2026-09-26T00:00:00+00:00"},
    }
    full = rsv._pack(METER, {**out, "meta": {**out["meta"], "flags": {"truncated": False}}}, 8000)
    packed = rsv._pack(METER, out, full["budget"]["used"] - 400)
    lines = packed["answer"].splitlines()
    assert lines[-2] == line and lines[-1].startswith("[answer truncated at token_budget=")
    assert packed["meta"]["flags"]["truncated"] is True and packed["meta"]["memory_as_of"]
