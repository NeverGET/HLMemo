"""Paged inserts must remain invisible and roll back together if another worker takes the lease."""

from __future__ import annotations

import uuid

import pytest

from hlmemo.core.write_service import default_deps, write
from hlmemo.worker import main as worker
from tests.integration._write_fixtures import MAIN, item, seed_world, write_req

pytestmark = pytest.mark.integration


async def test_takeover_after_first_page_rolls_back_all_pages(connect, monkeypatch):
    monkeypatch.setenv("HLM_WORKER_BATCH_CHUNKS", "2")
    deps = default_deps()
    async with await connect() as conn:
        world = await seed_world(conn)
        result = await write(
            conn, world.ctx_a, write_req(MAIN, [item("fact", "memory bounded worker " * 1500)]), deps=deps
        )
        await conn.commit()
        version_id = result.versions[0].version_id
        jobs = await worker.lease_jobs(conn, 1)
        insert = worker._insert_embeddings
        pages = 0

        async def takeover_after_insert(connection, job, rows):
            nonlocal pages
            assert len(rows) <= 2
            await insert(connection, job, rows)
            pages += 1
            if pages == 1:
                async with await connect() as observer:
                    cur = await observer.execute(
                        "SELECT count(*) FROM embeddings e JOIN chunks c USING (chunk_id)"
                        " WHERE c.version_id = %s",
                        (version_id,),
                    )
                    assert (await cur.fetchone())[0] == 0
                    await observer.execute(
                        "UPDATE jobs SET lease_token = %s WHERE job_id = %s", (str(uuid.uuid4()), job.job_id)
                    )
                    await observer.commit()

        monkeypatch.setattr(worker, "_insert_embeddings", takeover_after_insert)

        class Stub:
            def embed_passages(self, texts):
                return [[1.0] * 384 for _ in texts]

        stats = worker.DrainStats()
        await worker.process_jobs(conn, Stub(), jobs, stats)
        assert pages > 1
        assert stats.jobs_done == stats.chunks_embedded == 0
        cur = await conn.execute(
            "SELECT count(*) FROM embeddings e JOIN chunks c USING (chunk_id) WHERE c.version_id = %s",
            (version_id,),
        )
        assert (await cur.fetchone())[0] == 0
        await conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")
        await conn.commit()
        monkeypatch.setattr(worker, "_insert_embeddings", insert)
        winning = await worker.lease_jobs(conn, 1)
        await worker.process_jobs(conn, Stub(), winning, stats)
        assert stats.jobs_done == 1 and stats.chunks_embedded > 2
        cur = await conn.execute(
            "SELECT count(*) FROM chunks c WHERE c.version_id = %s AND NOT EXISTS"
            " (SELECT 1 FROM embeddings e WHERE e.chunk_id = c.chunk_id)",
            (version_id,),
        )
        assert (await cur.fetchone())[0] == 0
