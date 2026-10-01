"""D-118 write-time supersession: the deterministic per-update rules (``core/write_updates.py``),
the request/ack shapes and the wire schema. No database: every rule that reads rows is covered by
``tests/integration/test_write_updates.py``."""

from __future__ import annotations

from typing import Any

import pytest

from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.write_models import UpdateAck, WriteRequest, WriteResult, parse_request
from hlmemo.core.write_updates import (
    REASONS,
    parse_item,
    pessimistic_ack_entries,
    revise_guards,
    span_occurs,
)
from hlmemo.librarian import revise
from hlmemo.server.tools import TOOL_BY_NAME, schemas

OLD = (
    "The API cache TTL is 60 seconds for every endpoint.\n"
    "Backups run nightly at 02:00 on the db host.\n"
    "Deploys go out on Tuesdays."
)
NEW = "Since June the API cache TTL is 300 seconds for every endpoint."


def guards(**kw: Any) -> tuple[Any, str | None]:
    args: dict[str, Any] = {
        "old_body": OLD,
        "old_span": "60 seconds",
        "replacement": "300 seconds",
        "carrier_body": NEW,
        "old_projects": [1],
        "old_scope": "all",
        "new_projects": [1],
        "new_scope": "all",
    }
    args.update(kw)
    return revise_guards(**args)


# --------------------------------------------------------------------------- item / clue
@pytest.mark.parametrize(
    ("item", "expected", "out"),
    [
        ("v123", None, (None, 123, None)),
        ("v123.4", None, (None, 123, None)),  # a chunk clue names its version
        ("v123", 123, (None, 123, None)),  # a consistent expected_version is fine
        ("v123", 122, (None, None, "expected_version_mismatch")),  # consult 74 #1
        ("v0", None, (None, None, "bad_clue")),
        ("123", None, (None, None, "bad_clue")),
        ("v12x", None, (None, None, "bad_clue")),
        ("$0", None, (None, None, "bad_clue")),
        (42, 7, (42, 7, None)),  # the spec-literal form
        (42, None, (None, None, "expected_version_required")),
        (0, 1, (None, None, "bad_clue")),
    ],
)
def test_the_clue_fixes_the_logical_item_and_the_expected_version(item: Any, expected: Any, out: Any) -> None:
    assert parse_item(item, expected) == out


# --------------------------------------------------------------------------- revise guards
def test_a_valid_revise_passes_every_write_guard() -> None:
    chk, reason = guards()
    assert reason is None and chk is not None
    assert (chk.start, chk.end) == (OLD.index("60 seconds"), OLD.index("60 seconds") + len("60 seconds"))
    assert chk.replacement == "300 seconds"
    # the D-110 guards minus the two relate/v3 judged-statement guards (the writer quotes both sides)
    assert revise.WRITE_GUARDS == (
        "old_body_nfc",
        "old_span_unique",
        "span_word_boundary",
        "span_not_whole",
        "replacement_found",
        "replacement_differs",
        "length_ratio",
        "replacement_visibility",
    )
    assert chk.ok and chk.failed_of(revise.WRITE_GUARDS) == []


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"old_span": "90 seconds"}, "span_not_found"),
        ({"old_span": "on"}, "span_not_unique"),  # "on" occurs twice ("on the db host", "go out on")
        ({"old_span": "0 seconds"}, "span_word_boundary"),  # cuts "60"
        ({"old_span": "60 second"}, "span_word_boundary"),  # cuts "seconds"
        ({"replacement": "900 seconds"}, "replacement_not_in_body"),
        ({"replacement": "60 seconds", "carrier_body": "It stays 60 seconds."}, "replacement_same"),
        (
            {"old_span": "TTL", "replacement": "API cache TTL is 300 seconds for every endpoint"},
            "length_ratio",
        ),
        ({"old_projects": [1, 2]}, "replacement_visibility"),  # copying would widen the text's readers
        ({"new_scope": "device:7"}, "replacement_visibility"),
    ],
)
def test_each_revise_guard_rejects_with_its_reason(kw: dict[str, Any], reason: str) -> None:
    assert guards(**kw) == (None, reason)
    assert REASONS[reason][0] == "E_INVALID_ARG"


def test_a_span_that_is_the_whole_memory_suggests_supersede() -> None:
    body = "Deploys go out on Tuesdays."
    chk, reason = guards(old_body=body, old_span="Deploys go out on Tuesdays")
    assert chk is None and reason == "span_whole"
    assert "supersede" in REASONS["span_whole"][1]


def test_a_non_nfc_target_cannot_be_span_revised() -> None:
    body = "Café opens at 9. " + OLD  # decomposed é: not NFC
    assert guards(old_body=body) == (None, "target_not_nfc")


def test_the_replacement_is_searched_only_in_the_carrying_body() -> None:
    """Consult 74 #2: the title is not searched (unlike B-real) and nothing else of the batch is."""
    assert guards(carrier_body="Cache TTL raised.", replacement="300 seconds") == (
        None,
        "replacement_not_in_body",
    )


def test_an_omitted_replacement_is_the_whole_body_only_for_one_statement() -> None:
    chk, reason = guards(replacement=None, old_span="60 seconds", carrier_body="  300 seconds  \n")
    assert reason is None and chk.replacement == "300 seconds"
    # several statements: the writer must say which one replaces the span
    assert guards(replacement=None, carrier_body="TTL is 300 seconds. Keys are prefixed.") == (
        None,
        "replacement_required",
    )
    # one statement that fails the length ratio: also a missing replacement, not a ratio error
    assert guards(replacement=None, old_span="60 seconds", carrier_body=NEW) == (None, "replacement_required")


