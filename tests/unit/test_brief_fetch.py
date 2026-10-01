"""AL5 brief: the read-only fetch and the supersession filter. A fake MCP ``call``; no network, no prod."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from hlmemo.brief import fetch as F

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CARD = {"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False, "stale_clues": [], "truncated": False}


def hit(vid: int, kind: str, title: str, day: int, tags: list[str] | None = None, ordinal: int = 0) -> dict:
    return {
        "clue": f"v{vid}.{ordinal}",
        "kind": kind,
        "title": title,
        "preview": "p",
        "score": 0.1,
        "valid_from": f"2026-09-{day:02d}T10:00:00.000000Z",
        "tags": tags or [],
        "device_scope": "all",
    }


class FakeServer:
    """memory.query / memory.raw over a dict of versions; counts calls and can fail chosen ones."""

    def __init__(self) -> None:
        self.versions: dict[int, dict[str, Any]] = {}
        self.card: dict | None = CARD
        self.librarian: dict | None = None
        self.fail_raw: set[int] = set()
        self.fail_query = False
        self.calls: list[tuple[str, dict]] = []

    def add(
        self,
        vid: int,
        kind: str,
        title: str,
        body: str,
        day: int,
        *,
        lid: int | None = None,
        tags: list[str] | None = None,
        links: list[dict] | None = None,
        valid_to: str | None = None,
        superseded_at: str | None = None,
        pages: int = 1,
    ) -> None:
        self.versions[vid] = {
            "vid": vid, "kind": kind, "title": title, "body": body, "day": day, "lid": lid or vid + 1000,
            "tags": tags or [], "links": links or [], "valid_to": valid_to, "superseded_at": superseded_at,
            "pages": pages,
        }  # fmt: skip

    async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, args))
        if tool == "memory.query":
            if self.fail_query:
                raise RuntimeError("boom")
            kind = args["kinds"][0]
            hits = [
                hit(v["vid"], v["kind"], v["title"], v["day"], v["tags"])
                for v in self.versions.values()
                if v["kind"] == kind
            ]
            out: dict[str, Any] = {"card": self.card, "hits": hits, "budget": {"used": 1, "limit": 8000}}
            if self.librarian:
                out["librarian"] = self.librarian
            return out
        assert tool == "memory.raw"
        vid = args["version_id"]
        if vid in self.fail_raw:
            raise RuntimeError("raw failed")
        v = self.versions[vid]
        page = int(args.get("cursor") or 0)
        more = page + 1 < v["pages"]
        return {
            "version_id": vid, "logical_id": v["lid"], "kind": v["kind"],
            "recorded_at": f"2026-09-{v['day']:02d}T11:00:00.000000Z",
            "valid_to": v["valid_to"], "superseded_at": v["superseded_at"],
            "payload_item": {"body": v["body"]},
            "links": v["links"] if (page + 1 == v["pages"]) else [],  # links ride on the LAST page
            "next_cursor": str(page + 1) if more else None,
        }  # fmt: skip


def sup(dst_lid: int, **kw: Any) -> dict:
    return {"rel": "supersedes", "dst_logical_id": dst_lid, "dst_version_id": None, "valid_to": None,
            "superseded_at": None, **kw}  # fmt: skip


def run(server: FakeServer) -> F.Snapshot:
    return asyncio.run(F.gather_snapshot(server, "proj", now=NOW))


def server_with_notes() -> FakeServer:
    s = FakeServer()
    s.add(10, "session_note", "Session a", "Session\n\n## Decisions\n- d10", 20)
    s.add(11, "session_note", "Session b", "Session\n\n## Decisions\n- d11", 25)
    s.add(20, "lesson", "Lesson old", "rule old", 21)
    s.add(21, "lesson", "Lesson new", "rule new", 26)
    return s


def test_newest_first_and_card_and_as_of() -> None:
    snap = run(server_with_notes())
    assert [i.version_id for i in snap.sessions] == [11, 10]
    assert [i.version_id for i in snap.lessons] == [21, 20]
    assert snap.card == CARD
    assert snap.as_of == datetime(2026, 9, 26, 11, 0, tzinfo=UTC)
    assert snap.excluded == []
    assert snap.pending == 0


def test_pending_block() -> None:
    s = server_with_notes()
    s.librarian = {
        "pending_questions": 7,
        "notices": [{"question_id": "q", "kind": "link", "clues": ["v1"], "text": "t"}],
    }
    snap = run(s)
    assert snap.pending == 7 and snap.notices[0]["text"] == "t"


def test_superseded_by_live_link_is_excluded() -> None:
    s = server_with_notes()
    # lesson 21 (newer) supersedes lesson 20's logical id
    s.versions[21]["links"] = [sup(s.versions[20]["lid"])]
    snap = run(s)
    assert [i.version_id for i in snap.lessons] == [21]
    assert ("v20", "superseded") in snap.excluded


def test_cross_kind_superseder_in_pool_excludes() -> None:
    s = server_with_notes()
    s.versions[11]["links"] = [sup(s.versions[20]["lid"])]  # a session note supersedes a lesson
    snap = run(s)
    assert 20 not in [i.version_id for i in snap.lessons]


@pytest.mark.parametrize(
    "extra", [{"valid_to": "2026-09-30T00:00:00.000000Z"}, {"superseded_at": "2026-09-30T00:00:00.000000Z"}]
)
def test_expired_or_retracted_link_does_not_exclude(extra: dict) -> None:
    s = server_with_notes()
    s.versions[21]["links"] = [sup(s.versions[20]["lid"], **extra)]
    snap = run(s)
    assert [i.version_id for i in snap.lessons] == [21, 20]


def test_future_dated_link_end_still_live() -> None:
    s = server_with_notes()
    s.versions[21]["links"] = [sup(s.versions[20]["lid"], valid_to="2027-01-01T00:00:00.000000Z")]
    assert [i.version_id for i in run(s).lessons] == [21]


def test_other_relations_do_not_exclude() -> None:
    s = server_with_notes()
    s.versions[21]["links"] = [{**sup(s.versions[20]["lid"]), "rel": "relates_to"}]
    assert [i.version_id for i in run(s).lessons] == [21, 20]


def test_not_current_item_is_excluded() -> None:
    s = server_with_notes()
    s.versions[20]["superseded_at"] = "2026-09-29T00:00:00.000000Z"  # a revised/superseded version
    snap = run(s)
    assert [i.version_id for i in snap.lessons] == [21]
    assert ("v20", "not-current") in snap.excluded


def test_unverifiable_item_is_excluded() -> None:
    s = server_with_notes()
    s.fail_raw = {20}
    snap = run(s)
    assert [i.version_id for i in snap.lessons] == [21]
    assert ("v20", "unverified") in snap.excluded


def test_paged_raw_collects_links_from_later_pages() -> None:
    s = server_with_notes()
    s.versions[21]["pages"] = 2
    s.versions[21]["links"] = [sup(s.versions[20]["lid"])]
    snap = run(s)
    assert [i.version_id for i in snap.lessons] == [21]  # the supersedes edge on page 2 was read


def test_too_many_pages_is_unverified() -> None:
    s = server_with_notes()
    s.versions[21]["pages"] = F.RAW_PAGES + 2
    assert 21 not in [i.version_id for i in run(s).lessons]


def test_all_queries_failing_raises() -> None:
    s = server_with_notes()
    s.fail_query = True
    with pytest.raises(RuntimeError):
        run(s)


def test_one_failed_query_still_serves_the_rest() -> None:
    s = server_with_notes()
    real = s.__call__
    calls = {"n": 0}

    async def flaky(tool: str, args: dict) -> dict:
        if tool == "memory.query" and args["kinds"] == ["lesson"]:
            calls["n"] += 1
            raise RuntimeError("lesson query down")
        return await real(tool, args)

    snap = asyncio.run(F.gather_snapshot(flaky, "proj", now=NOW))
    assert snap.lessons == [] and [i.version_id for i in snap.sessions] == [11, 10]
    assert snap.card == CARD


def test_pool_is_capped_and_read_only() -> None:
    s = FakeServer()
    for i in range(30):
        s.add(100 + i, "lesson", f"L{i}", "b", 1 + i % 28)
    snap = run(s)
    raws = [a for t, a in s.calls if t == "memory.raw"]
    assert len(raws) <= F.POOL_LESSONS
    assert {t for t, _ in s.calls} == {"memory.query", "memory.raw"}  # never a write tool
    assert len(snap.lessons) <= F.POOL_LESSONS


def test_auto_detection() -> None:
    a = F.Item(1, "session_note", "t", None, [], body="Session 2026\n\nAUTO-CAPTURED session note")
    b = F.Item(2, "lesson", "t", None, ["auto-capture"], body="x")
    c = F.Item(3, "session_note", "t", None, [], body="Session 2026-09-26\n\nhand written")
    assert a.auto and b.auto and not c.auto
