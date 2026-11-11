"""D-279: the owner account (threat model: docs/consults/130-d279-threat-model.md).

A member of the owner account (a write grant on ``owner-global``, flagged by ``ops account set owner``)
creates unprefixed projects that are ordinary default-group projects. Every call goes over the real
MCP wire with operator-minted tokens, as in the D-274 tests, whose helpers and guest account are reused.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from hlmemo.auth.errors import HlmError
from hlmemo.config import get_settings
from hlmemo.core import account_service as acc
from hlmemo.db.librarian_queries import isolation_groups
from hlmemo.db.replay import rebuild_projections
from hlmemo.ops import service as ops
from tests.integration._import_fixtures import rows, scalar
from tests.integration._mcp_fixtures import ADMIN_TOKEN, mcp_rpc, running_app
from tests.integration._write_fixtures import dump_projections
from tests.integration.test_d274_accounts import TOOL, _account, _create, _device_id, _grants, _mint, _ops

pytestmark = pytest.mark.integration

NOT_MEMBER = acc._NOT_MEMBER  # noqa: SLF001 - the one refusal both paths must share


async def _owner(connect, *, quota: int | None = None) -> None:  # noqa: ANN001
    """The guest account of the D-274 tests (maher) plus the owner account's root."""
    await _account(connect)
    await _ops(connect, ops.project_create, acc.OWNER_ROOT, "Owner account")
    out = await _ops(connect, ops.account_set, "owner", quota=quota)
    assert out["is_root"] and out["root"] == "owner-global" and out["claimed"] == []


async def _pid(connect, slug: str) -> int:  # noqa: ANN001
    return int(await scalar(connect, "SELECT project_id FROM projects WHERE slug = %s", (slug,)))


async def _count(connect, slug: str) -> int:  # noqa: ANN001
    return int(await scalar(connect, "SELECT count(*) FROM projects WHERE slug = %s", (slug,)))