def test_statement_count_follows_the_statement_boundaries() -> None:
    assert revise.statement_count("One statement only") == 1
    assert revise.statement_count("  ") == 0
    assert revise.statement_count("First. Second.") == 2
    assert revise.statement_count("line one\nline two") == 2
    assert revise.statement_count('He said "stop." Then left.') == 2


def test_supersede_grounding_is_nfc_byte_exact() -> None:
    assert span_occurs(OLD, "Backups run nightly")
    assert not span_occurs(OLD, "backups run nightly")  # no case folding
    assert span_occurs("Café opens", "Café")  # NFC on both sides


# --------------------------------------------------------------------------- D-113 floor
@pytest.mark.parametrize("kind", sorted(revise.PROTECTED_KINDS))
def test_session_logs_are_protected_even_when_the_kinds_list_names_them(kind: str) -> None:
    """Consult 74: a misconfigured ``HLM_LIBRARIAN_REVISE_KINDS`` never lets an episode (or a
    session note) be revised or closed."""
    kinds = frozenset({"fact", "lesson", "doc_chunk", "episode", "session_note"})
    assert revise.revisable(kind, OLD, None, kinds) == "historical_kind"


def test_decision_rows_are_protected_whatever_their_kind() -> None:
    row = "D-047 | 2026-01-01 | ACCEPTED | The API cache TTL is 60 seconds.\nOwner: ops."
    kinds = frozenset({"fact", "lesson", "doc_chunk"})
    assert revise.revisable("fact", row, None, kinds) == "decision_record"
    assert revise.revisable("fact", OLD, {"path": "docs/adr/0001-cache.md"}, kinds) == "decision_record"
    assert revise.revisable("fact", OLD, None, kinds) is None


# --------------------------------------------------------------------------- request / ack shapes
def _req(updates: Any) -> dict[str, Any]:
    return {
        "project": "demo",
        "request_id": "00000000-0000-4000-8000-000000000001",
        "client": "pytest/0",
        "items": [{"kind": "fact", "title": "t", "body": "b", "updates": updates}],
    }


def test_the_update_shape_is_validated_by_the_request_model() -> None:
    ok = parse_request(WriteRequest, _req([{"item": "v5", "old_span": "x", "mode": "revise"}]))
    (u,) = ok.items[0].updates or []
    assert (u.item, u.expected_version, u.replacement, u.mode) == ("v5", None, None, "revise")
    for bad in (
        [{"item": True, "old_span": "x", "mode": "revise"}],  # a bool is not a logical id
        [{"item": "v5", "old_span": "", "mode": "revise"}],
        [{"item": "v5", "old_span": "x", "mode": "close"}],
        [{"item": "v5", "old_span": "x" * 2001, "mode": "revise"}],
        [{"item": "v5", "old_span": "x", "mode": "revise", "replacement": "y" * 1001}],
        [{"item": "v5", "old_span": "x", "mode": "revise", "extra": 1}],
        [{"item": "v5", "old_span": "x", "mode": "revise"}] * 9,
    ):
        with pytest.raises(ToolError) as ei:
            parse_request(WriteRequest, _req(bad))
        assert ei.value.code == "E_INVALID_ARG"


def test_an_item_without_updates_dumps_exactly_as_before() -> None:
    """``resolved.write`` of an ordinary item is unchanged (exclude_none drops the new field)."""
    req = parse_request(WriteRequest, {**_req(None), "items": [{"kind": "fact", "title": "t", "body": "b"}]})
    assert "updates" not in req.items[0].model_dump(mode="json", exclude_none=True)


def test_a_plain_write_ack_has_no_updates_key_and_unset_fields_are_omitted() -> None:
    base = {"request_id": "r", "replayed": False, "versions": [], "budget": {"limit": 2000, "used": 10}}
    assert "updates" not in WriteResult.model_validate(base).model_dump(mode="json")
    entry = {"index": 0, "update": 0, "status": "applied", "mode": "revise", "clue": "v9"}
    out = WriteResult.model_validate({**base, "updates": [entry]}).model_dump(mode="json")
    assert out["updates"] == [entry]
    assert UpdateAck(index=0, update=1, status="linked", mode="supersede").model_dump() == {
        "index": 0,
        "update": 1,
        "status": "linked",
        "mode": "supersede",
    }


def test_every_reason_has_a_hint_and_the_pessimistic_entry_bounds_every_real_entry() -> None:
    meter = Meter()
    (pess,) = pessimistic_ack_entries(1, meter)
    bound = meter.count(pess)
    for reason, (code, hint) in REASONS.items():
        assert hint and (code is None or code.startswith("E_"))
        real = {
            "index": 49,
            "update": 7,
            "status": "rejected",
            "mode": "supersede",
            "code": code or "",
            "reason": reason,
            "hint": hint,
            "current_clue": f"v{10**15}",
        }
        assert meter.count(real) <= bound, reason


# --------------------------------------------------------------------------- wire schema
def test_the_wire_schema_advertises_updates_and_the_description_guides_the_agent() -> None:
    item = schemas.WRITE_INPUT["$defs"]["Item"]["properties"]["updates"]
    assert item["maxItems"] == 8
    props = item["items"]["properties"]
    # the clue carries the expected version; the integer form's expected_version is accepted by the
    # request model (hlm CLI) but not advertised (the client benchmark saw agents fill it with 0)
    assert set(props) == {"item", "old_span", "mode", "replacement"} and props["item"] == {"type": "string"}
    assert props["mode"]["enum"] == ["revise", "supersede"]
    assert item["items"]["required"] == ["item", "old_span", "mode"]
    desc = TOOL_BY_NAME["memory.write"].description
    for word in ("updates", "clue", "old_span", "revise", "supersede", "verbatim", "rejected"):
        assert word in desc
