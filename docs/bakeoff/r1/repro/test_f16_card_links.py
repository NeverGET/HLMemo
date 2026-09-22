"""F16 (a): a card revision never supersedes the previous card version's derived_from links -> stale forever.
F16 (b): card derived_from links accumulate per close; memory.raw lists them unpaginated -> E_BUDGET_TOO_SMALL grows."""
import uuid

from docs.bakeoff.r1.repro._judge import deps, rdeps, world  # noqa: F401
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import raw
from hlmemo.core.write_service import call_the_day, write
from hlmemo.db import read_queries as rq
from tests.integration._write_fixtures import MAIN, item, write_req


def close(**kw):
    return {"project": MAIN, "request_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
            "client": "pytest/0", "notes": "n", **kw}


async def _card_head(conn, lid):
    cur = await conn.execute("SELECT max(version_id) FROM memory_versions WHERE logical_id=%s"
                             " AND superseded_at='infinity'", (lid,))
    return (await cur.fetchone())[0]


async def test_f16a_new_card_not_stale_after_source_dropped(connect, world, deps):
    async with await connect() as conn:
        x = await write(conn, world.ctx_a, write_req(MAIN, [item("X", "source")]), deps=deps)
        await conn.commit()
        xl, xv = x.versions[0].logical_id, x.versions[0].version_id
        await call_the_day(conn, world.ctx_a, close(card_update={"body": "card v1"},
                                                    expected_versions=[{"logical_id": xl, "version_id": xv}]), deps=deps)
        await conn.commit()
        await write(conn, world.ctx_a, write_req(MAIN, [item("X", "source v2", logical_id=xl, expected_version_id=xv)]),
                    deps=deps)
        await conn.commit()
        head = await _card_head(conn, world.main_card_lid)
        await call_the_day(conn, world.ctx_a, close(card_update={"body": "card v2 (no X)", "expected_version_id": head},
                                                    expected_versions=[]), deps=deps)
        await conn.commit()
        now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]
        srcs = await rq.pinned_sources(conn, world.main_card_lid, world.main_id,
                                       list(world.ctx_a.scope_values()), now, now)
        print("pinned sources of current card:", srcs)
        stale = [vid for vid, s in srcs if s]
        assert xv not in [vid for vid, _ in srcs], f"card v2 declared no X, but is stale via v{xv}: {srcs}"
        assert not stale


async def test_f16b_raw_card_budget_grows_with_closes(connect, world, deps, rdeps):
    async with await connect() as conn:
        head = None
        for i in range(30):
            cu = {"body": f"card {i}"}
            if head is not None:
                cu["expected_version_id"] = head
            await call_the_day(conn, world.ctx_a, close(card_update=cu), deps=deps)
            await conn.commit()
            head = await _card_head(conn, world.main_card_lid)
        cur = await conn.execute("SELECT count(*), count(*) FILTER (WHERE superseded_at='infinity')"
                                 " FROM links WHERE src_logical_id=%s", (world.main_card_lid,))
        print("card links total/current:", await cur.fetchone())
        try:
            out = await raw(conn, world.ctx_a, {"project": MAIN, "version_id": head, "token_budget": 2000}, deps=rdeps)
            print("raw links:", len(out["links"]), "used", out["budget"])
        except ToolError as e:
            raise AssertionError(f"raw(card head, 2000) after 30 closes -> {e.code} {e.details}") from e
