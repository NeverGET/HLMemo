"""R4 (R-1, R-7, R-12): ``hlm links backfill`` apply/revert on a real database (LLM-free; the
automatic proposer of wf-supersede-backfill is not ported).

* ``apply`` writes a reviewed proposal through the evented link path: ONE ``librarian`` event (client
  ``hlm-backfill``, op ``supersede_backfill``), ``props {by: backfill, scope, quote, span,
  declaration, ...}`` with ``quote`` = the older span for a part-scope link (the read contract).
  Idempotent and replayable; ``--dry-run`` rolls back.
* R-1: under the endpoint locks BOTH heads must be the proposal's versions AND belong to the
  requested project, for EVERY record of the file (before the ``already_linked`` filter); any stale
  or foreign record rejects the WHOLE apply (zero events, zero links).
* Astra 90 N-3 / Sol 90 N-2, N-3: the endpoint locks come FIRST and every check runs under them;
  the cycle check sees the live graph beyond the endpoints (an intermediate item).
* ``revert`` needs ``--project`` and supersedes every live backfill link of the project in ONE
  event (project-wide); apply -> revert round-trips and replays deterministically.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import subprocess
import sys
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


async def test_an_already_linked_foreign_pair_rejects_the_whole_apply(  # noqa: ANN001
    db_dsn: str, connect, world: World, tmp_path: Path
) -> None:
    """Astra 90 R-1: the validation covers the WHOLE file BEFORE the ``already_linked`` filter. A
    valid MAIN proposal plus a pair labelled MAIN whose heads live in OTHER and which a live link
    already joins (so it would be skipped as ``already_linked``): exit 65, zero events, zero links."""
    from hlmemo.cli.hlm import app

    mine = await _seed(connect, world)
    foreign = await _seed(connect, world, project=OTHER, suffix=" (other)")
    theirs = _proposal(
        foreign["D-021"], foreign["D-011"], "The restore drill runs yearly", "now runs monthly", project=OTHER
    )
    async with await connect() as conn:  # OTHER's own, legitimate link
        assert (await bf.apply(conn, OTHER, [theirs]))["applied"] == 1
        await conn.commit()
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(
        "\n".join(
            json.dumps(r)
            for r in (_proposal(mine["D-020"], mine["D-010"], SPAN, QUOTE), {**theirs, "project": MAIN})
        )
        + "\n"
    )
    before = await _counts(connect)
    res = await asyncio.to_thread(
        CliRunner().invoke,
        app,
        [
            "links",
            "backfill",
            "--project",
            MAIN,
            "--dsn",
            db_dsn,
            "--json",
            "--apply",
            "--proposals",
            str(mixed),
        ],
    )
    assert res.exit_code == 65, res.output
    out = json.loads(res.stdout)
    assert out["rejected"] is True and out["stale"] == [] and out["validated"] == 2
    assert [f["src_vid"] for f in out["foreign"]] == [foreign["D-021"].version_id]
    assert await _counts(connect) == before  # the valid MAIN pair was not written either


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


@pytest.mark.parametrize(
    ("live", "proposed"),
    [
        ([("A", "B"), ("B", "C")], [("C", "A")]),  # Sol 90 N-2
        ([("C", "B"), ("B", "A")], [("A", "C")]),  # Astra 90 N-3 (the order variant)
        ([("A", "B"), ("B", "C")], [("C", "D"), ("D", "A")]),  # live links PLUS another proposal
    ],
    ids=["sol-n2", "astra-n3", "live-plus-proposal"],
)
async def test_a_cycle_through_the_live_graph_is_dropped(  # noqa: ANN001
    connect, world: World, live: list[tuple[str, str]], proposed: list[tuple[str, str]]
) -> None:
    """Astra 90 N-3 / Sol 90 N-2: the cycle check sees the live ``supersedes`` graph beyond the
    proposal's endpoints (an intermediate item B): a proposal whose ``dst`` reaches its ``src``
    through live links (plus the file's other proposals) writes zero events and zero links."""
    v = await _seed(connect, world)
    node = {"A": v["D-010"], "B": v["D-020"], "C": v["D-011"], "D": v["D-021"]}

    def prop(s: str, d: str) -> dict[str, Any]:
        return _proposal(node[s], node[d], f"span {s}{d}", f"quote {s}{d}")

    first = await _write(connect, bf.apply, [prop(s, d) for s, d in live])
    assert first["applied"] == len(live)
    before = await _counts(connect)
    out = await _write(connect, bf.apply, [prop(s, d) for s, d in proposed])
    assert out["applied"] == 0 and out["event_id"] is None and out["links"] == []
    assert out["dropped"]["cycle_with_live"] == len(proposed) and out["already_linked"] == 0
    assert await _counts(connect) == before


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


async def _link_under_the_endpoint_locks(conn, world: World, src: Any, dst: Any) -> None:  # noqa: ANN001
    """A concurrent explicit/librarian-style writer: the SAME per-item locks, then one evented
    ``supersedes`` link ``src -> dst`` (left uncommitted: the caller commits)."""
    from hlmemo.db import write_queries as q
    from hlmemo.librarian.actor import materialize
    from hlmemo.ops import explicit_links as xl

    await q.lock_logical_ids(conn, [src.logical_id, dst.logical_id])
    action = {
        "op": "link_insert",
        "rel": "supersedes",
        "src_logical_id": src.logical_id,
        "dst_logical_id": dst.logical_id,
        "dst_version_id": None,
        "valid_from": "2026-09-23T00:00:00.000000Z",
        "props": {"by": "explicit", "scope": "whole", "quote": "concurrent"},
        "assessed": {str(src.logical_id): src.version_id, str(dst.logical_id): dst.version_id},
    }
    records, _ = await materialize(conn, None, None, [action])
    assert len(records) == 1
    await xl._record(conn, world.main_id, {"actor": "test", "op": "concurrent_writer"}, records)


async def test_a_concurrent_reverse_link_is_seen_under_the_locks(connect, world: World) -> None:  # noqa: ANN001
    """Sol 90 N-3: transaction 1 holds the endpoint locks and commits ``B -> A``; the backfill of
    ``A -> B`` waits for those locks, THEN reads the live links and runs its checks: it writes
    nothing (no 2-cycle, no duplicate)."""
    v = await _seed(connect, world)
    a, b = v["D-020"], v["D-010"]
    before = await _counts(connect)
    holder = await connect()
    try:
        await _link_under_the_endpoint_locks(holder, world, b, a)  # B -> A, uncommitted, locks held
        backfill = asyncio.create_task(_write(connect, bf.apply, [_proposal(a, b, SPAN, QUOTE)]))
        async with await connect() as probe:  # the backfill is blocked on an endpoint lock
            for _ in range(200):
                cur = await probe.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                    " AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
                )
                (waiting,) = await cur.fetchone()
                await probe.commit()
                if waiting or backfill.done():
                    break
                await asyncio.sleep(0.05)
        assert waiting == 1 and not backfill.done()
        await holder.commit()
    finally:
        await holder.close()
    out = await asyncio.wait_for(backfill, 30)
    assert out["applied"] == 0 and out["event_id"] is None and out["links"] == []
    assert out["already_linked"] == 1  # the live B -> A joins the pair: never a second, reverse edge
    live = await _rows(
        connect,
        "SELECT src_logical_id, dst_logical_id FROM links"
        " WHERE rel = 'supersedes' AND superseded_at = 'infinity' ORDER BY link_id",
    )
    assert live == [(b.logical_id, a.logical_id)]  # no 2-cycle, no duplicate
    assert await _counts(connect) == (before[0] + 1, before[1] + 1)  # the holder's link and event only


