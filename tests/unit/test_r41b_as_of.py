"""R4.1 review F-1: memory_as_of counts only items still readable/active/current at the end."""

import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hlmemo.core import research_service as rsv
from hlmemo.librarian.tasks import research as rs


def _run(conn, view):
    @contextlib.asynccontextmanager
    async def db():
        yield conn

    return NS(
        db=db,
        fresh_ctx=AsyncMock(return_value=NS(has=lambda p, _: p == 1)),
        project_id=1,
        sent={1},
        excluded=set(),
        view=view,
        researcher=NS(answer_mode="claims", writer_profile="w", redactor=NS(text=lambda s: s)),
        question="q",
        slug="p",
        queries=[],
        calls=0,
        steps=[],
        map_tokens=0,
        flags={"budget_stop": False},
        excerpts_shown=["v1.0"],
        cite=False,
        prose=False,
        claims_mode=True,
    )


async def _finish(monkeypatch, rows, view):
    async def version_access(_conn, vids, _at):
        return [rows[v] for v in vids if v in rows]

    monkeypatch.setattr(rsv.sq, "version_access", version_access)
    monkeypatch.setattr(rsv.mm, "project_policies", AsyncMock(return_value={1: "on", 2: "on"}))
    monkeypatch.setattr(rsv, "cross_project_excluded", AsyncMock(return_value=set()))
    monkeypatch.setattr(rsv.mm, "view_scopes", lambda _: [None])
    monkeypatch.setattr(rsv.mm, "isolation_ok", lambda *_: True)
    run = _run(NS(execute=AsyncMock()), view)
    excerpt = rs.Excerpt("v1.0", 1, "old", "old.md", "2026-09-20", "old fact")
    validated = rs.Validated(
        rs.ANSWERED,
        rs.ANSWERED,
        "old fact",
        [rs.Claim("old fact", [("v1.0", "old fact")], "kept")],
        ["v1.0"],
        [],
        "high",
    )
    return await rsv._finish(run, validated, [excerpt], datetime(2026, 9, 30, 12, tzinfo=UTC))


OLD = datetime(2026, 9, 20, tzinfo=UTC)
NEW = datetime(2026, 9, 30, tzinfo=UTC)


def _view():
    return {1: NS(path="old.md", recorded_at=OLD), 2: NS(path="protected.md", recorded_at=NEW)}


@pytest.mark.asyncio
async def test_as_of_excludes_revoked_view_item(monkeypatch):
    rows = {
        1: NS(version_id=1, project_ids=[1], device_scope=None, current=True, status="active"),
        2: NS(version_id=2, project_ids=[1, 2], device_scope=None, current=True, status="active"),
    }
    out = await _finish(monkeypatch, rows, _view())
    assert out["meta"]["memory_as_of"] == OLD.isoformat()


@pytest.mark.asyncio
async def test_as_of_excludes_superseded_item(monkeypatch):
    rows = {
        1: NS(version_id=1, project_ids=[1], device_scope=None, current=True, status="active"),
        2: NS(version_id=2, project_ids=[1], device_scope=None, current=False, status="active"),
    }
    out = await _finish(monkeypatch, rows, _view())
    assert out["meta"]["memory_as_of"] == OLD.isoformat()


@pytest.mark.asyncio
async def test_no_readable_item_means_null_and_no_freshness_line(monkeypatch):
    rows = {
        1: NS(version_id=1, project_ids=[1, 2], device_scope=None, current=True, status="active"),
        2: NS(version_id=2, project_ids=[1, 2], device_scope=None, current=True, status="active"),
    }
    out = await _finish(monkeypatch, rows, _view())
    assert out["meta"]["memory_as_of"] is None
    assert "Memory records" not in out["answer"] and "bellek" not in out["answer"]
