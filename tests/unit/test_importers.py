"""W1.5 importer units: parsing rules, the temporal rule, the export format and G-I1's golden dry-run.

The golden reports (``tests/fixtures/import/golden/*.json``) are the dry-run of each source over the
fixture tree against an empty project, with a fixed clock and ``tz=UTC``; regenerate them with
``HLM_UPDATE_GOLDEN=1`` after an intended rule change (and review the diff).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hlmemo.core.budget import Meter
from hlmemo.importers import common, exportfmt
from hlmemo.importers.cli import parse_source, resolve_tz
from hlmemo.importers.common import ImportRecord, Section
from hlmemo.importers.plan import classify, report, request_id

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "import"
GOLDEN = FIXTURE / "golden"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
SOURCES = {
    "markdown": dict(paths=[FIXTURE / "repo" / "docs"], base=FIXTURE / "repo"),
    "context": dict(paths=[FIXTURE / "repo"], base=FIXTURE / "repo"),
    "automemory": dict(paths=[FIXTURE / "automemory"]),
    "serena": dict(paths=[FIXTURE / "serena" / "memories"], repo=FIXTURE / "repo"),
}


@pytest.fixture(scope="module")
def meter() -> Meter:
    return Meter()


def dry_run(source: str, meter: Meter) -> dict:
    spec = SOURCES[source]
    parsed = parse_source(
        source, spec["paths"], base=spec.get("base"), repo=spec.get("repo"), now=NOW, tz=UTC
    )
    return report(classify("fx", source, parsed, [], meter), dry_run=True)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_golden_dry_run(source: str, meter: Meter) -> None:
    """G-I1 (1/4): the dry-run report of the fixture tree is golden-equal."""
    got = dry_run(source, meter)
    path = GOLDEN / f"{source}.json"
    if os.environ.get("HLM_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    assert got == json.loads(path.read_text())


def test_fixture_report_covers_every_rule(meter: Meter) -> None:
    md = dry_run("markdown", meter)
    keys = {i["key"]: i for i in md["items"]}
    # decision rows: one item per row with its own date; the 2099 row is rejected, not undated
    assert keys["markdown:docs/decisions/DECISIONS.md#D-001"]["valid_from"] == "2026-01-05T00:00:00Z"
    assert keys["markdown:docs/decisions/DECISIONS.md#D-001"]["evidence"] == "decision-row"
    assert md["rejected"] == [
        {
            "key": "markdown:docs/decisions/DECISIONS.md#D-003",
            "reason": "future_evidence_date",
            "date": "2099-01-01T00:00:00Z",
        }
    ]
    # dated headings split the log; the preamble has no evidence (server import time)
    assert keys["markdown:docs/notes/changelog.md#2026-01-10-queue-introduced"]["kind"] == "episode"
    assert keys["markdown:docs/notes/changelog.md"]["valid_from"] is None
    # frontmatter date + describes of an existing repo file
    fm = keys["markdown:docs/notes/frontmatter-date.md"]
    assert fm["evidence"] == "frontmatter:date" and fm["describes"] == ["src/app/main.py"]
    # no evidence → no valid_from (never mtime)
    assert keys["markdown:docs/notes/plain.md"]["valid_from"] is None
    reasons = {s["path"]: s["reason"] for s in md["skipped"]}
    assert reasons == {
        "docs/notes/archive/plain.md": "duplicate-of:docs/notes/plain.md",
        "docs/notes/empty.md": "empty",
        "docs/notes/stub.md": "stub-of:plain.md",
    }
    assert {g["reason"] for g in md["duplicate_groups"]} == {"same_content", "same_name"}
    ctx = dry_run("context", meter)
    assert {s["path"]: s["reason"] for s in ctx["skipped"]} == {"sub/CLAUDE.md": "stub-of:../AGENTS.md"}
    assert {i["key"] for i in ctx["items"]} == {"context:.mcp.json", "context:AGENTS.md", "context:CLAUDE.md"}
    auto = {i["key"]: i for i in dry_run("automemory", meter)["items"]}
    assert auto["automemory:feedback_testing.md"]["kind"] == "lesson"
    assert auto["automemory:project_state.md"]["valid_from"] == "2026-04-02T00:00:00Z"
    ser = {i["key"]: i for i in dry_run("serena", meter)["items"]}
    assert ser["serena:lessons_learned.md"]["kind"] == "lesson"
    assert ser["serena:session_2026_05_01.md"]["kind"] == "episode"
    assert ser["serena:session_2026_05_01.md"]["valid_from"] == "2026-05-01T00:00:00Z"
    assert ser["serena:01_auth_flow.md"]["describes"] == ["src/app/main.py"]


def test_date_only_evidence_reads_in_the_writers_zone() -> None:
    """A date written 'today' east of UTC is not a future date (the D-069 case at UTC+3)."""
    iso = common.evidence_instant("2026-09-24", resolve_tz("Europe/Istanbul"))
    assert iso == "2026-09-23T21:00:00Z"
    assert not common.is_future(iso, datetime(2026, 9, 23, 22, 42, tzinfo=UTC))
    assert common.is_future(
        common.evidence_instant("2026-09-24", UTC), datetime(2026, 9, 23, 22, 42, tzinfo=UTC)
    )
    assert common.evidence_instant("2026-09-24T10:00:00+02:00", UTC) == "2026-09-24T08:00:00Z"
    assert common.evidence_instant("not a date") is None


def test_future_tolerance_is_five_minutes() -> None:
    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    assert not common.is_future((now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), now)
    assert common.is_future((now + timedelta(minutes=5, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), now)


def test_size_split_keeps_the_main_anchor() -> None:
    text = "# Big\n\n" + "".join(f"## Part {i}\n\n" + ("word " * 3000) + "\n\n" for i in range(10))
    parts = common.size_split(Section(None, text))
    assert len(parts) >= 3 and all(len(p.body) <= common.ITEM_BODY_MAX for p in parts)
    assert parts[0].anchor is None and "".join(p.body for p in parts) == text
    assert len({p.anchor for p in parts}) == len(parts)


def test_long_files_become_heading_sections_with_stable_anchors() -> None:
    """G-I4 finding: a 50k spec as one item surfaces one chunk; sections compete on their own."""
    body = "\n\n".join(f"## Part {i}\n\n" + ("alpha beta " * 300) for i in range(6))
    text = "# Big spec\n\nIntro line.\n\n" + body + "\n"
    parts = common.section_split(Section(None, text), 5000, doc_title="Big spec")
    assert parts[0].anchor is None and parts[0].body.startswith("# Big spec")
    assert [p.anchor for p in parts[1:]] == [f"part-{i}" for i in range(6)]
    assert parts[1].lead == "Big spec › Part 0" and "".join(p.body for p in parts) == text
    # an edit inside one section changes only that section (anchors do not shift)
    edited = text.replace("## Part 3\n\n", "## Part 3\n\nNEW LINE\n\n")
    again = common.section_split(Section(None, edited), 5000, doc_title="Big spec")
    assert [p.anchor for p in again] == [p.anchor for p in parts]
    assert [a.body == b.body for a, b in zip(parts, again, strict=True)].count(False) == 1
    assert common.section_split(Section(None, "# small\n"), 5000) == [Section(None, "# small\n")]


def test_secret_files_never_enter_a_payload(tmp_path: Path) -> None:
    f = tmp_path / "notes.md"
    f.write_text("# Keys\n\naws " + "AKIA" + "ABCDEFGHIJKLMNOP" + " leaked\n")
    assert common.read_text(f) == (None, "secret-pattern:aws-access-key")


def test_frontmatter_subset() -> None:
    meta, body = common.parse_frontmatter('---\nname: A b\ntags: [x, "y"]\nn: 3\n---\nbody\n')
    assert meta == {"name": "A b", "tags": ["x", "y"], "n": 3} and body == "body\n"
    assert common.parse_frontmatter("no frontmatter\n") == ({}, "no frontmatter\n")


def test_dedupe_names_and_titles() -> None:
    assert common.dedupe_name("docs/01_Auth-Flow.md") == common.dedupe_name("x/auth_flow.md") == "auth-flow"
    assert (
        common.derive_title({}, "no heading", "notes/02_setup-steps.md")
        == "setup steps · notes/02_setup-steps.md"
    )
    assert common.derive_title({}, "# Title\n", "a.md") == "Title · a.md"


def test_export_format_round_trip() -> None:
    item = {
        "logical_id": 17,
        "version_id": 903,
        "kind": "fact",
        "title": "Retry policy",
        "tags": ["serena", "imported"],
        "valid_from": "2026-02-11T00:00:00.000000Z",
        "valid_to": None,
        "source": {"system": "serena", "path": "retry.md", "sha256": "a" * 64},
        "describes": ["src/app/main.py"],
        "links": [{"rel": "relates_to", "dst_logical_id": 18, "dst_version_id": None}],
        "pinned": False,
        "stability": "volatile",
        "importance": None,
        "device_scope": "all",
        "also_in": [],
        "status": "active",
        "body": "---\nnot frontmatter\n---\nbody text\n",
        "body_sha256": "b" * 64,
    }
    other = dict(item, logical_id=18, version_id=904, title="Other", source=None, links=[])
    names = exportfmt.file_names([item, other])
    text = exportfmt.render_item(item, names, "fx")
    meta, body = common.parse_frontmatter(text)
    assert body == item["body"] and exportfmt.is_export(meta)
    rec = exportfmt.record_from_export("markdown", names[17], meta, body)
    assert rec is not None and rec.export is not None
    assert rec.export["logical_id"] == 17 and rec.export["links"] == [
        {"rel": "relates_to", "target": names[18]}
    ]
    assert rec.key == "serena:retry.md" and rec.source() == item["source"] and "origin" not in text
    # ids are the only lines that differ between two projects
    moved = exportfmt.render_item(
        dict(item, logical_id=99, version_id=1000), {99: names[17], 18: names[18]}, "other-project"
    )
    assert exportfmt.strip_ids(moved) == exportfmt.strip_ids(text) and moved != text
    # an item without a real provenance is identified by its origin; imported elsewhere it keeps it
    native = exportfmt.render_item(other, names, "fx")
    assert 'origin: "fx/18"' in native and "source:" not in native
    meta2, body2 = common.parse_frontmatter(native)
    rec2 = exportfmt.record_from_export("markdown", names[18], meta2, body2)
    assert rec2 is not None and rec2.key == "hlm:fx/18"
    assert rec2.source() == {"system": "hlm", "path": "fx/18", "sha256": common.sha256_text(body2)}
    copy = dict(other, logical_id=500, version_id=501, source=rec2.source())
    assert exportfmt.strip_ids(
        exportfmt.render_item(copy, {500: names[18]}, "fx-copy")
    ) == exportfmt.strip_ids(native)
    index = exportfmt.render_index([item, other], names)
    assert exportfmt.is_index(index) and "v903" not in index


def test_file_names_do_not_depend_on_ids() -> None:
    a = {"logical_id": 1, "kind": "fact", "title": "Same", "body_sha256": "1", "valid_from": "x"}
    b = {"logical_id": 2, "kind": "fact", "title": "Same", "body_sha256": "2", "valid_from": "x"}
    first = exportfmt.file_names([a, b])
    swapped = exportfmt.file_names([dict(a, logical_id=20), dict(b, logical_id=10)])
    assert sorted(first.values()) == sorted(swapped.values()) == ["fact/same-2.md", "fact/same.md"]
    assert first[1] == swapped[20]


def _rec(path: str, body: str) -> ImportRecord:
    return ImportRecord(
        system="markdown",
        path=path,
        file=path,
        sha256=common.sha256_text(body),
        title="t",
        body=body,
        kind_guess="fact",
        tags=[],
        mtime="2026-01-01T00:00:00Z",
    )


def test_classification_ignores_mtime(meter: Meter) -> None:
    same, edited, fresh = _rec("a.md", "A\n"), _rec("b.md", "B2\n"), _rec("c.md", "C\n")
    manifest = [
        {
            "logical_id": 1,
            "version_id": 10,
            "kind": "fact",
            "source": dict(same.source(), mtime="1999-01-01T00:00:00Z"),
        },
        {"logical_id": 2, "version_id": 20, "kind": "fact", "source": _rec("b.md", "B1\n").source()},
        {"logical_id": 3, "version_id": 30, "kind": "fact", "source": _rec("gone.md", "G\n").source()},
    ]
    parsed = common.ParseResult(records=[same, edited, fresh], scopes=[""])
    plan = classify("fx", "markdown", parsed, manifest, meter)
    got = {e.record.path: (e.action, e.logical_id, e.head_version_id) for e in plan.entries}
    assert got == {"a.md": ("unchanged", 1, 10), "b.md": ("changed", 2, 20), "c.md": ("new", None, None)}
    assert [it["logical_id"] for it in plan.missing] == [3]
    rid = request_id("fx", "markdown:a.md", same.sha256)
    assert rid == request_id("fx", "markdown:a.md", same.sha256)
    assert rid != request_id("fx", "markdown:a.md", edited.sha256)
    # Sol 42 #4: the same content revising another head is another request (A->B->A->B cycles)
    assert request_id("fx", "k", same.sha256, 10) != request_id("fx", "k", same.sha256, 12) != rid


def test_negative_date_evidence_is_ignored() -> None:
    """Sol 42 #3: only dated-RECORD forms are evidence; dates in headings' text, tables and prose
    are not."""
    for heading in (
        "Prices as of 2026-09-22",
        "Round 10 (2026-09-22)",
        "Changes since 2026-01-01",
        "2026-02-30 impossible date",
        "v2026-09-22",
    ):
        text = f"# Notes\n\n## {heading}\n\nbody\n\n## Other {heading}\n\nmore\n"
        assert common.dated_sections(text, UTC) == (None, None), heading
        assert common.heading_date(heading, UTC) is None, heading
    table = "# Log\n\n| date | event |\n|---|---|\n| 2026-09-22 | deploy |\n\nOn 2026-09-23 we shipped.\n"
    assert common.dated_sections(table, UTC) == (None, None)
    for heading, want in (
        ("2026-09-24", "2026-09-24T00:00:00Z"),
        ("2026-09-24 — Queue introduced", "2026-09-24T00:00:00Z"),
        ("SESSION 2026-05-01 — cache rollout", "2026-05-01T00:00:00Z"),
        ("session 2026-05-01: notes", "2026-05-01T00:00:00Z"),
    ):
        assert common.heading_date(heading, UTC) == want, heading


def test_body_similarity_and_remap(meter: Meter) -> None:
    """Sol 42 #6: a renamed heading keeps its logical item (re-map), a removed section is closed."""
    from hlmemo.importers.plan import body_similarity, remap

    body = "## Setup\n\n" + " ".join(f"word{i}" for i in range(60)) + "\n"
    renamed = body.replace("## Setup", "## Installation")
    assert (
        body_similarity(body, renamed) == 1.0 and body_similarity(body, "## X\n\nother text here now\n") < 0.1
    )
    new = _rec("a.md#installation", renamed)
    new.file = "a.md"
    gone = {"logical_id": 7, "version_id": 70, "kind": "fact", "source": _rec("a.md#setup", body).source()}
    dropped = {"logical_id": 8, "version_id": 80, "kind": "fact", "source": _rec("a.md#old", "x").source()}
    plan = classify(
        "fx", "markdown", common.ParseResult(records=[new], scopes=["a.md"]), [gone, dropped], meter
    )
    assert sorted(it["logical_id"] for it in plan.missing) == [7, 8]
    remap(plan, [dict(gone, body=body), dict(dropped, body="## Old\n\nentirely different words\n")])
    (e,) = plan.entries
    assert (e.action, e.logical_id, e.expected, e.remapped_from) == ("changed", 7, 70, "markdown:a.md#setup")
    assert [it["logical_id"] for it in plan.closes] == [8]
    rep = report(plan, dry_run=True)
    assert rep["closed"] == ["markdown:a.md#old"] and rep["remapped"][0]["to"] == "markdown:a.md#installation"


