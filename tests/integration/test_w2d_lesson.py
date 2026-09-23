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
    assert res["cross_project_check"] == "not_wired" and res["embedding_status"] == "queued"
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
        assert again["cross_project_check"] == "replayed"
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


async def test_cross_project_hook_point(connect, world, deps, monkeypatch) -> None:  # noqa: ANN001
    seen: list[ls.LessonRegistered] = []

    async def hook(conn, ctx, ev: ls.LessonRegistered) -> bool:  # noqa: ANN001
        cur = await conn.execute("SELECT kind FROM events WHERE event_id = %s", (ev.event_id,))
        assert (await cur.fetchone())[0] == "write"  # same transaction: the event is visible
        seen.append(ev)
        return True

    monkeypatch.setattr(ls, "CROSS_PROJECT_CHECK_HOOK", hook)
    a = args()
    async with await connect() as conn:
        res = await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await conn.commit()
        again = await ls.register_lesson(conn, world.ctx_a, a, deps=deps)
        await conn.commit()
    assert res["cross_project_check"] == "queued" and again["cross_project_check"] == "replayed"
    (ev,) = seen  # not called on the replay
    assert ev.priority == 2 and ev.project_id == world.main_id and ev.version_id == res["version_id"]

    async def declines(conn, ctx, ev) -> bool:  # noqa: ANN001
        return False

    monkeypatch.setattr(ls, "CROSS_PROJECT_CHECK_HOOK", declines)
    async with await connect() as conn:
        res = await ls.register_lesson(conn, world.ctx_a, args(), deps=deps)
        await conn.commit()
    assert res["cross_project_check"] == "skipped"

    async def boom(conn, ctx, ev) -> bool:  # noqa: ANN001
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(ls, "CROSS_PROJECT_CHECK_HOOK", boom)
    b = args()
    async with await connect() as conn:
        with pytest.raises(RuntimeError):
            await ls.register_lesson(conn, world.ctx_a, b, deps=deps)
        await conn.rollback()
        cur = await conn.execute("SELECT count(*) FROM events WHERE request_id = %s", (b["request_id"],))
        assert (await cur.fetchone())[0] == 0  # the hook runs in the write transaction: all or nothing


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
            "cross_project_check",
            "budget",
        }
        again = json.loads(
            (await call_tool_raw(client, token, "memory.register_lesson", a))["content"][0]["text"]
        )
        assert again["replayed"] is True and again["version_id"] == out["version_id"]
        bad = await call_tool_raw(client, token, "memory.register_lesson", {**a, "mistake": ""})
        assert bad["isError"] and json.loads(bad["content"][0]["text"])["code"] == "E_INVALID_ARG"
