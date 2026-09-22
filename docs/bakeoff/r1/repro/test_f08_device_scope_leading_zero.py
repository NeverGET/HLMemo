"""F08: device_scope 'device:0<id>' passes the pattern + existence check, is stored verbatim, and is invisible to its own device."""
from docs.bakeoff.r1.repro._judge import deps, rdeps, world  # noqa: F401
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import raw
from hlmemo.core.write_service import write
from tests.integration._write_fixtures import MAIN, item, write_req


async def test_f08_noncanonical_device_scope(connect, world, deps, rdeps):
    scope = f"device:0{world.dev_a}"
    async with await connect() as conn:
        try:
            res = await write(conn, world.ctx_a, write_req(MAIN, [item("t", "b", device_scope=scope)]), deps=deps)
        except ToolError as e:
            assert e.code == "E_INVALID_ARG"
            return
        await conn.commit()
        vid = res.versions[0].version_id
        cur = await conn.execute("SELECT device_scope FROM memory_versions WHERE version_id=%s", (vid,))
        print("stored scope:", (await cur.fetchone())[0])
        try:
            out = await raw(conn, world.ctx_a, {"project": MAIN, "version_id": vid, "token_budget": 2000}, deps=rdeps)
        except ToolError as e:
            raise AssertionError(f"write acked {scope!r} but raw by the writing device -> {e.code}") from e
        assert out["version_id"] == vid
