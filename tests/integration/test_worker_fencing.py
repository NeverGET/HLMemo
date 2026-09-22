"""D-027 C6: a stale worker must not persist even copied predecessor vectors."""

from __future__ import annotations

import pytest

from hlmemo.core import MODEL_ID, MODEL_REVISION
from hlmemo.core.write_service import default_deps, write
from hlmemo.worker.main import DrainStats, lease_jobs, process_jobs
from tests.integration._write_fixtures import MAIN, item, seed_world, write_req

pytestmark = pytest.mark.integration


async def test_stale_lease_rolls_back_copied_vectors_then_new_owner_completes(connect):
    deps = default_deps()
    async with await connect() as conn:
        world = await seed_world(conn)
        valid_from = "2026-09-01T00:00:00Z"
        first = await write(
            conn,
            world.ctx_a,
            write_req(MAIN, [item("fact", "same text", valid_from=valid_from)]),
            deps=deps,
        )
        await conn.commit()
        original = first.versions[0]
        await conn.execute(
            "INSERT INTO embeddings (chunk_id, model, model_revision, preproc_version, dims, vec)"
            " SELECT chunk_id, %s, %s, 1, 384, %s::vector FROM chunks WHERE version_id = %s",
            (MODEL_ID, MODEL_REVISION, "[" + ",".join(["1"] * 384) + "]", original.version_id),
        )
        await conn.execute("UPDATE jobs SET status = 'done', done_at = now()")
        await conn.commit()
        revised = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "fact",
                        "same text",
                        valid_from=valid_from,
                        logical_id=original.logical_id,
                        expected_version_id=original.version_id,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        old_jobs = await lease_jobs(conn, 16)
        assert len(old_jobs) == 1
        await conn.execute(
            "UPDATE jobs SET lease_until = now() - interval '1 second' WHERE status = 'running'"
        )
        await conn.commit()
        new_jobs = await lease_jobs(conn, 16)
        assert len(new_jobs) == 1 and new_jobs[0].lease_token != old_jobs[0].lease_token

        class CopyOnly:
            def embed_passages(self, passages):
                raise AssertionError("identical predecessor text should be copied")

        stale_stats = DrainStats()
        await process_jobs(conn, CopyOnly(), old_jobs, stale_stats)
        cur = await conn.execute(
            "SELECT count(*) FROM embeddings e JOIN chunks c USING (chunk_id) WHERE c.version_id = %s",
            (revised.versions[0].version_id,),
        )
        assert (await cur.fetchone())[0] == 0
        assert stale_stats.jobs_done == stale_stats.chunks_copied == 0
        await conn.commit()
        winning_stats = DrainStats()
        await process_jobs(conn, CopyOnly(), new_jobs, winning_stats)
        assert winning_stats.jobs_done == winning_stats.chunks_copied == 1
        cur = await conn.execute("SELECT status FROM jobs WHERE job_id = %s", (new_jobs[0].job_id,))
        assert (await cur.fetchone())[0] == "done"