async def test_an_owner_member_creates_an_unprefixed_default_group_project(db_dsn, connect) -> None:
    await _owner(connect)
    mac = await _mint(connect, "owner-mac", "owner-global:write", "secret-proj:write")
    await _mint(connect, "owner-work", "owner-global:admin")
    await _mint(connect, "owner-reader", "owner-global:read")
    guest = await _mint(connect, "maher-mac", "maher-global:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "atlasapp", name="Atlasapp")
        assert not err, out
        assert out["created"] and out["account"] == "owner" and out["granted"] == 2
        assert out["quota"] == {"limit": 20, "used": 1} and out["project"]["slug"] == "atlasapp"
        listed = await mcp_rpc(client, mac, "tools/list", {})
        assert TOOL not in [t["name"] for t in listed.json()["result"]["tools"]]  # T9

        err, again = await _create(client, mac, "atlasapp")  # idempotent, nothing beyond the slug
        assert not err and again == {"project": {"slug": "atlasapp"}, "created": False, "account": "owner"}
        err, out = await _create(client, mac, "foo-bar")  # a hyphenated slug outside every account
        assert not err and out["quota"] == {"limit": 20, "used": 2}
        err, out = await _create(client, guest, "maher-api")  # the D-274 path is unchanged beside it
        assert not err and out["account"] == "maher"

    mac_id, work_id = await _device_id(connect, "owner-mac"), await _device_id(connect, "owner-work")
    assert await _grants(connect, "atlasapp") == {1: "admin", mac_id: "write", work_id: "write"}  # T4
    assert await _grants(connect, "maher-api") == {
        1: "admin",
        await _device_id(connect, "maher-mac"): "write",
    }
    policy = await scalar(connect, "SELECT policy FROM projects WHERE slug = 'atlasapp'")
    assert policy.get("owner_created") is True and "account" not in policy and "owner_root" not in policy
    kinds = await rows(
        connect,
        "SELECT e.kind FROM events e JOIN projects p USING (project_id) WHERE p.slug = 'atlasapp'"
        " ORDER BY e.event_id",
    )
    assert [k for (k,) in kinds] == ["project_created", "grant_added", "grant_added", "grant_added", "write"]

    # T5: an owner-created project is an ordinary default-group project, like the operator-made one;
    # the guest account keeps its own group, and the owner root carries no account tag either
    ids = {
        s: await _pid(connect, s) for s in ("atlasapp", "foo-bar", "secret-proj", "owner-global", "maher-api")
    }
    async with await connect() as conn:
        groups = await isolation_groups(conn, list(ids.values()))
    assert groups == {ids["maher-api"]: "a:maher"}

    shown = await _ops(connect, ops.account_show, "owner")
    assert shown["used"] == 2 and shown["projects"] == ["atlasapp", "foo-bar"] and shown["is_root"]
    assert [m["name"] for m in shown["members"]] == ["owner-mac", "owner-work"]
    assert (await _ops(connect, ops.account_show, "maher"))["projects"] == [
        "maher-global",
        "maher-sandbox",
        "maher-api",
    ]

    async with await connect() as conn:  # T10: replay keeps the projections and the new keys
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        assert await dump_projections(conn) == before
    root_policy = await scalar(connect, "SELECT policy FROM projects WHERE slug = 'owner-global'")
    assert root_policy["owner_root"] is True and root_policy["project_quota"] == 20
    assert "account" not in root_policy
    assert (await scalar(connect, "SELECT policy FROM projects WHERE slug = 'atlasapp'"))[
        "owner_created"
    ] is True


async def test_non_members_get_the_one_refusal_and_nothing_is_created(db_dsn, connect) -> None:
    await _owner(connect)
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    guest = await _mint(connect, "maher-mac", "maher-global:write")
    plain = await _mint(connect, "owner-dev", "secret-proj:write")
    reader = await _mint(connect, "owner-reader", "owner-global:read")
    # T3: a project that is not owner-global does not become the root by carrying the key
    await _ops(connect, ops.project_create, "evil-proj", "evil")
    async with await connect() as conn:
        await conn.execute(
            "UPDATE projects SET policy = policy || '{\"owner_root\": true}' WHERE slug = 'evil-proj'"
        )
        await conn.commit()
    evil = await _mint(connect, "evil-dev", "evil-proj:write")
    async with running_app(db_dsn) as client:
        calls = [
            (guest, "atlasapp"),  # T1: a guest account's member
            (guest, "foo-bar"),
            (guest, "hlm-x"),  # a non-member never learns the slug rules
            (guest, "x-global"),
            (plain, "atlasapp"),  # T1: grants on an owner project, none on the root
            (reader, "atlasapp"),  # T1: read is not membership
            (evil, "atlasapp"),  # T3
            (ADMIN_TOKEN, "atlasapp"),  # T1: device 1 uses the ops path, not this tool
            (mac, "maher-x"),  # T2: an owner member has no say in an account's namespace
            (mac, "maher-"),  # T2 (review 130 C1): an empty rest is still that account's namespace
            (guest, "maher-"),  # and no account member creates it either, as before D-279
            (mac, "maher-sandbox"),  # T2/T4: nor over an existing guest project
        ]
        refusals = [(slug, await _create(client, token, slug)) for token, slug in calls]
    for slug, (err, out) in refusals:  # T8: one code, one message, the same details shape
        assert err and out["code"] == "E_FORBIDDEN" and out["message"] == NOT_MEMBER, (slug, out)
        assert out["details"] == {"slug": slug}
    for slug in ("atlasapp", "foo-bar", "hlm-x", "x-global", "maher-x", "maher-"):
        assert await _count(connect, slug) == 0
    await _ops(connect, ops.account_set, "maher", quota=9)  # the account's namespace is still clean
    assert await _grants(connect, "maher-sandbox") == {1: "admin"}  # the owner member gained nothing


async def test_no_root_a_forged_root_and_an_archived_root(db_dsn, connect) -> None:
    await _account(connect)
    await _ops(connect, ops.project_create, acc.OWNER_ROOT, "Owner account")  # exists, not yet flagged
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "atlasapp")  # T3: only `ops account set owner` makes the root
        assert err and out["message"] == NOT_MEMBER
        async with await connect() as conn:  # a string is not the flag
            await conn.execute(
                "UPDATE projects SET policy = policy || %s::jsonb WHERE slug = 'owner-global'",
                (json.dumps({"owner_root": "true"}),),
            )
            await conn.commit()
        err, out = await _create(client, mac, "atlasapp")
        assert err and out["message"] == NOT_MEMBER

        await _ops(connect, ops.account_set, "owner")
        async with await connect() as conn:
            await conn.execute("UPDATE projects SET archived_at = now() WHERE slug = 'owner-global'")
            await conn.commit()
        err, out = await _create(client, mac, "atlasapp")  # an archived root is no root
        assert err and out["message"] == NOT_MEMBER
        assert (await _ops(connect, ops.account_show, "owner"))["is_root"] is False
        async with await connect() as conn:
            await conn.execute("UPDATE projects SET archived_at = NULL WHERE slug = 'owner-global'")
            await conn.commit()
        err, out = await _create(client, mac, "atlasapp")
        assert not err and out["created"]
    assert await _count(connect, "atlasapp") == 1


