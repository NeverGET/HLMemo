"""R4 (R-1, R-7, R-12): ``hlm links backfill`` apply/revert on a real database (LLM-free; the
automatic proposer of wf-supersede-backfill is not ported).

* ``apply`` writes a reviewed proposal through the evented link path: ONE ``librarian`` event (client
  ``hlm-backfill``, op ``supersede_backfill``), ``props {by: backfill, scope, quote, span,
  declaration, ...}`` with ``quote`` = the older span for a part-scope link (the read contract).
  Idempotent and replayable; ``--dry-run`` rolls back.
* R-1: under the endpoint locks BOTH heads must be the proposal's versions AND belong to the
  requested project; any stale or foreign pair rejects the WHOLE apply (zero events, zero links).
* ``revert`` needs ``--project`` and supersedes every live backfill link of the project in ONE
  event (project-wide); apply -> revert round-trips and replays deterministically.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hlmemo.core.write_service import default_deps
from hlmemo.db import librarian_queries as lq
from hlmemo.db.replay import rebuild_projections
from hlmemo.librarian.tasks.research import quote_overlaps
from hlmemo.ops import backfill_links as bf
from tests.integration._librarian_fixtures import seed_reserved
from tests.integration._w2b_fixtures import write_items
from tests.integration._write_fixtures import MAIN, OTHER, World, dump_projections, item, seed_world

pytestmark = pytest.mark.integration

OLD = "D-010 | 2026-09-20 | ACCEPTED | Backups run nightly at 02:00 on the VPS; seven copies are kept."
NEW = "D-020 | 2026-09-22 | ACCEPTED | Backups now run hourly (amends D-010); seven copies are kept."
OLD2 = "D-011 | 2026-09-20 | ACCEPTED | The restore drill runs yearly."
NEW2 = "D-021 | 2026-09-23 | ACCEPTED | The restore drill now runs monthly (amends D-011)."
SPAN = "Backups run nightly at 02:00 on the VPS"
QUOTE = "Backups now run hourly"


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _seed(connect, world: World, project: str = MAIN, suffix: str = "") -> dict[str, Any]:  # noqa: ANN001
    items = [
        item(f"D-010 · backups nightly{suffix}", OLD + suffix, valid_from="2026-09-19T21:00:00Z"),
        item(f"D-020 · backups hourly{suffix}", NEW + suffix, valid_from="2026-09-21T21:00:00Z"),
        item(f"D-011 · restore yearly{suffix}", OLD2 + suffix, valid_from="2026-09-19T21:00:00Z"),
        item(f"D-021 · restore monthly{suffix}", NEW2 + suffix, valid_from="2026-09-22T21:00:00Z"),
    ]
    acks = await write_items(connect, world.ctx_a, project, items, deps=default_deps())
    return {items[a.index]["title"].split(" · ")[0]: a for a in acks}


def _proposal(new: Any, old: Any, span: str, quote: str, project: str = MAIN, **kw: Any) -> dict[str, Any]:
    return {
        "project": project,
        "status": "proposed",
        "src_vid": new.version_id,
        "dst_vid": old.version_id,
        "src_logical_id": new.logical_id,
        "dst_logical_id": old.logical_id,
        "scope": "part",
        "relation": "supersedes_part",
        "older_span": span,
        "newer_quote": quote,
        "confidence": 0.95,
        "model": "curated",
        "profile": "curated-manual",
        "prompt_version": None,
        "generator": None,
        **kw,
    }


async def _rows(connect, sql: str, *params: Any) -> list[tuple[Any, ...]]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        await conn.commit()
    return rows


async def _write(connect, fn, *args: Any, **kw: Any) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        try:
            out = await fn(conn, MAIN, *args, **kw)
        except BaseException:
            await conn.rollback()
            raise
        await (conn.rollback() if kw.get("preview") else conn.commit())
    return out


async def _counts(connect) -> tuple[int, int]:  # noqa: ANN001
    ((links, events),) = await _rows(
        connect, "SELECT (SELECT count(*) FROM links), (SELECT count(*) FROM events WHERE kind = 'librarian')"
    )
    return int(links), int(events)


async def _replay_identical(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        before = await dump_projections(conn)
        await conn.commit()
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_projections(conn)
        await conn.commit()
    before.pop("jobs"), after.pop("jobs")  # embed jobs are re-queued by design (vectors are derived)
    assert before == after and before["links"]


async def test_apply_then_revert_round_trips_and_replays(connect, world: World) -> None:  # noqa: ANN001
    v = await _seed(connect, world)
    records = [
        _proposal(v["D-020"], v["D-010"], SPAN, QUOTE),
        _proposal(v["D-021"], v["D-011"], "The restore drill runs yearly", "now runs monthly"),
    ]
    base = await _counts(connect)
    preview = await _write(connect, bf.apply, records, preview=True)
    assert len(preview["links"]) == 2 and preview["applied"] == 0 and await _counts(connect) == base

    applied = await _write(connect, bf.apply, records)
    assert applied["applied"] == 2 and applied["event_id"] is not None
    assert await _counts(connect) == (base[0] + 2, base[1] + 1)  # ONE event for the whole apply
    rows = await _rows(
        connect, "SELECT src_logical_id, dst_logical_id, props, source_event_id FROM links ORDER BY link_id"
    )
    (src, dst, props, event_id) = rows[0]
    assert (src, dst, event_id) == (v["D-020"].logical_id, v["D-010"].logical_id, applied["event_id"])
    assert props["by"] == "backfill" and props["scope"] == "part" and props["profile"] == "curated-manual"
    assert props["quote"] == SPAN == props["span"] and props["declaration"] == QUOTE
    ((client, payload),) = await _rows(
        connect, "SELECT client, payload FROM events WHERE event_id = %s", applied["event_id"]
    )
    assert client == bf.CLIENT and payload["request"]["op"] == bf.OP
    assert [m["op"] for m in payload["resolved"]["mutations"]] == ["link_insert", "link_insert"]
    # the read side (D-184) finds the superseded statement in the older item's text
    async with await connect() as conn:
        now = (await (await conn.execute("SELECT now()")).fetchone())[0]
        links = await lq.supersessions_of(
            conn, [v["D-010"].logical_id], pid=world.main_id, scopes=["all"], valid_at=now, known_at=now
        )
        await conn.commit()
    assert links == [(v["D-020"].logical_id, v["D-010"].logical_id, True, SPAN)] and quote_overlaps(SPAN, OLD)

    again = await _write(connect, bf.apply, records)
    assert again["applied"] == 0 and again["already_linked"] == 2 and again["event_id"] is None
    await _replay_identical(connect)

    reverted = await _write(connect, bf.revert)
    assert reverted["reverted"] == 2 and reverted["event_id"] is not None
    ((payload,),) = await _rows(
        connect, "SELECT payload FROM events WHERE event_id = %s", reverted["event_id"]
    )
    assert payload["request"]["op"] == bf.OP_REVERT
    assert (await _rows(connect, "SELECT count(*) FROM links WHERE superseded_at = 'infinity'"))[0][0] == 0
    assert (await _rows(connect, "SELECT count(*) FROM links"))[0][0] == base[0] + 2  # superseded, kept
    await _replay_identical(connect)


async def test_a_cross_project_proposal_rejects_the_whole_apply(connect, world: World) -> None:  # noqa: ANN001
    """R-1: a record labelled for MAIN whose heads live in OTHER (valid ids of another project) is
    refused under the locks, and with it the valid MAIN pair of the same file: nothing is written."""
    mine = await _seed(connect, world)
    foreign = await _seed(connect, world, project=OTHER, suffix=" (other)")
    records = [
        _proposal(mine["D-020"], mine["D-010"], SPAN, QUOTE),
        _proposal(foreign["D-021"], foreign["D-011"], "The restore drill runs yearly", "now runs monthly"),
    ]
    before = await _counts(connect)
    with pytest.raises(bf.BackfillRejected) as exc:
        await _write(connect, bf.apply, records)
    details = exc.value.details
    assert details["stale"] == [] and len(details["foreign"]) == 1
    assert details["foreign"][0]["src_vid"] == foreign["D-021"].version_id
    assert world.main_id not in details["foreign"][0]["src_projects"]
    assert await _counts(connect) == before  # zero events, zero links


async def test_a_stale_head_rejects_the_whole_apply(connect, world: World) -> None:  # noqa: ANN001
    v = await _seed(connect, world)
    records = [
        _proposal(v["D-020"], v["D-010"], SPAN, QUOTE),
        _proposal(v["D-021"], v["D-011"], "The restore drill runs yearly", "now runs monthly"),
    ]
    old = v["D-011"]
    await write_items(
        connect,
        world.ctx_a,
        MAIN,
        [
            item(
                "D-011 · restore yearly",
                OLD2 + " Revised.",
                logical_id=old.logical_id,
                expected_version_id=old.version_id,
            )
        ],
        deps=default_deps(),
    )
    before = await _counts(connect)
    with pytest.raises(bf.BackfillRejected) as exc:
        await _write(connect, bf.apply, records)
    (stale,) = exc.value.details["stale"]
    assert stale["dst_vid"] == old.version_id and stale["dst_head"] != old.version_id
    assert await _counts(connect) == before  # the valid D-020 pair was not written either


async def test_revert_is_project_wide(connect, world: World) -> None:  # noqa: ANN001
    v = await _seed(connect, world)
    first = await _write(connect, bf.apply, [_proposal(v["D-020"], v["D-010"], SPAN, QUOTE)])
    second = await _write(
        connect,
        bf.apply,
        [_proposal(v["D-021"], v["D-011"], "The restore drill runs yearly", "now runs monthly")],
    )
    assert first["event_id"] != second["event_id"]
    reverted = await _write(connect, bf.revert)
    assert reverted["reverted"] == 2  # both applies' links, in ONE event


async def test_cli(db_dsn: str, connect, world: World, tmp_path: Path) -> None:  # noqa: ANN001
    from hlmemo.cli.hlm import app

    v = await _seed(connect, world)
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps(_proposal(v["D-020"], v["D-010"], SPAN, QUOTE)) + "\n")
    foreign = await _seed(connect, world, project=OTHER, suffix=" (other)")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps(_proposal(foreign["D-021"], foreign["D-011"], "The restore drill runs yearly", "monthly"))
        + "\n"
    )
    runner = CliRunner()
    base = ["links", "backfill", "--project", MAIN, "--dsn", db_dsn, "--json"]

    async def run(*args: str):  # noqa: ANN202
        return await asyncio.to_thread(runner.invoke, app, list(args))

    before = await _counts(connect)
    dry = await run(*base, "--dry-run", "--proposals", str(good))
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.stdout)["preview"] and len(json.loads(dry.stdout)["links"]) == 1
    assert await _counts(connect) == before
    rejected = await run(*base, "--apply", "--proposals", str(bad))
    assert rejected.exit_code == 65 and json.loads(rejected.stdout)["rejected"] is True
    assert await _counts(connect) == before
    done = await run(*base, "--apply", "--proposals", str(good))
    assert done.exit_code == 0 and json.loads(done.stdout)["applied"] == 1
    # R-7: --revert without --project is a usage error, never a default project
    no_project = await run("links", "backfill", "--dsn", db_dsn, "--revert")
    assert no_project.exit_code == 64
    both = await run(*base, "--apply", "--revert", "--proposals", str(good))
    assert both.exit_code == 64
    human = await run("links", "backfill", "--project", MAIN, "--dsn", db_dsn, "--revert")
    assert human.exit_code == 0 and "reverted=1" in human.output and "project-wide" in human.output
    missing = await run("links", "backfill", "--project", "nope", "--dsn", db_dsn, "--revert")
    assert missing.exit_code == 64
