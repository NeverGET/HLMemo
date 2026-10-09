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
    update_guards,
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
    """Consult 74 #2: the carrying item's title is not searched for the replacement (unlike B-real)
    and nothing else of the batch is. (The TARGET's title only grounds a supersede's old_span: see
    the title tests below.)"""
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


def ug(mode: str, historical: bool = False, **kw: Any) -> tuple[Any, str | None]:
    args: dict[str, Any] = {
        "old_body": OLD,
        "old_span": "Backups run nightly",
        "replacement": None,
        "carrier_body": NEW,
        "old_projects": [1],
        "old_scope": "all",
        "new_projects": [1],
        "new_scope": "all",
    }
    args.update(kw)
    return update_guards(mode=mode, historical=historical, **args)


@pytest.mark.parametrize("historical", [False, True])
def test_supersede_grounding_is_nfc_byte_exact(historical: bool) -> None:
    assert ug("supersede", historical)[1] is None
    assert ug("supersede", historical, old_span="backups run nightly")[1] == "span_not_found"  # no folding
    nfc, nfd = "Café", "Café"
    # NFC on both sides: a decomposed quote of an NFC memory, and the other way round
    assert ug("supersede", historical, old_body=f"{nfc} opens at 9 every day.", old_span=nfd)[1] is None
    assert ug("supersede", historical, old_body=f"{nfd} opens at 9 every day.", old_span=nfc)[1] is None


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("mode", ["supersede", "revise"])
def test_r96_sol2_every_update_passes_the_shared_old_span_rules_first(mode: str, historical: bool) -> None:
    """Review 96 Sol #2: exactly once and on word boundaries, whatever the mode or kind (the old
    supersede/link-only path only checked that the quote occurs)."""
    body = "The cache is Redis. The cache is monitored by the ops team every day."
    kw = {"old_body": body, "replacement": "removed", "carrier_body": "The cache was removed."}
    assert ug(mode, historical, old_span="cache", **kw) == (None, "span_not_unique")
    assert ug(mode, historical, old_span="Redi", **kw) == (None, "span_word_boundary")
    assert ug(mode, historical, old_span="Memcached", **kw) == (None, "span_not_found")
    assert ug(mode, historical, old_span="The cache is Redis", **kw)[1] is None


def test_r96_sol2_the_part_and_replacement_rules_apply_to_every_revise_not_to_a_supersede() -> None:
    whole = {"old_body": "Deploys go out on Tuesdays.", "old_span": "Deploys go out on Tuesdays"}
    assert ug("supersede", **whole)[1] is None  # a supersede IS the whole-item update
    assert ug("supersede", True, **whole)[1] is None
    for historical in (False, True):
        assert ug("revise", historical, replacement="x", **whole) == (None, "span_whole")
        assert ug("revise", historical, replacement="900 seconds") == (None, "replacement_not_in_body")
        assert ug("revise", historical, carrier_body="One. Two.") == (None, "replacement_required")
    # a historical revise is a link: it copies nothing into the target, so it may be narrower
    assert (
        ug("revise", True, old_span="60 seconds", replacement="300 seconds", new_scope="device:7")[1] is None
    )
    assert ug("revise", False, old_span="60 seconds", replacement="300 seconds", new_scope="device:7") == (
        None,
        "replacement_visibility",
    )


@pytest.mark.parametrize("kw", [{"new_scope": "device:7"}, {"old_projects": [1, 2]}])
def test_r96_sol3_a_closing_supersede_needs_a_carrier_as_visible_as_its_target(kw: dict[str, Any]) -> None:
    """Review 96 Sol #3: a narrow carrier never closes a broader target (its readers would lose the
    memory without seeing why); a link-only (historical) supersede adds a narrow link only."""
    assert ug("supersede", **kw) == (None, "replacement_visibility")
    assert ug("supersede", True, **kw)[1] is None
    hint = REASONS["replacement_visibility"][1]
    assert "widen" in hint and "supersede" not in hint  # supersede is no way around it any more


# --------------------------------------------------------------------------- the target's title
TITLE = "Ops defaults: cache TTL, nightly backups, Tuesday deploys"


