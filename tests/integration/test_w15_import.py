"""W1.5 import + export against a real database (gates G-I1, G-I2, G-I3; source ownership; replay).

The importer runs exactly as ``hlm import`` does, with the MCP session replaced by an in-process
caller over the same services (``_import_fixtures.Caller``); the wire path of ``hlm.export`` is
covered by ``test_w15_wire.py``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hlmemo.cli.mcp_client import ToolCallError
from hlmemo.core.budget import Meter
from hlmemo.db.replay import rebuild_projections
from hlmemo.importers import exportfmt
from hlmemo.importers.cli import import_async, parse_source
from hlmemo.importers.runner import fetch_items, run_export
from tests.integration._import_fixtures import (
    FIXTURE,
    Caller,
    copy_fixture,
    make_device,
    make_project,
    rows,
    scalar,
)
from tests.integration._write_fixtures import dump_projections

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def meter() -> Meter:
    return Meter()


async def _run(call, root: Path, meter: Meter, *, dry_run: bool = False, project: str = "fx") -> dict:  # noqa: ANN001
    parsed = parse_source("markdown", [root / "docs"], base=root, tz=UTC)
    return await import_async(
        call, source="markdown", parsed=parsed, project=project, dry_run=dry_run, meter=meter
    )


async def _writes(connect, device_id: int) -> int:  # noqa: ANN001
    return await scalar(
        connect, "SELECT count(*) FROM events WHERE kind = 'write' AND device_id = %s", (device_id,)
    )


# --------------------------------------------------------------------------- G-I1
async def test_gi1_import_is_idempotent_and_revises_only_edits(connect, tmp_path, meter) -> None:
    root = copy_fixture(tmp_path)
    pid, _card = await make_project(connect, "fx")
    ctx = await make_device(connect, "importer", {pid: "write"})
    call = Caller(connect, ctx)

    # the online dry-run of an empty project equals the golden offline report (and writes nothing)
    dry = await _run(call, root, meter, dry_run=True)
    golden = json.loads((FIXTURE / "golden" / "markdown.json").read_text())
    assert dry == dict(golden, project="fx") and call.writes() == 0

    first = await _run(call, root, meter)
    assert first["counts"]["new"] == 9 and first["writes"]["written"] == 9 and not first["writes"]["failed"]
    assert await _writes(connect, ctx.device_id) == 9
    # temporal rule: recorded_at = server time; valid_from only from evidence, else import time
    got = {
        path: (vf, ra)
        for path, vf, ra in await rows(
            connect,
            "SELECT source->>'path', valid_from, recorded_at FROM memory_versions"
            " WHERE project_id = %s AND source IS NOT NULL",
            (pid,),
        )
    }
    now = datetime.now(UTC)
    assert got["docs/decisions/DECISIONS.md#D-001"][0] == datetime(2026, 1, 5, tzinfo=UTC)
    assert all(now - timedelta(minutes=5) < ra <= now for _vf, ra in got.values())
    vf_plain, ra_plain = got["docs/notes/plain.md"]
    assert abs((vf_plain - ra_plain).total_seconds()) < 1  # no evidence: import time, never mtime
    assert "docs/decisions/DECISIONS.md#D-003" not in got  # rejected per item

    # second run: 0 writes
    second = await _run(call, root, meter)
    assert second["counts"]["unchanged"] == 9 and second["counts"]["new"] == 0
    assert "writes" in second and second["writes"]["written"] == 0 and call.writes() == 9
    assert await _writes(connect, ctx.device_id) == 9

    # an edited file: exactly one revision of the same logical item
    plain = root / "docs" / "other" / "plain.md"  # unique content (notes/plain.md has a copy)
    before_lid = await scalar(
        connect, "SELECT logical_id FROM memory_versions WHERE source->>'path' = 'docs/other/plain.md'"
    )
    plain.write_text(plain.read_text() + "\nThe refresh takes about 20 minutes.\n")
    third = await _run(call, root, meter)
    assert third["counts"]["changed"] == 1 and third["counts"]["unchanged"] == 8
    assert third["writes"]["revisions"] == 1 and third["writes"]["written"] == 1
    assert await _writes(connect, ctx.device_id) == 10
    heads = await rows(
        connect,
        "SELECT DISTINCT logical_id FROM memory_versions WHERE source->>'path' = 'docs/other/plain.md'"
        " AND superseded_at = 'infinity'",
    )
    assert heads == [(before_lid,)]

    # an mtime-only change: 0 writes and no valid-time change
    valid_before = await rows(
        connect, "SELECT version_id, valid_from, valid_to::text FROM memory_versions ORDER BY 1"
    )
    future = (datetime.now() + timedelta(days=3)).timestamp()
    for f in root.rglob("*.md"):
        os.utime(f, (future, future))
    fourth = await _run(call, root, meter)
    assert fourth["counts"]["unchanged"] == 9 and fourth["writes"]["written"] == 0
    assert await _writes(connect, ctx.device_id) == 10
    assert (
        await rows(connect, "SELECT version_id, valid_from, valid_to::text FROM memory_versions ORDER BY 1")
        == valid_before
    )

    # a re-sent identical write is a request_id replay (an interrupted run resumes safely)
    items, _ = await fetch_items(call, "fx")
    assert len([i for i in items if i["kind"] != "project_card"]) == 9


async def test_gi1_missing_sources_are_reported_not_closed(connect, tmp_path, meter) -> None:
    root = copy_fixture(tmp_path)
    pid, _ = await make_project(connect, "fx")
    call = Caller(connect, await make_device(connect, "importer", {pid: "write"}))
    await _run(call, root, meter)
    (root / "docs" / "other" / "plain.md").unlink()
    rep = await _run(call, root, meter)
    assert rep["missing"] == ["markdown:docs/other/plain.md"] and rep["writes"]["written"] == 0
    assert (
        await scalar(
            connect,
            "SELECT count(*) FROM memory_versions WHERE source->>'path' = 'docs/other/plain.md'"
            " AND superseded_at = 'infinity' AND valid_to = 'infinity'",
        )
        == 1
    )


# --------------------------------------------------------------------------- G-I2
async def test_gi2_raw_shows_source_and_code_refs(connect, tmp_path, meter) -> None:
    root = copy_fixture(tmp_path)
    pid, _ = await make_project(connect, "fx")
    call = Caller(connect, await make_device(connect, "importer", {pid: "write"}))
    await _run(call, root, meter)
    vid = await scalar(
        connect,
        "SELECT version_id FROM memory_versions WHERE source->>'path' = 'docs/notes/frontmatter-date.md'",
    )
    res = await call("memory.raw", {"project": "fx", "version_id": vid, "token_budget": 4000})
    src = res["source"]
    assert src["system"] == "markdown" and src["path"] == "docs/notes/frontmatter-date.md"
    assert len(src["sha256"]) == 64 and "mtime" in src  # provenance only
    assert res["code_refs"] == [{"path": "src/app/main.py", "commit": None}]
    assert res["payload_item"]["describes"] == ["src/app/main.py"]
    assert await rows(connect, "SELECT path FROM code_refs WHERE version_id = %s", (vid,)) == [
        ("src/app/main.py",)
    ]


# --------------------------------------------------------------------------- G-I3
async def _seed_links(call, pid: int) -> None:  # noqa: ANN001
    """A cycle (relates_to both ways) and a card derived from two items: links must round-trip."""
    items, _ = await fetch_items(call, "fx")
    by_path = {(i.get("source") or {}).get("path"): i for i in items}
    a = by_path["docs/notes/plain.md"]
    b = by_path["docs/other/plain.md"]
    card = next(i for i in items if i["kind"] == "project_card")
    await call(
        "memory.write",
        {
            "project": "fx",
            "request_id": "8d4f7c52-4a1a-4a39-9d39-1c3c0b6b8f11",
            "client": "pytest/0",
            "items": [
                {
                    "kind": "fact",
                    "title": a["title"],
                    "body": "# Plain note\n\nThe staging database is refreshed from the nightly dump"
                    " every Monday at 04:00.\n",
                    "logical_id": a["logical_id"],
                    "expected_version_id": a["version_id"],
                    "tags": a["tags"],
                    "source": a["source"],
                    "valid_from": a["valid_from"],
                    "links": [{"rel": "relates_to", "target": b["logical_id"]}],
                },
                {
                    "kind": "fact",
                    "title": b["title"],
                    "body": "# Another plain note\n\nFeature flags live in the `flags` table and are"
                    " cached for 60 seconds.\n",
                    "logical_id": b["logical_id"],
                    "expected_version_id": b["version_id"],
                    "tags": b["tags"],
                    "source": b["source"],
                    "valid_from": b["valid_from"],
                    "links": [{"rel": "relates_to", "target": a["logical_id"]}],
                },
                {
                    "kind": "project_card",
                    "title": "Project card",
                    "body": "# Fixture\n\nSessions in Postgres (D-001); retries back off (D-002).\n",
                    "expected_version_id": card["version_id"],
                    "links": [
                        {"rel": "derived_from", "target": a["logical_id"]},
                        {"rel": "derived_from", "target": b["logical_id"]},
                    ],
                },
            ],
        },
    )


def _tree(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): exportfmt.strip_ids(p.read_text()) for p in sorted(d.rglob("*.md"))}


async def test_gi3_export_import_export_is_byte_identical(connect, tmp_path, meter) -> None:
    root = copy_fixture(tmp_path)
    pid, _ = await make_project(connect, "fx")
    pid2, _ = await make_project(connect, "fx-copy")
    ctx = await make_device(connect, "importer", {pid: "write", pid2: "write"})
    call = Caller(connect, ctx)
    await _run(call, root, meter)
    for source, paths in (
        ("automemory", [FIXTURE / "automemory"]),
        ("serena", [FIXTURE / "serena" / "memories"]),
    ):
        parsed = parse_source(source, paths, tz=UTC)
        rep = await import_async(call, source=source, parsed=parsed, project="fx", dry_run=False, meter=meter)
        assert not rep["writes"]["failed"]
    await _seed_links(call, pid)

    first = tmp_path / "export-a"
    res = await run_export(call, "fx", first)
    assert (
        res["card"]
        and res["items"] == 16
        and (first / "CARD.md").is_file()
        and (first / "INDEX.md").is_file()
    )

    parsed = parse_source("markdown", [first], base=first, tz=UTC)
    assert not parsed.rejected and [s.reason for s in parsed.skipped] == ["export-index"]
    rep = await import_async(
        call, source="markdown", parsed=parsed, project="fx-copy", dry_run=False, meter=meter
    )
    assert not rep["writes"]["failed"] and rep["writes"]["links_dropped"] == 0
    assert rep["counts"]["changed"] == 1  # the skeleton card of the fresh project is revised

    second = tmp_path / "export-b"
    await run_export(call, "fx-copy", second)
    assert _tree(second) == _tree(first)
    raw_a = {p.name: p.read_text() for p in first.rglob("*.md")}
    assert any(exportfmt.strip_ids(t) != t for t in raw_a.values())  # ids really were present

    # re-importing the same export into the copy is a no-op (logical ids map back)
    again = await import_async(
        call, source="markdown", parsed=parsed, project="fx-copy", dry_run=False, meter=meter
    )
    assert again["writes"]["written"] == 0 and again["counts"]["unchanged"] == 16


# --------------------------------------------------------------------------- source ownership
def _item(path: str, body: str, **kw) -> dict:  # noqa: ANN003
    import hashlib

    return {
        "kind": "fact",
        "title": path,
        "body": body,
        "source": {"system": "markdown", "path": path, "sha256": hashlib.sha256(body.encode()).hexdigest()},
        **kw,
    }


def _req(items: list[dict], rid: str) -> dict:
    return {"project": "fx", "request_id": rid, "client": "pytest/0", "items": items}


async def test_one_current_owner_per_source_key(connect) -> None:
    pid, _ = await make_project(connect, "fx")
    call = Caller(connect, await make_device(connect, "importer", {pid: "write"}))
    ack = await call(
        "memory.write",
        _req([_item("a.md", "A\n", describes=["src/x.py"])], "00000000-0000-4000-8000-000000000001"),
    )
    lid, vid = ack["versions"][0]["logical_id"], ack["versions"][0]["version_id"]
    with pytest.raises(ToolCallError) as exc:
        await call("memory.write", _req([_item("a.md", "A2\n")], "00000000-0000-4000-8000-000000000002"))
    assert exc.value.code == "E_VERSION_CONFLICT"
    assert exc.value.details == {
        "index": 0,
        "reason": "source_owned",
        "logical_id": lid,
        "current_version_id": vid,
    }
    with pytest.raises(ToolCallError) as exc:
        await call(
            "memory.write",
            _req([_item("b.md", "B\n"), _item("b.md", "B\n")], "00000000-0000-4000-8000-000000000003"),
        )
    assert exc.value.code == "E_INVALID_ARG"
    # the owner may revise itself; the key then still has exactly one current owner
    await call(
        "memory.write",
        _req(
            [_item("a.md", "A2\n", logical_id=lid, expected_version_id=vid)],
            "00000000-0000-4000-8000-000000000004",
        ),
    )
    # the database backstop (EXCLUDE mv_one_source_owner) holds for any write path
    import psycopg

    async with await connect() as conn:
        with pytest.raises(psycopg.errors.ExclusionViolation):
            await conn.execute(
                "INSERT INTO memory_versions (logical_id, project_id, project_ids, kind, title, body,"
                " token_count, valid_from, recorded_at, source_event_id, source, source_key)"
                " SELECT 999999, project_id, project_ids, kind, title, body, token_count, valid_from,"
                " recorded_at, source_event_id, source, source_key FROM memory_versions"
                " WHERE source_key = 'markdown:a.md' AND superseded_at = 'infinity'"
            )
        await conn.rollback()


# --------------------------------------------------------------------------- G6 replay with W1.5 fields
async def test_replay_identical_with_sources_code_refs_and_survivors(connect) -> None:
    pid, _ = await make_project(connect, "fx")
    call = Caller(connect, await make_device(connect, "importer", {pid: "write"}))
    ack = await call(
        "memory.write",
        _req(
            [_item("r.md", "one\n", describes=["src/a.py", "src/b.py"], valid_from="2026-01-01T00:00:00Z")],
            "00000000-0000-4000-8000-000000000011",
        ),
    )
    lid, vid = ack["versions"][0]["logical_id"], ack["versions"][0]["version_id"]
    # a backdated correction of the middle of the interval: two survivors copy source + code_refs
    await call(
        "memory.write",
        _req(
            [
                _item(
                    "r.md",
                    "two\n",
                    describes=["src/c.py"],
                    logical_id=lid,
                    expected_version_id=vid,
                    valid_from="2026-03-01T00:00:00Z",
                    valid_to="2026-04-01T00:00:00Z",
                )
            ],
            "00000000-0000-4000-8000-000000000012",
        ),
    )
    survivors = await rows(
        connect,
        "SELECT mv.version_id, array_agg(c.path ORDER BY c.path), mv.source->>'path' FROM memory_versions mv"
        " JOIN code_refs c USING (version_id) WHERE mv.logical_id = %s AND mv.superseded_at = 'infinity'"
        " GROUP BY mv.version_id, mv.source ORDER BY mv.version_id",
        (lid,),
    )
    assert [s[1] for s in survivors] == [["src/a.py", "src/b.py"], ["src/a.py", "src/b.py"], ["src/c.py"]]
    assert {s[2] for s in survivors} == {"r.md"}
    async with await connect() as conn:
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
    assert before["code_refs"]


# --------------------------------------------------------------------------- hlm.export rules
async def test_export_authz_scope_paging_and_cursor(connect) -> None:
    pid, _ = await make_project(connect, "fx")
    a = await make_device(connect, "dev-a", {pid: "write"})
    b = await make_device(connect, "dev-b", {pid: "read"}, device_class="work")
    stranger = await make_device(connect, "dev-c", {})
    ca, cb = Caller(connect, a), Caller(connect, b)
    big = "x" * 60000 + "\n"
    await ca(
        "memory.write",
        _req(
            [_item(f"f{i}.md", f"body {i}\n") for i in range(30)]
            + [_item("big.md", big), _item("mine.md", "only mine\n", device_scope=f"device:{a.device_id}")],
            "00000000-0000-4000-8000-000000000021",
        ),
    )
    with pytest.raises(ToolCallError) as exc:
        await Caller(connect, stranger)("hlm.export", {"project": "fx", "token_budget": 4000})
    assert exc.value.code == "E_FORBIDDEN_PROJECT"
    small = {"project": "fx", "token_budget": 2000, "view": "full"}
    pages, cursor, got = 0, None, []
    while True:
        page = await ca("hlm.export", {**small, **({"cursor": cursor} if cursor else {})})
        pages += 1
        got += page["items"]
        assert page["budget"]["used"] <= 2000
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages > 5
    joined, _ = await fetch_items(ca, "fx", view="full")
    assert next(i for i in joined if (i.get("source") or {}).get("path") == "big.md")["body"] == big
    assert len(joined) == 33  # 32 items + the skeleton card
    seen_b, _ = await fetch_items(cb, "fx")
    assert {(i.get("source") or {}).get("path") for i in seen_b} >= {"f0.md", "big.md"}
    assert "mine.md" not in {(i.get("source") or {}).get("path") for i in seen_b}  # device scope
    first = await ca("hlm.export", small)
    with pytest.raises(ToolCallError) as exc:
        await cb("hlm.export", {**small, "cursor": first["next_cursor"]})
    assert exc.value.code == "E_INVALID_CURSOR"
    # no access events: a bulk export never resets idleness (D-012)
    assert await scalar(connect, "SELECT count(*) FROM events WHERE kind = 'access'") == 0
