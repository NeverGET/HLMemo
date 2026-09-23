"""Opt-in native batch experiment; run in an isolated memory-limited container.

The direct _run deliberately bypasses batching policy to compare the previous
32-text allocation with a bounded batch of two, using identical 400-token chunks.
"""

from __future__ import annotations

import argparse
import tracemalloc

from tests.deploy.profile_write_memory import snapshot

from hlmemo.core.embedder import PASSAGE_PREFIX, Embedder, default_model_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, required=True)
    args = parser.parse_args()
    tracemalloc.start()
    snapshot("start")
    model = Embedder(default_model_dir())
    snapshot("model_loaded")
    texts = [PASSAGE_PREFIX + "😀" * 400 for _ in range(args.batch)]
    feeds = model._encode_batch(texts)
    snapshot("tokenization", texts=len(texts), padded_tokens=int(feeds["input_ids"].size))
    del feeds
    vectors = model._run(texts)
    snapshot("embedding", vectors=len(vectors))
    del vectors, texts, model
    snapshot("released")


if __name__ == "__main__":
    main()
