"""Opt-in instrumentation for the pre-fix image; never used by production.

Mount tests at /probe/tests and set PYTHONPATH=/probe in a limited container.
Wrap the old implementation in-place to measure its actual queue processing.
"""

from __future__ import annotations

import os
import tracemalloc

from tests.deploy.profile_write_memory import snapshot

from hlmemo.core.embedder import Embedder
from hlmemo.worker import main as worker


def main() -> None:
    if os.environ.get("HLM_DB_DSN") != "postgresql://hlm:hlm@host.docker.internal:5432/hlm_exit":
        raise ValueError("baseline instrumentation requires the isolated hlm_exit database")
    tracemalloc.start()
    pending = worker._pending_chunks
    encode = Embedder._encode_batch
    run = Embedder._run
    insert = worker._insert_embeddings

    async def pending_profile(*args, **kwargs):
        rows = await pending(*args, **kwargs)
        snapshot("job_load", texts=len(rows))
        return rows

    def encode_profile(self, texts):
        feeds = encode(self, texts)
        snapshot("tokenization", texts=len(texts), padded_tokens=int(feeds["input_ids"].size))
        return feeds

    def run_profile(self, texts):
        snapshot("before_embedding", texts=len(texts))
        vectors = run(self, texts)
        snapshot("embedding", vectors=len(vectors))
        return vectors

    async def insert_profile(*args, **kwargs):
        result = await insert(*args, **kwargs)
        snapshot("insert")
        return result

    worker._pending_chunks = pending_profile
    worker._insert_embeddings = insert_profile
    Embedder._encode_batch = encode_profile
    Embedder._run = run_profile
    worker.main()


if __name__ == "__main__":
    main()
