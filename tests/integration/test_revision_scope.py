"""F13: revision, close pins and cards authorize all current segments before disclosure."""

from __future__ import annotations

import uuid

import pytest

from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import call_the_day, default_deps, write
from tests.integration._write_fixtures import (
    OTHER,
    count,
    dump_projections,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect):
    async with await connect() as conn:
        return await seed_world(conn)


async def _snapshot(conn):
    return await dump_projections(conn), await count(conn, "events")


@pytest.mark.parametrize("scope_kind", ["device", "class"])
@pytest.mark.parametrize("caller", ["ctx_b", "ctx_admin"])
@pytest.mark.parametrize("correct_head", [False, True])
async def test_hidden_revision_matches_missing_and_is_atomic(
    connect, world, deps, scope_kind, caller, correct_head
):
    scope = f"device:{world.dev_a}" if scope_kind == "device" else "class:personal"
    async with await connect() as conn:
        initial = await write(
            conn, world.ctx_a, write_req(OTHER, [item("private", "secret", device_scope=scope)]), deps=deps
        )
        await conn.commit()
        target = initial.versions[0]
        before = await _snapshot(conn)
        errors = []
        for logical_id in [target.logical_id, target.logical_id + 1_000_000]:
            with pytest.raises(ToolError) as exc:
                await write(
                    conn,
                    getattr(world, caller),
                    write_req(
                        OTHER,
                        [
                            item("new", "must roll back"),
                            item(
                                "overwrite",
                                "public replacement",
                                logical_id=logical_id,
                                expected_version_id=target.version_id + (0 if correct_head else 1000),
                            ),
                        ],
                    ),
                    deps=deps,
                )
            errors.append((exc.value.code, str(exc.value), exc.value.details))
            assert await _snapshot(conn) == before
        assert errors[0] == errors[1]
        assert errors[0][0] == "E_NOT_FOUND"


@pytest.mark.parametrize("target_kind", ["pin", "card"])
@pytest.mark.parametrize("scope_kind", ["device", "class"])
@pytest.mark.parametrize("correct_head", [False, True])
async def test_close_hidden_pin_or_card_never_writes(
    connect, world, deps, target_kind, scope_kind, correct_head
):
    scope = f"device:{world.dev_a}" if scope_kind == "device" else "class:personal"
    async with await connect() as conn:
        initial = await write(
            conn,
            world.ctx_a,
            write_req(
                OTHER,
                [
                    item(
                        "private",
                        "secret",
                        kind="project_card" if target_kind == "card" else "fact",
                        device_scope="all" if target_kind == "card" else scope,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        target = initial.versions[0]
        if target_kind == "card":
            # Legacy malformed rows still fail closed; new writes reject private cards.
            await conn.execute(
                "UPDATE memory_versions SET device_scope = %s WHERE version_id = %s",
                (scope, target.version_id),
            )
            await conn.commit()
        head = target.version_id + (0 if correct_head else 1000)
        req = {
            "project": OTHER,
            "request_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "client": "pytest/0",
            "notes": "must not be persisted",
        }
        if target_kind == "pin":
            req["expected_versions"] = [{"logical_id": target.logical_id, "version_id": head}]
        else:
            req["card_update"] = {"body": "replacement", "expected_version_id": head}
        before = await _snapshot(conn)
        with pytest.raises(ToolError) as exc:
            await call_the_day(conn, world.ctx_b, req, deps=deps)
        assert exc.value.code == "E_NOT_FOUND"
        assert "current_version_id" not in exc.value.details
        assert await _snapshot(conn) == before


@pytest.mark.parametrize("operation", ["write", "close"])
async def test_visible_head_does_not_authorize_hidden_survivor_segments(connect, world, deps, operation):
    async with await connect() as conn:
        initial = await write(
            conn,
            world.ctx_a,
            write_req(
                OTHER,
                [
                    item(
                        "private interval",
                        "secret",
                        device_scope=f"device:{world.dev_a}",
                        valid_from="2026-09-01T00:00:00Z",
                        valid_to="2026-09-04T00:00:00Z",
                    )
                ],
            ),
            deps=deps,
        )
        first = initial.versions[0]
        public = await write(
            conn,
            world.ctx_a,
            write_req(
                OTHER,
                [
                    item(
                        "public head",
                        "public",
                        logical_id=first.logical_id,
                        expected_version_id=first.version_id,
                        valid_from="2026-09-02T00:00:00Z",
                        valid_to="2026-09-03T00:00:00Z",
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        assert await count(conn, "memory_versions", "superseded_at = 'infinity'") == 3
        head = public.versions[0].version_id
        before = await _snapshot(conn)
        with pytest.raises(ToolError) as exc:
            if operation == "write":
                await write(
                    conn,
                    world.ctx_b,
                    write_req(
                        OTHER,
                        [item("replace", "body", logical_id=first.logical_id, expected_version_id=head)],
                    ),
                    deps=deps,
                )
            else:
                await call_the_day(
                    conn,
                    world.ctx_b,
                    {
                        "project": OTHER,
                        "request_id": str(uuid.uuid4()),
                        "session_id": str(uuid.uuid4()),
                        "client": "pytest/0",
                        "notes": "must not be persisted",
                        "expected_versions": [{"logical_id": first.logical_id, "version_id": head}],
                    },
                    deps=deps,
                )
        assert exc.value.code == "E_NOT_FOUND"
        assert await _snapshot(conn) == before


async def test_revision_replay_reauthorizes_changed_device_scope(connect, world, deps):
    async with await connect() as conn:
        initial = await write(conn, world.ctx_a, write_req(OTHER, [item("public", "body")]), deps=deps)
        target = initial.versions[0]
        req = write_req(
            OTHER,
            [
                item(
                    "now private to b",
                    "body",
                    logical_id=target.logical_id,
                    expected_version_id=target.version_id,
                    device_scope=f"device:{world.dev_b}",
                    valid_from="2026-09-01T00:00:00Z",
                )
            ],
        )
        await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        before = await _snapshot(conn)
        with pytest.raises(ToolError) as exc:
            await write(conn, world.ctx_a, req, deps=deps)
        assert exc.value.code == "E_NOT_FOUND"
        assert await _snapshot(conn) == before
