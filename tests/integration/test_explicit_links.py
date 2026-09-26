"""D-184 (A): explicit supersession links on a real database (``ops/explicit_links``, ``hlm links``).

* ``apply`` writes the proposals as ``supersedes`` links through the librarian's evented link path:
  ONE ``librarian`` event whose ``resolved.mutations`` are the ``link_insert`` records, the link
  rows carry that event id and ``props {by: explicit, scope, quote, marker}``; no version changes.
* It is idempotent (a second run writes nothing and records no event) and replayable (a
  projection rebuild from ``events`` is identical).
* ``memory.query``'s D-057 read rule then hides the superseded draft when both items are hits;
  ``revert`` supersedes the links again (evented, replayable), and an as-of read before the revert
  still hides the draft (bi-temporal: nothing is deleted).
* The CLI ``hlm links explicit`` drives the same path (``--dry-run`` writes nothing).

No LLM anywhere: the pass is deterministic and the librarian never runs.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hlmemo.core.read_service import query
from hlmemo.core.write_service import default_deps
from hlmemo.db.replay import rebuild_projections
from hlmemo.ops import explicit_links as xl
from tests.integration._librarian_fixtures import seed_reserved
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import embed, write_items
from tests.integration._write_fixtures import MAIN, World, dump_projections, item, seed_world

pytestmark = pytest.mark.integration

SHA = "0" * 64
SPEC_PATH = "docs/decisions/PHASE0-SPEC.md"
DRAFT_PATH = "docs/consults/03-claude-phase0-spec.md"
CODEX_PATH = "docs/consults/03-codex-phase0-design.md"
SPEC0 = (
    "# Phase-0 Specification (authoritative)\n\n"
    f"Status: MERGED 2026-09-22 from `{DRAFT_PATH}` (Claude Plan agent) and `{CODEX_PATH}` (codex).\n\n"
    "The retrieval guidance names the top five hits to drill; previews are excerpts.\n"
)
SPEC1 = "## 4. Retrieval\n\nThe retrieval constants are fixed; drill the top five hits.\n"
DRAFT = "# Phase-0 draft\n\nThe retrieval guidance names the top three hits to drill.\n"
CODEX = "1. POSTGRES DDL\n\nHalf-open intervals; infinity ends.\n"
FILLER = "# Runbook\n\nBackups run nightly; restore drills are monthly.\n"
Q = "retrieval guidance hits to drill"


def doc_item(title: str, body: str, path: str | None) -> dict[str, Any]:
    kw: dict[str, Any] = {"kind": "doc_chunk"}
    if path is not None:
        kw["source"] = {"system": "doc", "path": path, "sha256": SHA}
    return item(title, body, **kw)


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _seed(connect, world: World) -> dict[str, Any]:  # noqa: ANN001
    items = [
        doc_item("PHASE0-SPEC", SPEC0, f"{SPEC_PATH}#0"),
        doc_item("PHASE0-SPEC retrieval", SPEC1, f"{SPEC_PATH}#1"),
        doc_item("Claude phase-0 draft", DRAFT, f"{DRAFT_PATH}#0"),
        doc_item("Codex phase-0 design", CODEX, f"{CODEX_PATH}#0"),
        doc_item("Runbook", FILLER, None),
    ]
    acks = await write_items(connect, world.ctx_a, MAIN, items, deps=default_deps())
    return {items[a.index]["title"]: a for a in acks}


async def _apply(connect, *, dry_run: bool = False, revert: bool = False) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await (xl.revert if revert else xl.apply)(conn, MAIN, dry_run=dry_run)
        await (conn.rollback() if dry_run else conn.commit())
    return out


async def _rows(connect, sql: str, *params: Any) -> list[tuple[Any, ...]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        await conn.commit()
    return rows


async def _replay_identical(connect, *, embedded: bool = False) -> None:  # noqa: ANN001
    """A projection rebuild from ``events`` is identical. ``embedded``: the embed jobs were drained,
    and replay re-queues them by design (vectors are derived, not evented): compare the rest."""
    async with await connect() as conn:
        before = await dump_projections(conn)
        await conn.commit()
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_projections(conn)
        await conn.commit()
    if embedded:
        before.pop("jobs"), after.pop("jobs")
    assert before == after and before["links"]


async def test_apply_writes_evented_links_idempotent_and_replayable(connect, world: World) -> None:  # noqa: ANN001
    v = await _seed(connect, world)
    titles = ("PHASE0-SPEC", "PHASE0-SPEC retrieval", "Claude phase-0 draft", "Codex phase-0 design")
    spec0, spec1, draft, codex = (v[t] for t in titles)
    versions_before = await _rows(connect, "SELECT t::text FROM memory_versions t ORDER BY version_id")
    events_before = (await _rows(connect, "SELECT count(*) FROM events"))[0][0]

    dry = await _apply(connect, dry_run=True)
    assert dry["dry_run"] and dry["applied"] == 0 and len(dry["proposals"]) == 4
    assert (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == 0
    assert (await _rows(connect, "SELECT count(*) FROM events"))[0][0] == events_before

    out = await _apply(connect)
    assert out["applied"] == 4 and out["stale"] == 0 and out["event_id"] is not None
    expected = {
        (s.logical_id, d.logical_id) for s in (spec0, spec1) for d in (draft, codex)
    }  # every spec item supersedes both pre-merge drafts
    links = await _rows(
        connect,
        "SELECT src_logical_id, dst_logical_id, props, source_event_id, dst_version_id, valid_to = 'infinity'"
        " FROM links WHERE rel = 'supersedes' ORDER BY link_id",
    )
    assert {(s, d) for s, d, *_ in links} == expected
    for _s, _d, props, event_id, dst_version, open_ended in links:
        assert event_id == out["event_id"] and dst_version is None and open_ended
        assert props["by"] == "explicit" and props["scope"] == "whole" and props["marker"] == "merged_from"
        assert props["quote"].startswith("Status: MERGED 2026-09-22 from") and props["quote"] in SPEC0
    ((kind, payload, device_id, client),) = await _rows(
        connect, "SELECT kind, payload, device_id, client FROM events WHERE event_id = %s", out["event_id"]
    )
    assert kind == "librarian" and device_id == xl.OPERATOR_DEVICE_ID and client == xl.CLIENT
    assert payload["request"]["op"] == xl.OP and len(payload["request"]["proposals"]) == 4
    muts = payload["resolved"]["mutations"]
    assert [m["op"] for m in muts] == ["link_insert"] * 4 and all(m["rel"] == "supersedes" for m in muts)
    # links only: no version was closed, superseded or altered
    assert (
        await _rows(connect, "SELECT t::text FROM memory_versions t ORDER BY version_id") == versions_before
    )

    again = await _apply(connect)
    assert again["applied"] == 0 and again["already_linked"] == 4 and again["proposals"] == []
    assert again["event_id"] is None
    assert (await _rows(connect, "SELECT count(*) FROM events"))[0][0] == events_before + 1
    assert (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == 4

    await _replay_identical(connect)


async def test_memory_query_hides_the_superseded_draft_and_revert_is_evented(
    connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    v = await _seed(connect, world)
    await embed(connect, embedder)

    async def hits(**as_of: str) -> list[str]:
        async with await connect() as conn:
            out = await query(
                conn,
                world.ctx_a,
                {"project": MAIN, "query": Q, "token_budget": 2000, **as_of},
                deps=read_deps,
            )
            await conn.commit()
        return [h["title"] for h in out["hits"]]

    before = await hits()
    assert "PHASE0-SPEC" in before and "Claude phase-0 draft" in before  # both are hits

    applied = await _apply(connect)
    assert applied["applied"] == 4
    after = await hits()
    assert "PHASE0-SPEC" in after and "Claude phase-0 draft" not in after  # D-057: hidden
    ((applied_at,),) = await _rows(
        connect, "SELECT occurred_at FROM events WHERE event_id = %s", applied["event_id"]
    )

    reverted = await _apply(connect, revert=True)
    assert reverted["reverted"] == 4 and reverted["event_id"] is not None
    ((payload,),) = await _rows(
        connect, "SELECT payload FROM events WHERE event_id = %s", reverted["event_id"]
    )
    assert payload["request"]["op"] == xl.OP_REVERT
    assert [m["op"] for m in payload["resolved"]["mutations"]] == ["link_supersede"] * 4
    assert (await _rows(connect, "SELECT count(*) FROM links WHERE superseded_at = 'infinity'"))[0][0] == 0
    assert (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == 4  # superseded, never deleted
    assert "Claude phase-0 draft" in await hits()  # the draft is back
    as_of = applied_at.isoformat()  # known before the revert: the link still holds (bi-temporal)
    assert "Claude phase-0 draft" not in await hits(known_at=as_of)
    assert v["Claude phase-0 draft"].version_id  # the draft item itself was never touched

    await _replay_identical(connect, embedded=True)  # the reversal replays too
    reapplied = await _apply(connect)  # a reverted pass can be applied again (new link rows)
    assert reapplied["applied"] == 4 and (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == 8


async def test_cli_dry_run_then_apply(db_dsn: str, connect, world: World) -> None:  # noqa: ANN001
    from hlmemo.cli.hlm import app

    await _seed(connect, world)
    runner = CliRunner()
    args = ["links", "explicit", "--project", MAIN, "--dsn", db_dsn, "--json"]
    dry = await asyncio.to_thread(runner.invoke, app, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    out = json.loads(dry.stdout)
    assert out["dry_run"] and len(out["proposals"]) == 4 and out["applied"] == 0
    assert {(p["source_ref"], p["target_ref"], p["scope"], p["marker"]) for p in out["proposals"]} == {
        (SPEC_PATH, DRAFT_PATH, "whole", "merged_from"),
        (SPEC_PATH, CODEX_PATH, "whole", "merged_from"),
    }
    assert (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == 0

    done = await asyncio.to_thread(runner.invoke, app, args)
    assert done.exit_code == 0, done.output
    assert json.loads(done.stdout)["applied"] == 4
    assert (await _rows(connect, "SELECT count(*) FROM links WHERE props->>'by' = 'explicit'"))[0][0] == 4

    human = await asyncio.to_thread(
        runner.invoke, app, ["links", "explicit", "--project", MAIN, "--dsn", db_dsn]
    )
    assert human.exit_code == 0 and "already_linked=4" in human.output

    missing = await asyncio.to_thread(
        runner.invoke, app, ["links", "explicit", "--project", "nope", "--dsn", db_dsn]
    )
    assert missing.exit_code == 64
