"""Opt-in allocation evidence without logging memory contents."""

from __future__ import annotations

import logging
import os
import resource
import sys
import tracemalloc
from pathlib import Path

log = logging.getLogger("hlmemo.memory")
_configured: bool | None = None


def configure_memory_profile(enabled: bool) -> None:
    """Apply the resolved worker setting once; standalone probes retain their env fallback."""
    global _configured
    _configured = enabled


def memory_sample(stage: str, **counts: int) -> None:
    enabled = _configured
    if enabled is None:
        enabled = os.environ.get("HLM_WORKER_MEMORY_PROFILE", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    current, peak = tracemalloc.get_traced_memory()
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        rss_peak *= 1024
    rss = rss_peak
    if sys.platform == "linux":
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    log.info(
        "memory stage=%s rss_mib=%.2f rss_peak_mib=%.2f python_mib=%.2f python_peak_mib=%.2f %s",
        stage,
        rss / 2**20,
        rss_peak / 2**20,
        current / 2**20,
        peak / 2**20,
        " ".join(f"{key}={value}" for key, value in counts.items()),
    )
