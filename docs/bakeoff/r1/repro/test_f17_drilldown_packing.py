"""F17: drilldown packing stops at the first non-fitting prefix; a complete page (no cursor) that fits is never tried."""
from docs.bakeoff.r1.repro._judge import deps, rdeps, world  # noqa: F401
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import drilldown
from hlmemo.core.write_service import write
from tests.integration._write_fixtures import MAIN, item, write_req


async def test_f17_complete_page_that_fits_is_not_rejected(connect, world, deps, rdeps):
    async with await connect() as conn:
        r = await write(conn, world.ctx_a, write_req(MAIN, [item("t", "a " * 410)]), deps=deps)
        await conn.commit()
        vid = r.versions[0].version_id
        print("chunks:", r.versions[0].chunk_count)

        async def dd(b):
            try:
                out = await drilldown(conn, world.ctx_a, {"project": MAIN, "clue_ids": [f"v{vid}"], "token_budget": b},
                                      deps=rdeps)
                await conn.commit()
                return out
            except ToolError as e:
                await conn.rollback()
                return e

        full = await dd(32000)
        assert full["next_cursor"] is None
        full_used = full["budget"]["used"]
        print("complete page used:", full_used)
        fails = []
        for b in range(max(256, full_used), full_used + 400):
            res = await dd(b)
            if isinstance(res, ToolError):
                fails.append((b, res.code, res.details))
        print("budgets >= complete-page size that still fail:", fails[:3], "...", len(fails))
        assert not fails, f"complete page needs {full_used} tokens, yet budget {fails[0][0]} -> {fails[0][1]} {fails[0][2]}"