# ------------------------------------------------------------------ the RUNBOOK's preview commands
RUNBOOK = Path(__file__).resolve().parents[2] / "deploy" / "RUNBOOK.md"


def _runbook_hlm(marker: str) -> list[str]:
    """The ``hlm links backfill ...`` argv (without ``hlm``) of the RUNBOOK line holding ``marker``,
    exactly as documented (the ssh/docker wrapper stripped)."""
    (line,) = [ln for ln in RUNBOOK.read_text().splitlines() if marker in ln]
    cmd = re.search(r"exec -T api (hlm links backfill [^']*)'", line)
    assert cmd, line
    return shlex.split(cmd.group(1))[1:]


def _runbook_python(json_name: str) -> str:
    """The documented ``python3 -c`` PASS predicate of the preview whose JSON is ``json_name``."""
    (line,) = [ln for ln in RUNBOOK.read_text().splitlines() if "python3 -c" in ln and json_name in ln]
    return shlex.split(line.strip())[2]


def _runbook_counts_sql() -> str:
    (line,) = [ln for ln in RUNBOOK.read_text().splitlines() if ln.startswith("psql ") and "--command=" in ln]
    return shlex.split(line)[-1].removeprefix("--command=")


async def test_the_runbook_preview_commands_run_as_documented(  # noqa: ANN001
    db_dsn: str, connect, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Astra 90 N-2 / Sol 90 N-1: the RUNBOOK's apply and revert previews, taken from the RUNBOOK
    text (the real ``--dry-run`` flag; no ``--preview`` anywhere), run through the CLI with the DSN
    from HLM_DB_DSN as in the api container: exit 0, the documented PASS predicate holds
    (len(links) == the approved count / the live backfill link count of the ``counts`` query) and
    the event and link counts are unchanged."""
    from hlmemo.cli.hlm import app

    text = RUNBOOK.read_text()
    assert "--preview" not in text and "--dry-run --json" in text
    v = await _seed(connect, world)
    records = [
        _proposal(v["D-020"], v["D-010"], SPAN, QUOTE),
        _proposal(v["D-021"], v["D-011"], "The restore drill runs yearly", "now runs monthly"),
    ]
    approved = len(bf.candidates(records, MAIN))
    proposals = tmp_path / "r4-links.jsonl"
    proposals.write_text("".join(json.dumps(r) + "\n" for r in records))
    sql = _runbook_counts_sql().replace("'hlmemo'", f"'{MAIN}'")

    def argv(marker: str) -> list[str]:
        subst = {"hlmemo": MAIN, "/tmp/r4-links.jsonl": str(proposals)}
        return [subst.get(a, a) for a in _runbook_hlm(marker)]

    async def counts() -> tuple[int, ...]:
        ((events, links, backfill),) = await _rows(connect, sql)
        return int(events), int(links), int(backfill)

    monkeypatch.setenv("HLM_DB_DSN", db_dsn)
    runner = CliRunner()

    async def run(args: list[str], save_as: str | None = None) -> dict[str, Any]:
        res = await asyncio.to_thread(runner.invoke, app, args)
        assert res.exit_code == 0, res.output
        if save_as is not None:  # the RUNBOOK saves the preview JSON for its predicate
            await asyncio.to_thread((tmp_path / save_as).write_text, res.stdout)
        return json.loads(res.stdout) if "--json" in args else {}

    def predicate(json_name: str, expected: int) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", _runbook_python(json_name), str(tmp_path / json_name), str(expected)],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0 and proc.stdout.startswith("preview PASS"), proc.stdout + proc.stderr

    apply_preview = argv("--apply --proposals /tmp/r4-links.jsonl --dry-run --json")
    assert apply_preview[-2:] == ["--dry-run", "--json"] and "--apply" in apply_preview
    before = await counts()
    out = await run(apply_preview, "r4-links-preview.json")
    assert out["preview"] is True and out["applied"] == 0 and len(out["links"]) == approved == 2
    predicate("r4-links-preview.json", approved)
    assert await counts() == before and before[2] == 0

    await run(argv("--apply --proposals /tmp/r4-links.jsonl' "))  # the documented apply
    live = await counts()
    assert live[2] == approved and live[1] == before[1] + approved

    revert_preview = argv("--revert --dry-run --json")
    assert revert_preview[-3:] == ["--revert", "--dry-run", "--json"]
    out = await run(revert_preview, "r4-revert-preview.json")
    assert out["preview"] is True and out["reverted"] == 0 and len(out["links"]) == live[2]
    predicate("r4-revert-preview.json", live[2])
    assert await counts() == live  # nothing written by the revert preview