@pytest.mark.parametrize("historical", [False, True])
def test_a_supersede_may_quote_the_outdated_title(historical: bool) -> None:
    """A writer superseding a memory whose TITLE is outdated quotes the title: when the span is not
    in the body at all, the shared old_span rules run over the NFC title and the update passes as a
    whole-memory update (``quote_in == "title"``)."""
    chk, reason = ug("supersede", historical, old_span="Tuesday deploys", old_title=TITLE)
    assert reason is None and chk.quote_in == "title"
    assert (chk.start, chk.end) == (TITLE.index("Tuesday deploys"), len(TITLE))  # offsets into the title
    # a body quote stays a body quote, also when the title holds it too
    chk, reason = ug("supersede", historical, old_span="Backups run nightly", old_title=TITLE)
    assert reason is None and chk.quote_in == "body"
    chk, reason = ug("supersede", historical, old_span="cache TTL", old_title="The cache TTL is 60 seconds")
    assert reason is None and chk.quote_in == "body"
    # without a title the old behaviour holds
    assert ug("supersede", historical, old_span="Tuesday deploys") == (None, "span_not_found")


@pytest.mark.parametrize("historical", [False, True])
def test_a_revise_quoting_the_title_is_told_to_supersede(historical: bool) -> None:
    """A revise changes only the body (and a part-scope quote must stay in it): a span found only in
    the title is ``span_in_title``, whose hint names supersede; a span found nowhere stays
    ``span_not_found``, whose hint says it looks in the body."""
    kw = {"replacement": "300 seconds", "old_title": TITLE}
    assert ug("revise", historical, old_span="Tuesday deploys", **kw) == (None, "span_in_title")
    assert ug("revise", historical, old_span="Ops default", **kw) == (None, "span_in_title")  # any hit
    assert ug("revise", historical, old_span="Memcached", **kw) == (None, "span_not_found")
    code, hint = REASONS["span_in_title"]
    assert code == "E_INVALID_ARG" and "title" in hint and "supersede" in hint
    assert "body" in REASONS["span_not_found"][1]


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize(
    ("title", "span", "reason"),
    [
        (TITLE, "Memcached", "span_not_found"),  # neither body nor title
        (TITLE, "Ops default", "span_word_boundary"),  # cuts "defaults"
        (TITLE, "efaults", "span_word_boundary"),
        ("Ops defaults and Ops owners", "Ops", "span_not_unique"),  # twice in the title
        ("Café hours", "Cafe hours", "span_not_found"),  # no folding beyond NFC
    ],
)
def test_a_title_quote_passes_the_same_shared_rules(
    title: str, span: str, reason: str, historical: bool
) -> None:
    assert ug("supersede", historical, old_span=span, old_title=title) == (None, reason)


def test_a_title_quote_is_nfc_byte_exact_and_still_needs_a_visible_carrier() -> None:
    nfc, nfd = "Café hours", "Café hours"
    assert nfc != nfd
    for title, span in ((nfd, nfc), (nfc, nfd)):
        chk, reason = ug("supersede", old_span=span, old_title=title)
        assert reason is None and chk.quote_in == "title"
    # review 96 Sol #3 holds for a title quote: a closing supersede never narrows the readers
    assert ug("supersede", old_span="Tuesday deploys", old_title=TITLE, new_scope="device:7") == (
        None,
        "replacement_visibility",
    )
    assert ug("supersede", True, old_span="Tuesday deploys", old_title=TITLE, new_scope="device:7")[1] is None


def test_a_body_span_follows_the_body_path_even_when_the_title_would_pass() -> None:
    """Found in the body (even twice, or cutting a word), the body's verdict stands: the title is
    consulted only for a span the body does not hold at all."""
    title = "Deploys on Tuesdays"
    assert ug("supersede", old_span="on", old_title=title) == (None, "span_not_unique")
    assert ug("revise", old_span="on", replacement="300 seconds", old_title=title) == (
        None,
        "span_not_unique",
    )
    assert ug("supersede", old_span="eploys go", old_title="eploys go") == (None, "span_word_boundary")


# --------------------------------------------------------------------------- review 96 Astra #2
class _ChainConn:
    """A fake connection over a restore chain: version (link) ``k`` is superseded and the copy a
    reversal restored of it is ``k + 1``; only ``last`` is live (the shape that revise/revert cycles
    of later updates leave behind)."""

    def __init__(self, last: int) -> None:
        self.last = last

    async def execute(self, sql: str, params: Any) -> Any:
        last = self.last
        if "version_reopen" in sql:  # the reversal copy of a version
            key, row = int(params[2]), None
            row = (key + 1,) if key < last else None
        elif "supersedes_link_id" in sql:  # the reversal copy of a link
            key = int(params[1])
            row = (key + 1,) if key < last else None
        else:  # is this version / link live?
            row = (int(params[0]) == last,)

        class Cur:
            async def fetchone(self) -> Any:
                return row

        return Cur()


