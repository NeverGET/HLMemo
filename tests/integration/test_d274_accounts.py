"""D-274: account self-service project creation (threat model: docs/consults/127-d274-threat-model.md).

Every call goes over the real MCP wire with operator-minted tokens, so membership is what the
bearer resolves to (revoked, expired and read-only devices included), never a hand-built context.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.db.replay import rebuild_projections
from hlmemo.ops import service as ops
from tests.integration._import_fixtures import rows, scalar
from tests.integration._mcp_fixtures import call_tool_raw, mcp_rpc, running_app
from tests.integration._write_fixtures import dump_projections

pytestmark = pytest.mark.integration

TOOL = "hlm.project_create"


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


async def _create(client, token: str, slug: str, **kw: Any) -> tuple[bool, dict[str, Any]]:  # noqa: ANN001
    res = await call_tool_raw(client, token, TOOL, {"slug": slug, **kw})
    return bool(res.get("isError")), _payload(res)


async def _ops(connect, fn, *a: Any, **kw: Any) -> Any:  # noqa: ANN001
    async with await connect() as conn:
        out = await fn(conn, *a, **kw)
        await conn.commit()
    return out


async def _mint(connect, name: str, *grants: str) -> str:  # noqa: ANN001
    minted = await _ops(connect, ops.mint, name=name, device_class="personal", grants=list(grants))
    return minted.token


async def _account(connect, *, quota: int | None = None) -> None:  # noqa: ANN001
    """maher-global as the root, maher-sandbox claimed; an owner project beside it."""
    for slug in ("maher-global", "maher-sandbox", "secret-proj"):
        await _ops(connect, ops.project_create, slug, slug.title())
    with pytest.raises(HlmError) as exc:  # T9: an untagged project with the prefix needs --claim
        await _ops(connect, ops.account_set, "maher")
    assert exc.value.code == "E_INVALID_ARG" and exc.value.details["projects"] == ["maher-sandbox"]
    out = await _ops(connect, ops.account_set, "maher", quota=quota, claim=True)
    assert out["claimed"] == ["maher-sandbox"] and out["is_root"]


async def _grants(connect, slug: str) -> dict[int, str]:  # noqa: ANN001
    got = await rows(
        connect,
        "SELECT g.device_id, g.role FROM device_project_grants g JOIN projects p USING (project_id)"
        " WHERE p.slug = %s AND g.revoked_at IS NULL ORDER BY g.device_id",
        (slug,),
    )
    return {int(d): r for d, r in got}


async def _device_id(connect, name: str) -> int:  # noqa: ANN001
    return int(await scalar(connect, "SELECT device_id FROM devices WHERE name = %s", (name,)))


async def test_a_member_creates_a_project_granted_to_every_live_member(db_dsn, connect) -> None:
    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    await _mint(connect, "maher-pad", "maher-global:admin")
    await _mint(connect, "maher-reader", "maher-global:read")
    await _mint(connect, "owner-dev", "secret-proj:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "maher-api", name="Maher API")
        assert not err, out
        assert out["created"] and out["account"] == "maher" and out["quota"] == {"limit": 20, "used": 2}
        # the claimed sandbox counts; the root does not
        listed = await mcp_rpc(client, mac, "tools/list", {})
        assert TOOL not in [t["name"] for t in listed.json()["result"]["tools"]]  # T7

        again_err, again = await _create(client, mac, "maher-api")  # idempotent, no grant change
        assert not again_err and again["created"] is False

    mac_id, pad_id = await _device_id(connect, "maher-mac"), await _device_id(connect, "maher-pad")
    assert await _grants(connect, "maher-api") == {1: "admin", mac_id: "write", pad_id: "write"}  # T4
    policy = await scalar(connect, "SELECT policy FROM projects WHERE slug = 'maher-api'")
    assert policy.get("account") == "maher" and "account_root" not in policy
    kinds = await rows(
        connect,
        "SELECT e.kind FROM events e JOIN projects p USING (project_id) WHERE p.slug = 'maher-api'"
        " ORDER BY e.event_id",
    )
    assert [k for (k,) in kinds] == ["project_created", "grant_added", "grant_added", "grant_added", "write"]
    assert await scalar(connect, "SELECT count(*) FROM projects WHERE slug = 'maher-api'") == 1

    shown = await _ops(connect, ops.account_show, "maher")
    assert shown["used"] == 2 and [m["name"] for m in shown["members"]] == ["maher-mac", "maher-pad"]

    async with await connect() as conn:  # T8: replay keeps the projections and the account keys
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
    root_policy = await scalar(connect, "SELECT policy FROM projects WHERE slug = 'maher-global'")
    assert root_policy["account_root"] is True and root_policy["project_quota"] == 20


async def test_non_members_and_foreign_slugs_are_refused_alike(db_dsn, connect) -> None:
    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    reader = await _mint(connect, "maher-reader", "maher-global:read")
    owner = await _mint(connect, "owner-dev", "secret-proj:write")
    # T3: a forged root (account tag without account_root, or a string "true") is no root
    for slug, policy in (
        ("evil-global", {"account": "evil"}),
        ("fake-global", {"account": "fake", "account_root": "true"}),
    ):
        await _ops(connect, ops.project_create, slug, slug)
        async with await connect() as conn:
            await conn.execute(
                "UPDATE projects SET policy = policy || %s::jsonb WHERE slug = %s", (json.dumps(policy), slug)
            )
            await conn.commit()
    evil = await _mint(connect, "evil-dev", "evil-global:write", "fake-global:write")
    async with running_app(db_dsn) as client:
        refusals = [
            await _create(client, owner, "maher-x"),  # T2: no grant on the root
            await _create(client, reader, "maher-x"),  # T2: read is not membership
            await _create(client, mac, "secret-proj"),  # T1: no account named "secret"
            await _create(client, mac, "secret-x"),
            await _create(client, mac, "hlm-x"),  # reserved account name
            await _create(client, mac, "maher-global"),  # the root itself
            await _create(client, evil, "evil-x"),  # T3
            await _create(client, evil, "fake-x"),  # T3
        ]
    messages = {out["message"] for err, out in refusals if err}
    assert all(err for err, _ in refusals) and {out["code"] for _, out in refusals} == {"E_FORBIDDEN"}
    assert len(messages) == 1  # T6: one message for every non-member cause
    for slug in ("maher-x", "secret-x", "hlm-x", "evil-x", "fake-x"):
        assert await scalar(connect, "SELECT count(*) FROM projects WHERE slug = %s", (slug,)) == 0
    assert await _grants(connect, "secret-proj") == {
        1: "admin",
        await _device_id(connect, "owner-dev"): "write",
    }


async def test_an_untagged_slug_with_the_prefix_is_never_granted(db_dsn, connect) -> None:
    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    await _ops(connect, ops.project_create, "maher-ops", "made by the operator later")  # T1: unclaimed
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "maher-ops")
    assert err and out["code"] == "E_FORBIDDEN" and "does not belong to your account" in out["message"]
    assert await _grants(connect, "maher-ops") == {1: "admin"}


async def test_revoked_expired_and_ungranted_devices(db_dsn, connect) -> None:
    settings = get_settings(db_dsn=db_dsn)
    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    pad = await _mint(connect, "maher-pad", "maher-global:write")
    await _mint(connect, "maher-old", "maher-global:write")
    async with await connect() as conn:  # an expired member: never granted, cannot call
        await conn.execute(
            "UPDATE devices SET expires_at = now() - interval '1 hour' WHERE name = 'maher-old'"
        )
        await conn.commit()
    await _ops(connect, ops.revoke, "maher-pad", settings=settings)
    async with running_app(db_dsn) as client:
        r = await mcp_rpc(client, pad, "tools/call", {"name": TOOL, "arguments": {"slug": "maher-two"}})
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"  # the pre-body gate refuses it
        err, out = await _create(client, mac, "maher-two")
        assert not err, out
        await _ops(connect, ops.ungrant, "maher-mac", "maher-global")  # a revoked root grant ends membership
        err, out = await _create(client, mac, "maher-three")
        assert err and out["code"] == "E_FORBIDDEN"
    assert await _grants(connect, "maher-two") == {
        1: "admin",
        await _device_id(connect, "maher-mac"): "write",
    }


async def test_the_quota_holds_under_concurrent_creates(db_dsn, connect) -> None:
    await _account(connect, quota=3)  # the claimed sandbox already uses one
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    async with running_app(db_dsn) as client:
        err, _ = await _create(client, mac, "maher-one")
        assert not err
        results = await asyncio.gather(*(_create(client, mac, f"maher-race-{i}") for i in range(4)))  # T5
        err, out = await _create(client, mac, "maher-late")
    assert [e for e, _ in results].count(False) == 1
    assert (
        err and out["code"] == "E_FORBIDDEN" and out["details"] == {"account": "maher", "quota": 3, "used": 3}
    )
    assert await scalar(connect, "SELECT count(*) FROM projects WHERE policy->>'account' = 'maher'") == 4


async def test_account_set_refuses_a_foreign_tag_and_bad_names(connect) -> None:
    await _account(connect)
    await _ops(connect, ops.project_create, "maherx-global", "x")
    for bad in ("maher-x", "hlm", "a", "Maher"):
        with pytest.raises(HlmError) as exc:
            await _ops(connect, ops.account_set, bad)
        assert exc.value.code in ("E_INVALID_ARG", "E_NOT_FOUND")
    async with await connect() as conn:  # a project with the prefix tagged for someone else stops it
        await conn.execute(
            "UPDATE projects SET policy = policy || '{\"account\": \"other\"}' WHERE slug = 'maher-sandbox'"
        )
        await conn.commit()
    with pytest.raises(HlmError) as exc:
        await _ops(connect, ops.account_set, "maher", claim=True)
    assert exc.value.code == "E_FORBIDDEN" and exc.value.details["projects"] == ["maher-sandbox"]


async def test_a_concurrent_member_removal_waits_for_the_create(connect) -> None:
    """Review 127 S1: the members read holds their grant and device rows, so a revoke that lands
    between that read and the grants waits instead of leaving a removed member granted."""
    import psycopg

    from hlmemo.core import account_service as acc

    await _account(connect)
    await _mint(connect, "maher-pad", "maher-global:write")
    root_id = int(await scalar(connect, "SELECT project_id FROM projects WHERE slug = 'maher-global'"))
    pad_id = await _device_id(connect, "maher-pad")
    revokes = (
        (
            "UPDATE device_project_grants SET revoked_at = now() WHERE device_id = %s AND project_id = %s",
            (pad_id, root_id),
        ),
        ("UPDATE devices SET status = 'revoked' WHERE device_id = %s", (pad_id,)),
    )
    async with await connect() as creator:
        assert await acc.live_members(creator, root_id) == [pad_id]  # transaction open, rows held
        for sql, params in revokes:
            async with await connect() as operator:
                await operator.execute("SET lock_timeout = '300ms'")  # inside its own transaction
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    await operator.execute(sql, params)
        await creator.rollback()


async def test_a_failure_after_the_insert_leaves_nothing(db_dsn, connect, monkeypatch) -> None:  # noqa: ANN001
    """Review 127 C1: the tool runs in a savepoint; a failure after the insert undoes it all."""
    from hlmemo.core import account_service as acc

    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    events_before = await scalar(connect, "SELECT count(*) FROM events")

    async def broken(*_a: Any, **_kw: Any) -> int:
        raise RuntimeError("card writer down")

    monkeypatch.setattr(acc, "write_skeleton_card", broken)
    async with running_app(db_dsn) as client:
        err, _out = await _create(client, mac, "maher-half")
    assert err
    assert await scalar(connect, "SELECT count(*) FROM projects WHERE slug = 'maher-half'") == 0
    assert await scalar(connect, "SELECT count(*) FROM events") == events_before
    assert (await _ops(connect, ops.account_show, "maher"))["used"] == 1


async def test_device_one_an_archived_root_and_a_quota_change(db_dsn, connect) -> None:
    from tests.integration._mcp_fixtures import ADMIN_TOKEN

    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, ADMIN_TOKEN, "maher-x")  # device 1: the ops path, not this tool
        assert err and out["code"] == "E_FORBIDDEN"
        async with await connect() as conn:
            await conn.execute("UPDATE projects SET archived_at = now() WHERE slug = 'maher-global'")
            await conn.commit()
        err, out = await _create(client, mac, "maher-y")  # an archived root is no root
        assert err and out["code"] == "E_FORBIDDEN"
    async with await connect() as conn:
        await conn.execute("UPDATE projects SET archived_at = NULL WHERE slug = 'maher-global'")
        await conn.commit()
    out = await _ops(connect, ops.account_set, "maher", quota=5)
    assert out["quota"] == 5 and out["previous"]["project_quota"] == 20 and out["claimed"] == []


async def test_input_edges(db_dsn, connect) -> None:
    await _account(connect)
    mac = await _mint(connect, "maher-mac", "maher-global:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "maher-x\n")  # `$` would match before a newline
        assert err and out["code"] == "E_INVALID_ARG"
        err, out = await _create(client, mac, "maher-sandbox")  # idempotent: the slug, nothing more
        assert not err and out == {"project": {"slug": "maher-sandbox"}, "created": False, "account": "maher"}
    with pytest.raises(HlmError):
        await _ops(connect, ops.account_set, "maher\n")
