"""F03: a sub-interval correction that restates a link supersedes the whole link; outside [cf,ct) the edge vanishes."""
from docs.bakeoff.r1.repro._judge import D0, DAY, deps, world  # noqa: F401
from hlmemo.core.write_service import write
from tests.integration._write_fixtures import MAIN, item, write_req


async def test_f03_link_survives_outside_correction_interval(connect, world, deps):
    async with await connect() as conn:
        x = await write(conn, world.ctx_a, write_req(MAIN, [item("X", "target")]), deps=deps)
        await conn.commit()
        link = [{"rel": "depends_on", "target": x.versions[0].logical_id}]
        r1 = await write(
            conn, world.ctx_a, write_req(MAIN, [item("Q", "orig", valid_from=D0.isoformat(), links=link)]), deps=deps
        )
        await conn.commit()
        q_lid, v1 = r1.versions[0].logical_id, r1.versions[0].version_id
        await write(
            conn,
            world.ctx_a,
            write_req(MAIN, [item("Q", "corrected", logical_id=q_lid, expected_version_id=v1,
                                  valid_from=(D0 + 5 * DAY).isoformat(), valid_to=(D0 + 10 * DAY).isoformat(),
                                  links=link)]),
            deps=deps,
        )
        await conn.commit()

        async def live(table, col, at):
            cur = await conn.execute(
                f"SELECT count(*) FROM {table} WHERE {col} = %s AND superseded_at = 'infinity'"
                " AND valid_from <= %s AND %s < valid_to",
                (q_lid, at, at),
            )
            return (await cur.fetchone())[0]

        for at in (D0 + DAY, D0 + 11 * DAY):
            assert await live("memory_versions", "logical_id", at) == 1  # survivor segment exists
            n_links = await live("links", "src_logical_id", at)
            assert n_links == 1, f"valid_at={at:%F}: survivor version has {n_links} live depends_on edges (was 1 before)"
