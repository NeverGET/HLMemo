"""F13: a revision of an item whose device_scope hides it from the caller discloses the head and lets the caller supersede it."""
from docs.bakeoff.r1.repro._judge import deps, world  # noqa: F401
from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import write
from tests.integration._write_fixtures import OTHER, item, write_req


async def test_f13_hidden_item_revision_is_not_found(connect, world, deps):
    async with await connect() as conn:
        # dev_a (personal) writes a private item in OTHER; dev_b (work) also holds write on OTHER.
        r = await write(conn, world.ctx_a,
                        write_req(OTHER, [item("secret", "a's private", device_scope=f"device:{world.dev_a}")]), deps=deps)
        await conn.commit()
        lid, head = r.versions[0].logical_id, r.versions[0].version_id
        bad = head + 1000
        try:
            await write(conn, world.ctx_b,
                        write_req(OTHER, [item("x", "y", logical_id=lid, expected_version_id=bad)]), deps=deps)
            code, details = "OK", {}
        except ToolError as e:
            await conn.rollback()
            code, details = e.code, e.details
        print("probe:", code, details)
        assert code == "E_NOT_FOUND", f"hidden item probe -> {code} {details} (head oracle)"