def _export_rec(meta: dict, body: str = "text\n") -> ImportRecord:
    rec = exportfmt.record_from_export("markdown", "fact/x.md", {"hlm_export": 1, **meta}, body)
    assert rec is not None
    return rec


def test_export_origin_mapping_is_ownership_checked(meter: Meter) -> None:
    """Sol 42 #5: a crafted logical_id never binds; origin maps back only in its own project, only
    onto a same-kind item without foreign provenance, and only if the file's version is the head."""
    unrelated = {"logical_id": 5, "version_id": 50, "kind": "fact", "title": "t", "source": None}
    base = {"kind": "fact", "title": "t", "valid_from": "2026-01-01T00:00:00.000000Z"}
    # a file from project "aa" whose logical_id line names project bb's item 5
    crafted = _export_rec({**base, "origin": "aa/77", "logical_id": 5, "version_id": 50})
    plan = classify("bb", "markdown", common.ParseResult(records=[crafted]), [unrelated], meter)
    assert [(e.action, e.logical_id) for e in plan.entries] == [("new", None)]
    assert crafted.source()["path"] == "aa/77"
    # an origin naming b/5 with a version that is not the head: rejected, never applied
    stale = _export_rec({**base, "origin": "bb/5", "version_id": 49}, "changed text\n")
    plan = classify("bb", "markdown", common.ParseResult(records=[stale]), [unrelated], meter)
    assert plan.entries == [] and [r.reason for r in plan.rejected] == ["stale_or_foreign_export"]
    # the right head version: a native revision (no source is written onto the item)
    ok = _export_rec({**base, "origin": "bb/5", "version_id": 50}, "changed text\n")
    (e,) = classify("bb", "markdown", common.ParseResult(records=[ok]), [unrelated], meter).entries
    assert (e.action, e.logical_id, e.expected, e.native) == ("changed", 5, 50, True)
    # kind mismatch or foreign provenance: rejected
    sourced = dict(unrelated, source={"system": "serena", "path": "x.md", "sha256": "0" * 64})
    plan = classify("bb", "markdown", common.ParseResult(records=[ok]), [sourced], meter)
    assert [r.reason for r in plan.rejected] == ["origin_mismatch"]


def test_source_key_expression_is_pinned() -> None:
    """The migration's CHECK and the insert path spell source_key identically."""
    import importlib.util

    from hlmemo.db.write_queries import source_key_sql

    spec = importlib.util.spec_from_file_location(
        "mig0007", Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0007_import.py"
    )
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.SOURCE_KEY_EXPR == source_key_sql("source")


def test_export_tool_is_dispatched_but_never_listed() -> None:
    """CC-4 / G-SURF: the client-protocol tool does not grow the agent tool surface."""
    from hlmemo.server.tools import TOOL_BY_NAME, TOOL_NAMES, TOOLS

    assert "hlm.export" in TOOL_BY_NAME
    assert "hlm.export" not in TOOL_NAMES and all(t.name.startswith("memory.") for t in TOOLS)
