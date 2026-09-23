"""Opt-in, database-free measurement of the contract-max write's preprocessing.

Run with the same HLM_MODELS_DIR as the API. Worker stages are measured separately
by HLM_WORKER_MEMORY_PROFILE in worker_oom_smoke.py. RSS includes native allocations;
tracemalloc reports Python allocations only. This is not an end-to-end API peak.
"""

from __future__ import annotations

import gc
import json
import os
import subprocess
import tracemalloc
from pathlib import Path

from hlmemo.core.budget import Meter
from hlmemo.core.chunker import Chunker
from hlmemo.core.embedder import default_model_dir


def snapshot(stage: str, **counts: int) -> None:
    status = Path("/proc/self/status")
    if status.exists():
        rss = next(
            int(line.split()[1]) for line in status.read_text().splitlines() if line.startswith("VmRSS:")
        )
    else:
        rss = int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True))
    current, peak = tracemalloc.get_traced_memory()
    print(
        json.dumps(
            {
                "stage": stage,
                "rss_MiB": round(rss / 1024, 2),
                "python_MiB": round(current / 2**20, 2),
                "python_peak_MiB": round(peak / 2**20, 2),
                **counts,
            }
        ),
        flush=True,
    )


def main() -> None:
    tracemalloc.start()
    snapshot("start")
    wire = json.dumps({"items": [{"body": "😀" * 64000} for _ in range(50)]}).encode()
    items = json.loads(wire)["items"]
    snapshot("job_input", wire_bytes=len(wire), items=len(items))
    del wire
    meter = Meter()
    chunker = Chunker(default_model_dir())
    snapshot("tokenizers_loaded")
    tokens = [meter.count_text(item["body"]) for item in items]
    snapshot("tokenization", tokens=sum(tokens))
    chunks = [chunker.chunk(item["body"]) for item in items]
    snapshot("chunking", chunks=sum(map(len, chunks)))
    del items, chunks, meter, chunker, tokens
    gc.collect()
    snapshot("released")


if __name__ == "__main__":
    main()
