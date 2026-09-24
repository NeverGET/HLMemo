"""W2d ``memory.register_lesson``: lesson format, idempotency, authorization, the W2b hook point,
provenance (``memory.raw``) and replay (G6 rebuild identical)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from hlmemo.core import lesson_service as ls
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps, raw
from hlmemo.core.write_service import default_deps, write
from hlmemo.db.replay import rebuild_projections
from tests.integration._mcp_fixtures import call_tool_raw, running_app, writer_on
from tests.integration._write_fixtures import MAIN, OTHER, World, dump_projections, event_payload, seed_world

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        return await seed_world(conn)


def args(project: str = MAIN, **kw: Any) -> dict[str, Any]:
    base = {
        "project": project,
        "request_id": str(uuid.uuid4()),
        "mistake": "Piped deploy.sh into `ssh host bash -s`; `docker compose exec -T` ate the script.",
        "fix": "Give every stdin-reading command `</dev/null`.",
        "context": "D-035: first deploy exited 0 with the app never started.",
        "tags": ["deploy", "ssh", "deploy"],
    }
    return {**base, **kw}


async def test_lesson_item_format_and_result(connect, world, deps) -> None:  # noqa: ANN001
    a = args()
    async with await connect() as conn:
        res = await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT kind, title, body, tags, device_scope, stability, project_ids FROM memory_versions"
            " WHERE version_id = %s",
            (res["version_id"],),
        )
        kind, title, body, tags, scope, stability, pids = await cur.fetchone()
    assert kind == "lesson" and scope == "all" and stability == "stable" and pids == [world.main_id]
    assert title == a["mistake"] and tags == ["deploy", "ssh"]
    assert body == f"## Mistake\n{a['mistake']}\n\n## Fix\n{a['fix']}\n\n## Context\n{a['context']}"
    assert res["clue"] == f"v{res['version_id']}" and res["replayed"] is False
    assert res["embedding_status"] == "queued"
    assert res["budget"]["used"] <= res["budget"]["limit"] == 2000
    # no context -> no Context section; a long first line becomes a word-cut title with an ellipsis
    long = "word " * 60
    async with await connect() as conn:
        res2 = await ls.register_lesson(conn, world.ctx_a, args(mistake=long, context=None), deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT title, body FROM memory_versions WHERE version_id = %s", (res2["version_id"],)
        )
        title2, body2 = await cur.fetchone()
    assert "## Context" not in body2 and len(title2) <= ls.TITLE_MAX and title2.endswith("…")


async def test_idempotent_replay_and_conflicts(connect, world, deps) -> None:  # noqa: ANN001
    a = args()
    async with await connect() as conn:
        first = await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await conn.commit()
        again = await ls.register_lesson(conn, world.ctx_a, dict(a), deps=deps)
        await conn.commit()
        assert again["replayed"] is True and again["version_id"] == first["version_id"]
        with pytest.raises(ToolError) as ei:
            await ls.register_lesson(conn, world.ctx_a, {**a, "fix": "something else"}, deps=deps)
        assert ei.value.code == "E_REQUEST_ID_CONFLICT"
        await conn.rollback()
        with pytest.raises(ToolError) as ei:  # the same request_id used by memory.write
            await write(
                conn,
                world.ctx_a,
                {
                    "project": MAIN,
                    "request_id": a["request_id"],
                    "client": "pytest/0",
                    "items": [{"kind": "fact", "title": "t", "body": "b"}],
                },
                deps=deps,
            )
        assert ei.value.code == "E_REQUEST_ID_CONFLICT"
        await conn.rollback()
        payload = await event_payload(conn, a["request_id"])
    assert payload["request"] == a  # verbatim register_lesson arguments
    (derived,) = payload["resolved"]["write"]["items"]
    assert derived["kind"] == "lesson" and derived["body"].startswith("## Mistake\n")


@pytest.mark.parametrize(
    "bad",
    [
        {"mistake": "   "},
        {"fix": ""},
        {"request_id": "not-a-uuid"},
        {"device_scope": "device:02"},
        {"device_scope": "everyone"},
        {"unknown": 1},
        {"project": "Bad Slug"},
    ],
)
async def test_invalid_arguments(connect, world, deps, bad) -> None:  # noqa: ANN001
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await ls.register_lesson(conn, world.ctx_a, args(**bad), deps=deps)
    assert ei.value.code == "E_INVALID_ARG"
    assert all("input" not in e for e in ei.value.details["errors"])  # F06: never echo the request


async def test_write_grant_required(connect, world, deps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        for project in (MAIN, "no-such-project"):  # dev-b: write on OTHER only
            with pytest.raises(ToolError) as ei:
                await ls.register_lesson(conn, world.ctx_b, args(project), deps=deps)
            assert ei.value.code == "E_FORBIDDEN_PROJECT"
            await conn.rollback()
        res = await ls.register_lesson(conn, world.ctx_b, args(OTHER, device_scope="class:work"), deps=deps)
        await conn.commit()
    assert res["replayed"] is False


async def test_librarian_priority_is_persisted_write_context(connect, world, deps) -> None:  # noqa: ANN001
    """The priority-2 cross-project check is write context recorded in the lesson's own event
    (``payload.resolved.librarian_priority``): atomic, replayable, never a client argument, absent on
    ordinary writes. W2b's enqueue reads it (see core/lesson_service.py)."""
    a = args()
    w = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "items": [{"kind": "lesson", "title": "t", "body": "b"}],
    }
    async with await connect() as conn:
        await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await write(conn, world.ctx_a, w, deps=deps)
        await conn.commit()
        lesson_payload = await event_payload(conn, a["request_id"])
        write_payload = await event_payload(conn, w["request_id"])
        with pytest.raises(ToolError) as ei:  # clients cannot set it through register_lesson
            await ls.register_lesson(conn, world.ctx_a, args(librarian_priority=1), deps=deps)
        assert ei.value.code == "E_INVALID_ARG"
        await conn.rollback()
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
        again = await event_payload(conn, a["request_id"])
    assert lesson_payload["resolved"]["librarian_priority"] == ls.PRIORITY == 2
    assert "librarian_priority" not in write_payload["resolved"]
    assert "librarian_priority" not in lesson_payload["request"]  # server-side context only
    assert again == lesson_payload  # events are never rewritten by replay


