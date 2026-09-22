"""F07 / F02: a correction copies the live (unlocked, possibly stale) last_access_at into survivors and does not
record it; replay applies the concurrent access event (lower event_id) first -> rebuilt projections differ.

The race is scheduled deterministically: a drilldown on another connection commits while the correction sits
between its unlocked current_versions read and its event-id allocation (hook on write_queries.clock_now)."""
from docs.bakeoff.r1.repro._judge import D0, DAY, deps, rdeps, world  # noqa: F401
from hlmemo.core.read_service import drilldown
from hlmemo.core.write_service import write
from hlmemo.db import write_queries as wq
from hlmemo.db.replay import rebuild_projections
from tests.integration._write_fixtures import MAIN, dump_projections, item, write_req


async def test_f07_rebuild_identical_after_concurrent_access(connect, world, deps, rdeps, monkeypatch):
    async with await connect() as conn:
        r = await write(conn, world.ctx_a, write_req(MAIN, [item("V", "base", valid_from=D0.isoformat())]), deps=deps)
        await conn.commit()
        lid, vid = r.versions[0].logical_id, r.versions[0].version_id

        orig = wq.clock_now
        fired = False

        async def hooked(c):
            nonlocal fired
            if not fired:
                fired = True
                async with await connect() as other:  # concurrent drilldown commits its access event now
                    await drilldown(other, world.ctx_a, {"project": MAIN, "clue_ids": [f"v{vid}"], "token_budget": 2000},
                                    deps=rdeps)
                    await other.commit()
            return await orig(c)

        monkeypatch.setattr(wq, "clock_now", hooked)
        await write(conn, world.ctx_a,
                    write_req(MAIN, [item("V", "fix", logical_id=lid, expected_version_id=vid,
                                          valid_from=(D0 + 5 * DAY).isoformat(), valid_to=(D0 + 6 * DAY).isoformat())]),
                    deps=deps)
        await conn.commit()
        monkeypatch.setattr(wq, "clock_now", orig)
        assert fired
        cur = await conn.execute("SELECT event_id, kind FROM events ORDER BY event_id")
        print("events:", await cur.fetchall())
        before = await dump_projections(conn)
        await rebuild_projections(conn)
        await conn.commit()
        after = await dump_projections(conn)
        diff = [(b, a) for b, a in zip(before["memory_versions"], after["memory_versions"]) if a != b]
        for b, a in diff:
            print("live   :", b[-60:]); print("replay :", a[-60:])
        assert after == before
