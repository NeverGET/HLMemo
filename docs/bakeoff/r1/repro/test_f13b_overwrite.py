"""F13 (second half): with the disclosed head, dev_b supersedes dev_a's device-private item."""
from docs.bakeoff.r1.repro._judge import deps, world  # noqa: F401
from hlmemo.core.errors import ToolError
from hlmemo.core.write_service import write
from tests.integration._write_fixtures import OTHER, item, write_req


async def test_f13_hidden_item_cannot_be_superseded(connect, world, deps):
    async with await connect() as conn:
        r = await write(conn, world.ctx_a,
                        write_req(OTHER, [item("secret", "a's private", device_scope=f"device:{world.dev_a}")]), deps=deps)
        await conn.commit()
        lid, head = r.versions[0].logical_id, r.versions[0].version_id
        try:
            res = await write(conn, world.ctx_b,
                              write_req(OTHER, [item("x", "b overwrote", logical_id=lid, expected_version_id=head)]), deps=deps)
            await conn.commit()
        except ToolError as e:
            assert e.code == "E_NOT_FOUND"
            return
        cur = await conn.execute("SELECT version_id, device_scope, body, superseded_at='infinity' FROM memory_versions"
                                 " WHERE logical_id=%s ORDER BY version_id", (lid,))
        rows = await cur.fetchall()
        raise AssertionError(f"dev_b superseded dev_a's private item: {rows}")
