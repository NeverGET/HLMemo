"""Kit fixes after the first second-device migrations (2026-11-12): a changed re-import keeps a narrowed
``device_scope``, a comma-separated ``tags`` line is a list, and ``hlm project list`` falls back to the
device's grants on a server without the listing route."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hlmemo.cli import hlm as cli
from hlmemo.cli.http_client import HlmHttpError
from hlmemo.core.budget import Meter
from hlmemo.importers import common, markdown
from hlmemo.importers.build import _tags
from hlmemo.importers.common import ImportRecord
from hlmemo.importers.plan import Entry, classify, remap
from hlmemo.importers.runner import _item


def _rec(path: str, body: str) -> ImportRecord:
    return ImportRecord(
        system="markdown",
        path=path,
        file=path,
        sha256=common.sha256_text(body),
        title=path,
        body=body,
        kind_guess="fact",
        tags=[],
        mtime="2026-01-01T00:00:00Z",
    )


def _row(lid: int, rec: ImportRecord, **extra: Any) -> dict[str, Any]:
    return {"logical_id": lid, "version_id": lid * 10, "kind": "fact", "source": rec.source(), **extra}


def test_a_changed_reimport_keeps_a_narrowed_scope() -> None:
    edited, plain, same = _rec("a.md", "A2\n"), _rec("b.md", "B2\n"), _rec("c.md", "C\n")
    manifest = [
        _row(1, _rec("a.md", "A1\n"), device_scope="class:work"),  # re-scoped after its import
        _row(2, _rec("b.md", "B1\n"), device_scope="all"),
        _row(3, same, device_scope="class:work"),
    ]
    parsed = common.ParseResult(records=[edited, plain, same], scopes=[""])
    plan = classify("fx", "markdown", parsed, manifest, Meter())
    by = {e.record.path: e for e in plan.entries}
    assert (by["a.md"].action, by["a.md"].keep_scope) == ("changed", "class:work")
    assert (by["b.md"].action, by["b.md"].keep_scope) == ("changed", None)
    assert by["c.md"].action == "unchanged"  # the scope alone never makes a source file "changed"

    kept = _item(by["a.md"], None)
    assert (
        kept["device_scope"] == "class:work" and kept["logical_id"] == 1 and kept["expected_version_id"] == 10
    )
    assert "device_scope" not in _item(by["b.md"], None)  # the write's default stays "all"


def test_a_moved_file_keeps_the_scope_through_the_remap() -> None:
    """Review of the kit fixes: a re-mapped record is a revision of the old item too."""
    body = "## Setup\n\n" + " ".join(f"word{i}" for i in range(60)) + "\n"
    moved = _rec("b.md#installation", body.replace("## Setup", "## Installation"))
    moved.file = "b.md"
    gone = {**_row(7, _rec("a.md#setup", body), device_scope="class:work"), "body": body}
    plan = classify("fx", "markdown", common.ParseResult(records=[moved], scopes=[""]), [gone], Meter())
    assert plan.entries[0].action == "new" and [it["logical_id"] for it in plan.missing] == [7]
    remap(plan, [gone], confirm_close=True)
    e = plan.entries[0]
    assert (e.action, e.logical_id, e.remapped_from) == ("changed", 7, "markdown:a.md#setup")
    assert e.keep_scope == "class:work" and _item(e, None)["device_scope"] == "class:work"


def test_a_kind_change_keeps_the_scope_and_a_card_or_an_export_record_never_takes_it() -> None:
    rec = _rec("a.md", "A\n")
    rec.kind_guess = "lesson"  # same body, another kind: a revision
    manifest = [_row(1, _rec("a.md", "A\n"), device_scope="device:9")]
    plan = classify("fx", "markdown", common.ParseResult(records=[rec], scopes=[""]), manifest, Meter())
    assert plan.entries[0].action == "changed" and _item(plan.entries[0], None)["device_scope"] == "device:9"

    card = _rec("CARD.md", "card\n")
    card.kind_guess = "project_card"  # the server demands "all" for a card
    assert "device_scope" not in _item(
        Entry(card, "changed", 5, 50, expected=50, keep_scope="class:work"), None
    )

    exported = _rec("x.md", "X\n")
    exported.export = {"logical_id": 3, "device_scope": "class:personal", "source": exported.source()}
    item = _item(Entry(exported, "changed", 3, 30, expected=30, keep_scope="class:work"), None)
    assert item["device_scope"] == "class:personal"  # an export record states its own scope
    exported.export = {"logical_id": 3, "source": exported.source()}
    assert "device_scope" not in _item(
        Entry(exported, "changed", 3, 30, expected=30, keep_scope="class:work"), None
    )


def test_a_new_item_from_a_source_file_carries_no_scope() -> None:
    parsed = common.ParseResult(records=[_rec("n.md", "N\n")], scopes=[""])
    plan = classify("fx", "markdown", parsed, [], Meter())
    assert plan.entries[0].keep_scope is None and "device_scope" not in _item(plan.entries[0], None)


@pytest.mark.parametrize(
    ("meta", "want"),
    [
        (
            {"tags": ["colleague-work", "infra"]},
            ["markdown", "imported", "sessions", "colleague-work", "infra"],
        ),
        ({"tags": "colleague-work, infra"}, ["markdown", "imported", "sessions", "colleague-work", "infra"]),
        ({"tags": "colleague-work"}, ["markdown", "imported", "sessions", "colleague-work"]),
        ({"tags": " , "}, ["markdown", "imported", "sessions"]),
        ({"tags": "[colleague-work, infra"}, ["markdown", "imported", "sessions", "colleague-work", "infra"]),
        (
            {"tags": 'colleague-work, "infra"'},
            ["markdown", "imported", "sessions", "colleague-work", "infra"],
        ),
        ({}, ["markdown", "imported", "sessions"]),
    ],
)
def test_tags_as_a_list_or_a_comma_string(meta: dict[str, Any], want: list[str]) -> None:
    from hlmemo.migrate import lint

    assert _tags("markdown", "sessions/2026-10.md", meta) == want
    assert lint._tags(meta) == want[3:]  # noqa: SLF001 - the lint and the import read the same tags


def test_a_comma_tags_line_survives_the_markdown_import(tmp_path: Path) -> None:
    f = tmp_path / "sessions" / "one.md"
    f.parent.mkdir()
    f.write_text("---\ntitle: One entry\ndate: 2026-10-09\ntags: colleague-work, infra\n---\nBody.\n")
    parsed = markdown.parse([tmp_path], base=tmp_path, use_git=False)
    assert [r.tags for r in parsed.records] == [
        ["markdown", "imported", "sessions", "colleague-work", "infra"]
    ]


class _FakeHttp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def project_list(self) -> list[dict[str, Any]]:
        if self.status == 200:
            return [{"id": 7, "slug": "alpha", "name": "Alpha", "role": "write"}]
        raise HlmHttpError("E_NOT_FOUND", "HTTP 404", status=404 if self.status >= 4040 else self.status)

    def whoami(self) -> dict[str, Any]:
        if self.status == 4040:  # the listing route is missing and whoami fails too
            raise HlmHttpError("E_AUTH", "HTTP 401", status=401)
        if self.status == 4041:
            return {"device": {"id": 3}, "grants": []}
        return {
            "device": {"id": 3},
            "grants": [{"project": "alpha", "role": "write"}, {"project": "beta", "role": "read"}],
        }


class _FakeCtx:
    def __init__(self, status: int, as_json: bool) -> None:
        self.status, self.as_json = status, as_json

    def http(self) -> _FakeHttp:
        return _FakeHttp(self.status)


@pytest.mark.parametrize("as_json", [True, False])
def test_project_list_falls_back_to_the_grants_on_a_404(
    monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    monkeypatch.setattr(cli, "_ctx", lambda _ctx: _FakeCtx(404, as_json))
    r = CliRunner().invoke(cli.app, ["project", "list"])
    assert r.exit_code == 0, r.output
    if as_json:
        got = json.loads(r.stdout)
        assert got["source"] == "grants"
        assert got["projects"] == [{"slug": "alpha", "role": "write"}, {"slug": "beta", "role": "read"}]
    else:
        assert "alpha" in r.stdout and "role=read" in r.stdout


def test_project_list_keeps_the_listing_route_and_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_ctx", lambda _ctx: _FakeCtx(200, True))
    r = CliRunner().invoke(cli.app, ["project", "list"])
    assert r.exit_code == 0 and json.loads(r.stdout)["source"] == "projects"
    monkeypatch.setattr(cli, "_ctx", lambda _ctx: _FakeCtx(403, True))
    r = CliRunner().invoke(cli.app, ["project", "list"])
    assert r.exit_code != 0  # a refusal is not a missing route
    monkeypatch.setattr(cli, "_ctx", lambda _ctx: _FakeCtx(4040, True))
    r = CliRunner().invoke(cli.app, ["project", "list"])
    assert r.exit_code != 0  # a failing whoami is reported, not hidden
    monkeypatch.setattr(cli, "_ctx", lambda _ctx: _FakeCtx(4041, True))
    r = CliRunner().invoke(cli.app, ["project", "list"])
    assert r.exit_code == 0 and json.loads(r.stdout) == {"projects": [], "source": "grants"}