async def test_a_member_cannot_create_reserved_existing_or_account_slugs(db_dsn, connect) -> None:
    await _owner(connect)
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    # T2: `evil-global` exists but is no account root, and maher's root gets archived below: both
    # prefixes stay an account's namespace
    await _ops(connect, ops.project_create, "evil-global", "evil")
    async with running_app(db_dsn) as client:
        reserved = ["hlm-x", "owner-x", "admin-x", "librarian-x", "mig-x", "system-x", "root-x"]
        for slug in reserved:  # T6
            err, out = await _create(client, mac, slug)
            assert err and out["code"] == "E_FORBIDDEN" and "reserved system namespace" in out["message"], (
                slug
            )
        for slug in ("x-global", "new-thing-global"):  # T6: roots are the operator's
            err, out = await _create(client, mac, slug)
            assert err and out["code"] == "E_FORBIDDEN" and "-global" in out["message"], slug
        err, out = await _create(client, mac, "owner-global")  # the root itself: reserved, and it exists
        assert err and out["code"] == "E_FORBIDDEN" and "reserved system namespace" in out["message"]
        err, out = await _create(client, mac, "secret-proj")  # T4: an operator-made project is not joined
        assert err and out["code"] == "E_FORBIDDEN" and "ask the operator" in out["message"]
        err, out = await _create(client, mac, "evil-x")
        assert err and out["message"] == NOT_MEMBER
        async with await connect() as conn:
            await conn.execute("UPDATE projects SET archived_at = now() WHERE slug = 'maher-global'")
            await conn.commit()
        err, out = await _create(client, mac, "maher-x")
        assert err and out["message"] == NOT_MEMBER
        err, out = await _create(client, mac, "nobody-")  # no such account: still not a slug to create
        assert err and out["code"] == "E_FORBIDDEN" and "hyphen" in out["message"]
        err, out = await _create(client, mac, "Atlasapp")
        assert err and out["code"] == "E_INVALID_ARG"
        err, out = await _create(client, mac, "atlasapp\n")
        assert err and out["code"] == "E_INVALID_ARG"
    for slug in (*reserved, "x-global", "new-thing-global", "evil-x", "maher-x", "nobody-"):
        assert await _count(connect, slug) == 0
    assert await _grants(connect, "secret-proj") == {1: "admin"}
    assert (await _ops(connect, ops.account_show, "owner"))["used"] == 0


async def test_revoked_expired_and_ungranted_owner_devices(db_dsn, connect) -> None:
    settings = get_settings(db_dsn=db_dsn)
    await _owner(connect)
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    work = await _mint(connect, "owner-work", "owner-global:write")
    await _mint(connect, "owner-old", "owner-global:write")
    async with await connect() as conn:  # an expired member: never granted, cannot call
        await conn.execute(
            "UPDATE devices SET expires_at = now() - interval '1 hour' WHERE name = 'owner-old'"
        )
        await conn.commit()
    await _ops(connect, ops.revoke, "owner-work", settings=settings)
    async with running_app(db_dsn) as client:
        r = await mcp_rpc(client, work, "tools/call", {"name": TOOL, "arguments": {"slug": "two"}})
        assert r.status_code == 401 and r.json()["code"] == "E_AUTH"
        err, out = await _create(client, mac, "project-two")
        assert not err, out
        await _ops(connect, ops.ungrant, "owner-mac", "owner-global")  # a revoked root grant ends membership
        err, out = await _create(client, mac, "project-three")
        assert err and out["message"] == NOT_MEMBER
    assert await _grants(connect, "project-two") == {
        1: "admin",
        await _device_id(connect, "owner-mac"): "write",
    }
    assert await _count(connect, "project-three") == 0


