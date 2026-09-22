"""D-033 regression tests (bake-off R1 findings F01, F06, F08, F12, F17).

Each test is the adapted blinded reproduction from ``docs/bakeoff/r1/repro/`` (which stays
untouched): it asserts the correct behaviour that the defect violated.
"""

from __future__ import annotations

import json
import uuid

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import default_read_deps, drilldown, raw
from hlmemo.core.write_service import call_the_day, default_deps, write
from tests.integration._mcp_fixtures import call_tool_raw, running_app, write_args, writer_on
from tests.integration._write_fixtures import MAIN, World, item, seed_world, write_req

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def deps():
    return default_deps()


@pytest.fixture(scope="module")
def rdeps():
    return default_read_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        return await seed_world(conn)


# --------------------------------------------------------------------------- F01
async def test_f01_noncanonical_clue_is_invalid_arg(db_dsn):
    """``v0`` / ``v01`` pass the wire pattern but are not canonical clues: E_INVALID_ARG, not a
    retryable E_UNAVAILABLE crash."""
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "d033-f01")
        for clue in ("v0", "v01", "v1.00"):
            res = await call_tool_raw(
                client,
                tok,
                "memory.drilldown",
                {"project": "d033-f01", "clue_ids": [clue], "token_budget": 1000},
            )
            env = json.loads(res["content"][0]["text"])
            assert res["isError"] is True
            assert env["code"] == "E_INVALID_ARG", env
            assert env["retryable"] is False, env


# --------------------------------------------------------------------------- F06
async def test_f06_invalid_arg_envelope_is_bounded(db_dsn):
    """Validation errors never echo the request (``input``) and the error list is capped."""
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "d033-f06")
        items = [{"kind": "fact", "body": "z" * 64000} for _ in range(50)]  # every item lacks a title
        res = await call_tool_raw(
            client, tok, "memory.write", write_args("d033-f06", items, token_budget=256)
        )
        text = res["content"][0]["text"]
        env = json.loads(text)
        assert env["code"] == "E_INVALID_ARG"
        assert len(text.encode()) < 16_000, f"error envelope is {len(text.encode())} bytes"
        assert "zzzz" not in text
        assert env["details"]["error_count"] == 50
        assert len(env["details"]["errors"]) <= 20
        assert all("input" not in e and "url" not in e for e in env["details"]["errors"])


# --------------------------------------------------------------------------- F12
async def test_f12_oversized_close_is_invalid_arg_with_limit(db_dsn):
    """notes (64000) + decisions overflow the session-note item body: a validation error naming
    the limit, not a retryable internal error."""
    async with running_app(db_dsn) as client:
        tok = await writer_on(client, "d033-f12")
        args = {
            "project": "d033-f12",
            "request_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "client": "x",
            "notes": "x" * 64000,
            "decisions": ["x"],
        }
        res = await call_tool_raw(client, tok, "memory.call_the_day", args)
        env = json.loads(res["content"][0]["text"])
        assert res["isError"] is True
        assert env["code"] == "E_INVALID_ARG", env
        assert env["retryable"] is False
        assert "64000" in env["message"], env


async def test_f12_close_at_the_limit_is_accepted(connect, world, deps):
    """The boundary: a session note of exactly 64000 characters is written."""
    decisions = ["keep it"]
    tail = len("\n\n## Decisions\n- keep it")
    req = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "n" * (64000 - tail),
        "decisions": decisions,
    }
    async with await connect() as conn:
        res = await call_the_day(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
    assert res.versions[0].version_id >= 1


async def test_f12_card_with_too_many_expected_versions_is_invalid_arg(connect, world, deps):
    """The card item links ``$0`` + one ``derived_from`` per expected version; more than the item
    link limit (32) is a validation error, not an internal one."""
    req = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "n",
        "card_update": {"body": "card"},
        "expected_versions": [{"logical_id": i, "version_id": i} for i in range(1, 33)],
    }
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await call_the_day(conn, world.ctx_a, req, deps=deps)
    assert ei.value.code == "E_INVALID_ARG"
    assert "32" in ei.value.message


# --------------------------------------------------------------------------- F08
@pytest.mark.parametrize("fmt", ["device:0{}", "device:00{}"])
async def test_f08_noncanonical_device_scope_is_rejected(connect, world, deps, fmt):
    scope = fmt.format(world.dev_a)
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await write(conn, world.ctx_a, write_req(MAIN, [item("t", "b", device_scope=scope)]), deps=deps)
    assert ei.value.code == "E_INVALID_ARG"
    assert "canonical" in ei.value.message


async def test_f08_lesson_device_scope_is_canonical_too(connect, world, deps):
    req = {
        "project": MAIN,
        "request_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "client": "pytest/0",
        "notes": "n",
        "lessons": [{"title": "l", "body": "b", "device_scope": f"device:0{world.dev_a}"}],
    }
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await call_the_day(conn, world.ctx_a, req, deps=deps)
    assert ei.value.code == "E_INVALID_ARG"


async def test_f08_canonical_device_scope_is_readable_by_the_writer(connect, world, deps, rdeps):
    scope = f"device:{world.dev_a}"
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(MAIN, [item("t", "b", device_scope=scope)]), deps=deps)
        await conn.commit()
        vid = res.versions[0].version_id
        out = await raw(
            conn, world.ctx_a, {"project": MAIN, "version_id": vid, "token_budget": 2000}, deps=rdeps
        )
        await conn.commit()
    assert out["version_id"] == vid
    assert out["device_scope"] == scope


# --------------------------------------------------------------------------- F17
async def test_f17_complete_page_that_fits_is_served(connect, world, deps, rdeps):
    """Partial pages carry a signed cursor, the complete page does not: every budget that holds
    the complete page must serve it, and ``min`` must not overstate the real need."""
    async with await connect() as conn:
        r = await write(conn, world.ctx_a, write_req(MAIN, [item("t", "a " * 410)]), deps=deps)
        await conn.commit()
        vid = r.versions[0].version_id
        assert r.versions[0].chunk_count >= 2

        async def dd(b: int):
            try:
                out = await drilldown(
                    conn,
                    world.ctx_a,
                    {"project": MAIN, "clue_ids": [f"v{vid}"], "token_budget": b},
                    deps=rdeps,
                )
                await conn.commit()
                return out
            except ToolError as e:
                await conn.rollback()
                return e

        full = await dd(32000)
        assert full["next_cursor"] is None
        full_used = full["budget"]["used"]
        for b in range(max(256, full_used), full_used + 200, 7):
            res = await dd(b)
            assert not isinstance(res, ToolError), (b, res.code, res.details)
            assert res["next_cursor"] is None
            assert res["budget"]["used"] <= b
        if full_used - 1 >= 256:
            res = await dd(full_used - 1)
            if isinstance(res, ToolError):  # nothing fits: min is the exact smallest servable size
                assert res.code == "E_BUDGET_TOO_SMALL"
                assert res.details["min"] <= full_used, res.details
            else:
                assert res["budget"]["used"] <= full_used - 1
