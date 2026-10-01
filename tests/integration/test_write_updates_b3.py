"""B3 (the D-118 port onto main): one explicit reproducer per refusal rule, through the real write path.

Each test checks that the NEW memory is still written, that the target is untouched (rows and links),
and that live == replay. The ported branch suites (``test_write_updates.py``, ``..._r76.py``) cover
the rest (revise / supersede / historical kinds / locks / reversal).
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import pytest

from tests.integration._write_fixtures import MAIN, item
from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by import
    NEW_TTL,
    OLD,
    REPL,
    SPAN,
    _links,
    _old,
    _replay_identical,
    _rows,
    _upd,
    _write,
    deps,
    world,
)

pytestmark = pytest.mark.integration


async def _revised(connect, world, deps, old: dict) -> dict:  # noqa: ANN001
    (rev,) = (
        await _write(
            connect,
            world.ctx_a,
            MAIN,
            [
                {
                    **item(OLD[0], OLD[1] + "\nOwner: ops."),
                    "logical_id": old["logical_id"],
                    "expected_version_id": old["version_id"],
                }
            ],
            deps,
        )
    )["versions"]
    return rev


async def test_b3_a_wrong_expected_version_is_refused_in_every_form(connect, world, deps) -> None:  # noqa: ANN001
    """The memory the writer read has moved on (v1 → v2): a stale clue, the integer form with a stale
    or an unknown ``expected_version``, and a clue contradicted by ``expected_version`` are all
    refused; the new memories are written; the target keeps its rows and gains no link."""
    old = await _old(connect, world, deps)
    rev = await _revised(connect, world, deps, old)
    state, links = await _rows(connect, old["logical_id"]), await _links(connect)
    specs = [
        _upd(old, replacement=REPL),  # the stale clue v1
        {"item": old["logical_id"], "expected_version": old["version_id"], "old_span": SPAN,
         "mode": "revise", "replacement": REPL},  # integer form, stale version
        {"item": old["logical_id"], "expected_version": rev["version_id"] + 1000, "old_span": SPAN,
         "mode": "supersede"},  # integer form, a version that is not the head
        _upd(rev, replacement=REPL, expected_version=old["version_id"]),  # clue vs expected_version
    ]  # fmt: skip
    outcomes = []
    for spec in specs:
        ack = await _write(connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[spec])], deps)
        assert len(ack["versions"]) == 1  # the new memory is ALWAYS written
        (u,) = ack["updates"]
        assert u["status"] == "rejected" and u["hint"]
        outcomes.append((u["code"], u["reason"], u.get("current_clue")))
    assert outcomes == [
        ("E_VERSION_CONFLICT", "version_conflict", f"v{rev['version_id']}"),
        ("E_VERSION_CONFLICT", "version_conflict", f"v{rev['version_id']}"),
        ("E_VERSION_CONFLICT", "version_conflict", f"v{rev['version_id']}"),
        ("E_INVALID_ARG", "expected_version_mismatch", None),
    ]
    assert await _rows(connect, old["logical_id"]) == state and await _links(connect) == links
    # the integer form with the CURRENT version applies (the hlm CLI's spec-literal form)
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                *NEW_TTL,
                updates=[
                    {
                        "item": old["logical_id"],
                        "expected_version": rev["version_id"],
                        "old_span": SPAN,
                        "mode": "revise",
                        "replacement": REPL,
                    }
                ],
            )
        ],
        deps,
    )
    assert ack["updates"][0]["status"] == "applied"
    await _replay_identical(connect)


@pytest.mark.parametrize("old_span", ["cache", "for every endpoint", "The"])
async def test_b3_an_ambiguous_old_span_is_refused(connect, world, deps, old_span: str) -> None:  # noqa: ANN001
    """A revise ``old_span`` that occurs more than once in the memory cannot say WHICH statement is
    outdated: refused (``span_not_unique``, hint: quote more words); nothing is revised."""
    body = (
        "The API cache TTL is 60 seconds for every endpoint.\n"
        "The cache backend is Redis 7 for every endpoint.\n"
        "The cache keys are prefixed."
    )
    old = await _old(connect, world, deps, body=body)
    state = await _rows(connect, old["logical_id"])
    ack = await _write(
        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(old, old_span, replacement=REPL)])], deps
    )
    (u,) = ack["updates"]
    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", "span_not_unique")
    assert "more words" in u["hint"]
    assert len(ack["versions"]) == 1
    assert await _rows(connect, old["logical_id"]) == state and await _links(connect) == []
    await _replay_identical(connect)


@pytest.mark.parametrize("kind", ["episode", "session_note"])
async def test_b3_a_historical_kind_gets_a_link_only(connect, world, deps, kind: str) -> None:  # noqa: ANN001
    """D-113: a session log keeps its text and validity in BOTH modes; the update only adds a
    ``supersedes`` link (``linked``), part-scope for revise, whole for supersede."""
    target = await _old(connect, world, deps, kind=kind, body="Session: " + OLD[1])
    state = await _rows(connect, target["logical_id"])
    ack = await _write(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(*NEW_TTL, updates=[_upd(target, replacement=REPL)]),
            item("Gone", "The cache backend was removed.", updates=[_upd(target, "Redis 7", "supersede")]),
        ],
        deps,
    )
    # two updates of one target in one batch are a conflict: send them one at a time
    assert [u["reason"] for u in ack["updates"]] == ["batch_conflict", "batch_conflict"]
    for spec, scope in (
        (_upd(target, replacement=REPL), "part"),
        (_upd(target, "Redis 7", "supersede"), "whole"),
    ):
        ack = await _write(connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[spec])], deps)
        (u,) = ack["updates"]
        assert (u["status"], u["reason"]) == ("linked", "historical_kind") and "code" not in u
        assert (await _links(connect))[-1][5]["scope"] == scope
    assert await _rows(connect, target["logical_id"]) == state  # text and validity untouched
    await _replay_identical(connect)