async def test_the_owner_quota_holds_under_concurrent_creates(db_dsn, connect) -> None:
    await _owner(connect, quota=2)  # operator-made projects (secret-proj, the roots) never count
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    async with running_app(db_dsn) as client:
        err, _ = await _create(client, mac, "first")
        assert not err
        results = await asyncio.gather(*(_create(client, mac, f"race-{i}") for i in range(4)))  # T7
        err, out = await _create(client, mac, "late")
        again_err, again = await _create(client, mac, "first")  # an idempotent re-create is not counted
    assert [e for e, _ in results].count(False) == 1
    assert err and out["code"] == "E_FORBIDDEN"
    assert out["details"] == {"account": "owner", "quota": 2, "used": 2}
    assert not again_err and again["created"] is False
    assert await scalar(connect, "SELECT count(*) FROM projects WHERE policy->>'owner_created' = 'true'") == 2


async def test_account_set_owner_rules_and_no_claim_of_owner_projects(db_dsn, connect) -> None:
    await _account(connect)
    with pytest.raises(HlmError) as exc:  # the root must exist first
        await _ops(connect, ops.account_set, "owner")
    assert exc.value.code == "E_NOT_FOUND"
    await _ops(connect, ops.project_create, acc.OWNER_ROOT, "Owner account")
    for kw in ({"claim": True}, {"quota": 0}, {"quota": acc.MAX_QUOTA + 1}):
        with pytest.raises(HlmError) as exc:
            await _ops(connect, ops.account_set, "owner", **kw)
        assert exc.value.code == "E_INVALID_ARG"
    for sql in (
        "UPDATE projects SET archived_at = now() WHERE slug = 'owner-global'",
        'UPDATE projects SET archived_at = NULL, policy = policy || \'{"account": "x"}\''
        " WHERE slug = 'owner-global'",
        "UPDATE projects SET policy = (policy - 'account') || '{\"reserved\": true}'"
        " WHERE slug = 'owner-global'",
    ):  # review 130 C4: an archived, account-tagged or reserved project is never flagged
        async with await connect() as conn:
            await conn.execute(sql)
            await conn.commit()
        with pytest.raises(HlmError) as exc:
            await _ops(connect, ops.account_set, "owner")
        assert exc.value.code == "E_FORBIDDEN"
    async with await connect() as conn:
        await conn.execute("UPDATE projects SET policy = policy - 'reserved' WHERE slug = 'owner-global'")
        await conn.commit()
    assert "owner_root" not in await scalar(
        connect, "SELECT policy FROM projects WHERE slug = 'owner-global'"
    )
    out = await _ops(connect, ops.account_set, "owner", quota=7)
    assert out["quota"] == 7 and out["previous"] == {"owner_root": None, "project_quota": None}
    out = await _ops(connect, ops.account_set, "owner")  # running it again keeps the quota
    assert out["quota"] == 7 and out["previous"] == {"owner_root": True, "project_quota": 7}
    op = await scalar(
        connect,
        "SELECT payload->'request'->>'op' FROM events WHERE kind = 'librarian'"
        " ORDER BY event_id DESC LIMIT 1",
    )
    assert op == "set_owner_account"
    for bad in ("hlm", "admin", "root"):  # the other reserved names stay refused
        with pytest.raises(HlmError):
            await _ops(connect, ops.account_set, bad)

    mac = await _mint(connect, "owner-mac", "owner-global:write")
    async with running_app(db_dsn) as client:
        err, out = await _create(client, mac, "foo-bar")
        assert not err, out
    # a member who joins later re-creates an existing owner project: the slug, no grant
    late = await _mint(connect, "owner-late", "owner-global:write")
    await _ops(
        connect, ops.account_set, "owner", quota=1
    )  # a quota below what is used stops new creates only
    async with running_app(db_dsn) as client:
        err, out = await _create(client, late, "foo-bar")
        assert not err and out == {"project": {"slug": "foo-bar"}, "created": False, "account": "owner"}
        err, out = await _create(client, late, "another")
        assert err and out["details"] == {"account": "owner", "quota": 1, "used": 1}
    assert await _grants(connect, "foo-bar") == {1: "admin", await _device_id(connect, "owner-mac"): "write"}
    # T6: a later account with that prefix never claims the owner's project, --claim or not
    await _ops(connect, ops.project_create, "foo-global", "Foo")
    for claim in (False, True):
        with pytest.raises(HlmError) as exc:
            await _ops(connect, ops.account_set, "foo", claim=claim)
        assert exc.value.code == "E_FORBIDDEN" and exc.value.details["projects"] == ["foo-bar"]
    policy = await scalar(connect, "SELECT policy FROM projects WHERE slug = 'foo-bar'")
    assert "account" not in policy
    async with running_app(db_dsn) as client:  # and the prefix is now an account's namespace (T2)
        err, out = await _create(client, mac, "foo-baz")
        assert err and out["message"] == NOT_MEMBER


