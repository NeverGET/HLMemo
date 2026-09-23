"""Bound resident chunk texts and padded native inference, independent of job size."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from hlmemo import config
from hlmemo.core import MODEL_ID, MODEL_REVISION, memory_profile
from hlmemo.core.embedder import Embedder
from hlmemo.worker import main as worker


@pytest.fixture(autouse=True)
def _clean_tables():
    yield


@pytest.mark.parametrize(
    ("lengths", "limit", "sizes"),
    [
        ([512] * 7, 1024, [2, 2, 2, 1]),
        ([10, 10, 512, 10, 10], 1024, [2, 2, 1]),
        ([20] * 7, 1024, [3, 3, 1]),
        ([512, 512], 512, [1, 1]),
    ],
)
def test_batches_bound_padded_tokens_and_keep_order(lengths, limit, sizes):
    embedder = object.__new__(Embedder)
    embedder.batch_size = 3
    embedder.max_batch_tokens = limit
    embedder._tok = SimpleNamespace(
        encode=lambda text, **kw: SimpleNamespace(text=text, ids=[0] * lengths[int(text)])
    )
    batches = []

    def infer(encs):
        # Each text is tokenized once; the inference batch reuses those encodings.
        batches.append(len(encs))
        assert len(encs) * max(len(e.ids) for e in encs) <= limit
        return np.array([[int(e.text)] * embedder.dims for e in encs], dtype=np.float32)

    embedder._feeds = lambda encs: list(encs)
    embedder._infer = infer
    result = embedder.embed_texts([str(i) for i in range(len(lengths))])
    assert batches == sizes
    assert result[:, 0].tolist() == list(range(len(lengths)))


class Connection:
    commit = AsyncMock()
    rollback = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


def job():
    return worker.Job(1, "embed", {"model": MODEL_ID, "model_revision": MODEL_REVISION}, 1, "lease")


@pytest.mark.parametrize("fail_first_job", [False, True])
async def test_large_job_never_materialises_more_than_one_page(monkeypatch, fail_first_job):
    alive = peak = inserted = 0
    page_size = 3
    total = 100

    class Text(str):
        def __new__(cls, value):
            nonlocal alive, peak
            result = super().__new__(cls, value)
            alive += 1
            peak = max(peak, alive)
            assert alive <= page_size, "previous page still retains chunk text"
            return result

        def __del__(self):
            nonlocal alive
            alive -= 1

    async def pending(conn, current_job, *, after_id, limit):
        assert limit == page_size
        return [(i, Text(str(i))) for i in range(after_id + 1, min(after_id + limit, total) + 1)]

    async def insert(conn, current_job, rows):
        nonlocal inserted
        assert len(rows) <= page_size
        inserted += len(rows)

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(worker_batch_chunks=page_size))
    monkeypatch.setattr(worker, "_pending_chunks", pending)
    monkeypatch.setattr(worker, "_copyable_chunks", AsyncMock(return_value=set()))
    monkeypatch.setattr(worker, "_copy_from_predecessor", AsyncMock(return_value=0))
    monkeypatch.setattr(worker, "_insert_embeddings", insert)
    monkeypatch.setattr(worker, "_mark_done", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "_test_barrier", AsyncMock())
    failed = False

    def embed(texts):
        nonlocal failed
        if fail_first_job and not failed:
            failed = True
            raise RuntimeError("failed page must be released before next job")
        return [[0.0] * 384 for _ in texts]

    async def failed_job(*args):
        pass

    monkeypatch.setattr(worker, "_handle_failure", failed_job)
    embedder = SimpleNamespace(embed_passages=embed)
    stats = worker.DrainStats()
    jobs = [job(), job()] if fail_first_job else [job()]
    await worker.process_jobs(Connection(), embedder, jobs, stats)
    assert (peak, alive, inserted) == (page_size, 0, total)
    assert stats.jobs_done == 1 and stats.chunks_embedded == total


async def test_cancelled_worker_awaits_native_inference(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def embed(texts):
        started.set()
        assert release.wait(5)
        finished.set()
        return [[0.0] * 384]

    monkeypatch.setattr(worker, "_pending_chunks", AsyncMock(return_value=[(1, "text")]))
    monkeypatch.setattr(worker, "_copyable_chunks", AsyncMock(return_value=set()))
    monkeypatch.setattr(worker, "_copy_from_predecessor", AsyncMock(return_value=0))
    task = asyncio.create_task(
        worker.process_jobs(Connection(), SimpleNamespace(embed_passages=embed), [job()], worker.DrainStats())
    )
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_pending_sql_keyset_has_server_side_limit():
    execute = AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[])))
    current_job = job()
    current_job.payload.update(version_id=4, preproc_version=1)
    await worker._pending_chunks(SimpleNamespace(execute=execute), current_job, after_id=17, limit=3)
    sql, params = execute.call_args.args
    assert "c.chunk_id > %(after_id)s" in sql
    assert "LIMIT %(limit)s" in sql
    assert params["after_id"] == 17 and params["limit"] == 3


def test_memory_profile_uses_resolved_configuration(monkeypatch):
    monkeypatch.delenv("HLM_WORKER_MEMORY_PROFILE", raising=False)
    monkeypatch.setattr(memory_profile, "_configured", None)
    logged = []
    monkeypatch.setattr(memory_profile.log, "info", lambda *args: logged.append(args))
    monkeypatch.setattr(memory_profile.tracemalloc, "is_tracing", lambda: True)
    monkeypatch.setattr(memory_profile.tracemalloc, "get_traced_memory", lambda: (0, 0))
    memory_profile.configure_memory_profile(True)
    memory_profile.memory_sample("configured")
    assert len(logged) == 1
    memory_profile.configure_memory_profile(False)
    memory_profile.memory_sample("disabled")
    assert len(logged) == 1


async def test_worker_cancellation_closes_connection_despite_repeated_cancel(monkeypatch):
    running = asyncio.Event()
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()

    async def run_once(*args):
        running.set()
        await asyncio.Event().wait()

    async def close():
        closing.set()
        await release_close.wait()
        closed.set()

    connection = SimpleNamespace(closed=False, close=close)
    monkeypatch.setattr(worker, "run_once", run_once)
    task = asyncio.create_task(worker.run_forever(AsyncMock(return_value=connection), object()))
    await running.wait()
    task.cancel()
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