async def test_raw_provenance_and_replay_identical(connect, world, deps) -> None:  # noqa: ANN001
    a = args()
    async with await connect() as conn:
        res = await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await conn.commit()
        out = await raw(
            conn,
            world.ctx_a,
            {"project": MAIN, "version_id": res["version_id"], "token_budget": 4000},
            deps=default_read_deps(),
        )
        await conn.commit()
        assert out["kind"] == "lesson" and out["payload_item"]["body"].startswith("## Mistake\n")
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before


async def test_register_lesson_over_the_wire(db_dsn) -> None:  # noqa: ANN001
    async with running_app(db_dsn) as client:
        token = await writer_on(client, "wire-lessons")
        a = args("wire-lessons")
        res = await call_tool_raw(client, token, "memory.register_lesson", a)
        assert not res.get("isError"), res
        (block,) = res["content"]
        out = json.loads(block["text"])
        assert set(out) == {
            "request_id",
            "replayed",
            "clue",
            "logical_id",
            "version_id",
            "embedding_status",
            "budget",
        }
        again = json.loads(
            (await call_tool_raw(client, token, "memory.register_lesson", a))["content"][0]["text"]
        )
        assert again["replayed"] is True and again["version_id"] == out["version_id"]
        bad = await call_tool_raw(client, token, "memory.register_lesson", {**a, "mistake": ""})
        assert bad["isError"] and json.loads(bad["content"][0]["text"])["code"] == "E_INVALID_ARG"