async def test_a_failure_after_the_insert_leaves_nothing_on_the_owner_path(
    db_dsn, connect, monkeypatch
) -> None:  # noqa: ANN001
    await _owner(connect)
    mac = await _mint(connect, "owner-mac", "owner-global:write")
    events_before = await scalar(connect, "SELECT count(*) FROM events")

    async def broken(*_a: Any, **_kw: Any) -> int:
        raise RuntimeError("card writer down")

    monkeypatch.setattr(acc, "write_skeleton_card", broken)
    async with running_app(db_dsn) as client:
        err, _out = await _create(client, mac, "half-made")
    assert err
    assert await _count(connect, "half-made") == 0  # T11
    assert await scalar(connect, "SELECT count(*) FROM events") == events_before
    assert (await _ops(connect, ops.account_show, "owner"))["used"] == 0


async def test_a_non_member_never_waits_for_the_owner_root_lock(db_dsn, connect) -> None:
    """T8: the refusal comes from unlocked reads, so it returns while the root row is locked."""
    await _owner(connect)
    guest = await _mint(connect, "maher-mac", "maher-global:write")
    async with await connect() as holder:
        await holder.execute("SELECT 1 FROM projects WHERE slug = 'owner-global' FOR UPDATE")
        async with running_app(db_dsn) as client:
            err, out = await asyncio.wait_for(_create(client, guest, "atlasapp"), timeout=5)
        await holder.rollback()
    assert err and out["message"] == NOT_MEMBER


def test_owner_slug_rules_are_pure() -> None:
    assert acc.owner_slug_refusal("atlasapp") is None and acc.owner_slug_refusal("foo-bar") is None
    assert acc.owner_slug_refusal("globalist") is None and acc.owner_slug_refusal("my-global-app") is None
    for slug in ("hlm-global", "hlm-librarian", "owner-global", "mig-scratch", "x-global", "hlm"):
        assert acc.owner_slug_refusal(slug) is not None, slug
    assert acc.account_of("owner-x") is None  # no slug prefix ever maps to the owner account
    assert acc.owner_slug_refusal("nobody-") is not None
    assert acc.namespace_of("maher-") == "maher" and acc.account_of("maher-") is None  # review 130 C1
    assert acc.namespace_of("maher-x") == "maher" and acc.namespace_of("maher-global") == "maher"
    for slug in ("atlasapp", "hlm-x", "owner-x", "a-b", "x" * 33 + "-y", "Maher-x"):
        assert acc.namespace_of(slug) is None, slug
    row = {"slug": "owner-global", "archived_at": None, "policy": {"owner_root": True}}
    assert acc.is_owner_root(row)
    assert not acc.is_owner_root({**row, "slug": "evil-proj"})
    assert not acc.is_owner_root({**row, "policy": {"owner_root": "true"}})
    assert not acc.is_owner_root({**row, "policy": {"account": "owner", "account_root": True}})
    assert not acc.is_owner_root({**row, "archived_at": "2026-01-01"}) and not acc.is_owner_root(None)
    assert json.dumps({acc.OWNER_CREATED_KEY: True}) == '{"owner_created": true}'
