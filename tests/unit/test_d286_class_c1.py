"""D-286: class C1's plan-time conditions (``write_review.c1_link``), its settings, the reader label
and the ops parser. The apply-time conditions, the switches, revert and demotion run against a
database in ``tests/integration/test_d286_class_c1.py``."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from hlmemo.config import Settings
from hlmemo.librarian import classes
from hlmemo.librarian.guards import Judgement
from hlmemo.librarian.tasks import Proposal
from hlmemo.librarian.tasks.write_review import c1_link

OLD_T = datetime(2026, 1, 1, tzinfo=UTC)
NEW_T = datetime(2026, 6, 1, tzinfo=UTC)
QUOTE = "The API cache TTL is 60 seconds"


def _row(lid: int, vid: int, when: datetime, kind: str = "fact") -> Any:
    return SimpleNamespace(logical_id=lid, version_id=vid, valid_from=when, project_ids=[7], kind=kind)


def _judgement(**kw: Any) -> Judgement:
    base: dict[str, Any] = {
        "relation": "contradicts",
        "supersedes": "new",
        "confidence": "high",
        "tier": "action",
        "scope": "part",
        "verification": {"kind": "supersede", "agreed": True},
    }
    base.update(kw)
    return Judgement("v1", **base)


def _c1(j: Judgement | None = None, *, new: Any = None, old: Any = None, **kw: Any) -> dict[str, Any] | None:
    args: dict[str, Any] = {
        "cross": False,
        "same_class": True,
        "props": {
            "by": "librarian",
            "relation": "contradicts",
            "confidence": "high",
            "scope": "part",
            "quote": QUOTE,
        },
        "assessed": {"20": 21, "10": 11},
        "newer": "new",
        "profiles": ("relate-profile", "verify-profile"),
    }
    args.update(kw)
    return c1_link(j or _judgement(), new or _row(20, 21, NEW_T), old or _row(10, 11, OLD_T), **args)


def test_c1_link_is_the_pinned_part_supersedes_link_alone() -> None:
    link = _c1()
    assert link is not None
    assert link["op"] == "link_insert" and link["rel"] == "supersedes"
    assert (link["src_logical_id"], link["dst_logical_id"], link["dst_version_id"]) == (20, 10, 11)
    assert link["props"] == {
        "by": "librarian",
        "relation": "contradicts",
        "confidence": "high",
        "scope": "part",
        "quote": QUOTE,
        "auto": "C1",
    }
    assert link["assessed"] == {"20": 21, "10": 11}


@pytest.mark.parametrize(
    ("case", "kw"),
    [
        ("1 relation duplicate", {"j": _judgement(relation="duplicate", supersedes="none")}),
        ("1 the OLD item supersedes", {"j": _judgement(supersedes="old")}),
        ("1 whole scope", {"j": _judgement(scope="whole")}),
        ("1 a close", {"j": _judgement(close_ok=True)}),
        ("2 question tier", {"j": _judgement(tier="question")}),
        ("2 verifier did not agree", {"j": _judgement(verification={"kind": "supersede", "agreed": False})}),
        ("2 no verifier", {"j": _judgement(verification=None)}),
        ("3 newer is a lesson", {"new": _row(20, 21, NEW_T, "lesson")}),
        ("3 older is an episode", {"old": _row(10, 11, OLD_T, "episode")}),
        ("3 older is a session note", {"old": _row(10, 11, OLD_T, "session_note")}),
        ("3 older is a doc chunk", {"old": _row(10, 11, OLD_T, "doc_chunk")}),
        ("3 older is an experience", {"old": _row(10, 11, OLD_T, "experience")}),
        ("3 decision row", {"j": _judgement(flags=["decision_record"])}),
        ("4 not the same class", {"same_class": False}),
        ("4 across projects", {"cross": True}),
        ("5 newer is older in valid time", {"new": _row(20, 21, datetime(2025, 1, 1, tzinfo=UTC))}),
        ("6 empty quote", {"props": {"scope": "part", "quote": "  "}}),
        ("6 unverified quote", {"j": _judgement(flags=["quote_unverified"])}),
        # review 1 M1: on a valid_from tie the new item must be the later-recorded one
        ("5 full tie", {"new": _row(20, 21, OLD_T), "newer": None}),
        ("5 tie, the OLD one recorded later", {"new": _row(20, 21, OLD_T), "newer": "old"}),
        # review 1 L3: the verifier answered on another profile than the relate call
        ("2 verifier on the same profile", {"profiles": ("p", "p")}),
        ("2 verifier did not answer", {"profiles": ("p", "")}),
    ],
)
def test_each_plan_time_condition_false_gives_no_c1(case: str, kw: dict[str, Any]) -> None:
    assert _c1(**kw) is None, case


def test_proposals_default_to_no_c1_link() -> None:
    """A proposal built without one (pair_check, every other class) never carries a C1 link."""
    prop = Proposal("contradiction", [], True, {}, [], [1])
    assert prop.c1 is None


def test_settings_switch_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().librarian_c1 is False and Settings().librarian_c1_daily_max == 10
    monkeypatch.setenv("HLM_LIBRARIAN_C1", "on")
    monkeypatch.setenv("HLM_LIBRARIAN_C1_DAILY_MAX", "3")
    s = Settings()
    assert s.librarian_c1 is True and s.librarian_c1_daily_max == 3
    monkeypatch.setenv("HLM_LIBRARIAN_C1", "off")
    assert Settings().librarian_c1 is False
    monkeypatch.setenv("HLM_LIBRARIAN_C1_DAILY_MAX", "-1")
    with pytest.raises(ValidationError):
        Settings()


def test_applied_by_names_only_class_links() -> None:
    assert classes.applied_by({"auto": "C1", "by": "librarian"}) == "librarian (C1)"
    for props in (None, {}, {"by": "librarian"}, {"by": "explicit"}, {"auto": "C2"}, {"auto": True}):
        assert classes.applied_by(props) is None


def test_ops_parser_class_commands() -> None:
    from hlmemo.ops.librarian import add_parser

    parser = argparse.ArgumentParser()
    add_parser(parser.add_subparsers(dest="cmd"))
    a = parser.parse_args(["librarian", "class", "set", "C1", "on", "--project", "p", "--decision", "D-286"])
    assert (a.action, a.caction, a.cls, a.state, a.project, a.decision) == (
        "class",
        "set",
        "C1",
        "on",
        "p",
        "D-286",
    )
    a = parser.parse_args(["librarian", "class", "applied", "--since", "2026-11-01", "--json"])
    assert (a.caction, a.since, a.json, a.project) == ("applied", "2026-11-01", True, None)
    a = parser.parse_args(["librarian", "class", "revert", "42", "--reason", "wrong"])
    assert (a.caction, a.link, a.reason) == ("revert", 42, "wrong")
    for bad in (
        ["librarian", "class", "set", "C2", "on", "--project", "p", "--decision", "D-1"],
        ["librarian", "class", "set", "C1", "maybe", "--project", "p", "--decision", "D-1"],
        ["librarian", "class", "set", "C1", "on", "--decision", "D-1"],  # no project-less grant
        ["librarian", "class", "set", "C1", "on", "--project", "p"],  # a grant names its decision
        ["librarian", "class", "revert", "42"],  # a revert names its reason
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(bad)
    assert classes.parse_since("2026-11-01") == datetime(2026, 11, 1, tzinfo=UTC)
    assert classes.parse_since(None) is None


def test_class_applied_prints_no_control_characters() -> None:
    """Round 2 L3: titles and quotes in ``class applied`` carry no control or bidi characters."""
    assert classes._printable("a\x1b[31mb‮c\td\ne\x00f⁦") == "a[31mbc d ef"
    assert classes._printable("Cache TTL changed") == "Cache TTL changed"


def test_decision_id_shape() -> None:
    for ok in ("D-286", "D-1234"):
        assert classes.DECISION_RE.match(ok)
    for bad in ("D-1", "d-286", "D-286 x", "D-28600", "", "286"):
        assert not classes.DECISION_RE.match(bad)