@pytest.mark.parametrize("last", [2, 17, 40])
def test_r96_astra2_the_restore_chain_has_no_fixed_length_bound(last: int, monkeypatch: Any) -> None:
    import asyncio

    from hlmemo.librarian import actor

    async def get_link(_conn: Any, lk: int) -> Any:
        return f"link-{lk}"

    monkeypatch.setattr(actor.q, "get_link", get_link)
    conn = _ChainConn(last)
    assert asyncio.run(actor._live_version(conn, 99, 1)) == last  # None at 17 before the fix
    assert asyncio.run(actor._live_link(conn, 1)) == f"link-{last}"


def test_r98_every_supersedes_reader_takes_the_version_read() -> None:
    """Review 98 #3: the old ``superseded_among`` wrapper called ``supersession_among`` without
    ``version_of``, so it silently dropped every pinned link. It is gone; every remaining reader of
    ``supersedes`` links in ``librarian_queries`` applies ``pinned_applies`` with the version read."""
    import inspect

    from hlmemo.db import librarian_queries as lq

    assert not hasattr(lq, "superseded_among") and "superseded_among" not in lq.__all__
    readers = {n: f for n, f in inspect.getmembers(lq, inspect.iscoroutinefunction) if n.startswith("supers")}
    assert set(readers) == {"supersession_among", "supersessions_of"}
    for f in readers.values():
        assert "version_of" in inspect.signature(f).parameters
        assert 'pinned_applies("l", "hv.vid")' in inspect.getsource(f)


@pytest.mark.parametrize(
    ("body", "quote", "carries"),
    [
        ("Session log: the cache TTL is 60. Owner Bob.", "the cache TTL is 60", True),  # exactly once
        ("Session log: the cache TTL is 600. Owner Bob.", "the cache TTL is 60", False),  # inside a word
        ("Session log: TTL 60 here and TTL 60 there.", "TTL 60", False),  # twice: ambiguous
        ("Session log: STTL 60 only.", "TTL 60", False),  # starts inside a word
        ("Oturum: değer 60ı oldu.", "değer 60", False),  # Unicode word char (Python \w)
        ("Oturum: değer 60, sahibi Ayşe.", "değer 60", True),
        ("Session log: TTL 6060 only.", "TTL 60", False),
        ("Session log: nothing here.", "TTL 60", False),
        ("Session log: Cafe\u0301 opens at 9. Owner Bob.", "Café opens at 9", True),  # NFC body
    ],
)
def test_r98_the_part_carry_uses_the_write_path_span_rule(body: str, quote: str, carries: bool) -> None:
    """Review 98 #1 (coordinator round): a pinned part link carries to a later version only when
    that version quotes it the way ``update_guards`` requires of a link-only target — exactly once
    in the NFC body, on word boundaries. The verdict equals ``update_guards`` itself."""
    from hlmemo.core.write_updates import span_quoted_once

    assert span_quoted_once(body, quote) is carries
    _chk, reason = update_guards(
        mode="supersede",
        historical=True,
        old_body=body,
        old_span=quote,
        replacement=None,
        carrier_body="x",
        old_projects=[1],
        old_scope="all",
        new_projects=[1],
        new_scope="all",
    )
    assert (reason is None) is carries


def test_r98_the_part_carry_is_for_part_links_from_another_item_behind_the_pinned_authz() -> None:
    """Review 98 #1: SQL only selects the candidates (part scope, another item, a non-empty quote,
    pinned to an OLDER version of the same item, quote in the NFC body) and the carry sits behind the
    pinned version's authorization like the copy chain."""
    import inspect

    from hlmemo.db.read_queries import part_carries, pinned_applies

    src = inspect.getsource(part_carries)
    for guard in (
        "COALESCE(l.props->>'scope', 'whole') = 'part'",
        "l.src_logical_id <> l.dst_logical_id",
        "length(COALESCE(l.props->>'quote', '')) > 0",
        "JOIN links l ON l.dst_logical_id = pv.logical_id",
        "l.dst_version_id < pv.version_id",
        "span_quoted_once(body, quote)",
    ):
        assert guard in src, guard
    sql = " ".join(pinned_applies("l", "v").split())
    authz = sql.index("pd.version_id = l.dst_version_id")
    assert authz < sql.index("WITH RECURSIVE") and authz < sql.index("%(carry)s")


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


def test_r121_no_hint_outgrows_the_longest_hint_before_the_title_rule() -> None:
    """Review 121 (Sol 6.1): the pessimistic ack sizes every update by the longest hint, so a longer new hint
    made requests that fit a budget before (322 tokens, three body-span supersedes) need 337 after."""
    from hlmemo.core.write_updates import REASONS

    longest_before = Meter().count_text("replacement is too long: at most 3x old_span and 1000 characters")
    assert max(Meter().count_text(h) for _, h in REASONS.values()) <= longest_before
