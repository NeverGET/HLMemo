"""G4 — warm query latency (PHASE0-SPEC §7, VALIDATION-GATES G4).

300 ``memory.query`` calls (the 100 fixture queries × 3) issued by 3 concurrent callers, each on
its own connection, against the loaded G3 world (~11.6k chunks, exact vector scan). The measured
time is the whole service call: term split, query embedding (ONNX, in-process), the three
candidate lists, fusion, card, packing. Gate: p95 ≤ 500 ms. Writes ``HARDWARE.md`` at the repo
root with the machine, Postgres/pgvector versions and the measured percentiles.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version
from pathlib import Path

import pytest

from hlmemo.core.read_service import query
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    RetrWorld,
    _clean_tables,
    embedder,
    load_queries,
    read_deps,
    retr_world,
    world,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
HARDWARE_MD = ROOT / "HARDWARE.md"
CALLERS = 3
N_QUERIES = 300
WARMUP = 20
BUDGET = 2000
P95_LIMIT_MS = 500.0


def _sysctl(key: str) -> str | None:
    try:
        return (
            subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _hardware() -> dict[str, str]:
    info: dict[str, str] = {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
    }
    if sys.platform == "darwin":
        info["chip"] = _sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown"
        info["cores"] = _sysctl("hw.ncpu") or "?"
        perf, eff = _sysctl("hw.perflevel0.physicalcpu"), _sysctl("hw.perflevel1.physicalcpu")
        if perf and eff:
            info["cores"] += f" ({perf} performance + {eff} efficiency)"
        mem = _sysctl("hw.memsize")
        info["ram_gb"] = f"{int(mem) / 2**30:.0f}" if mem else "?"
        info["macos"] = platform.mac_ver()[0]
    else:
        chip = platform.processor() or "unknown"
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    chip = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        info["chip"] = chip
        import os

        info["cores"] = str(os.cpu_count() or "?")
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal"):
                    info["ram_gb"] = f"{int(line.split()[1]) / 2**20:.0f}"
                    break
        except OSError:
            info["ram_gb"] = "?"
    return info


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return float("nan")
    k = (len(sorted_ms) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_ms) - 1)
    return sorted_ms[lo] + (sorted_ms[hi] - sorted_ms[lo]) * (k - lo)


async def test_warm_p95_le_500ms_3_callers(retr_world: RetrWorld, connect, read_deps, db_dsn: str) -> None:  # noqa: ANN001
    queries = [q["query"] for q in load_queries()]
    rng = random.Random(20260922)
    plan = [q for _ in range(N_QUERIES // len(queries)) for q in queries]
    rng.shuffle(plan)
    assert len(plan) == N_QUERIES
    ctx = retr_world.ctx_reader

    async def run_one(conn, text: str) -> float:  # noqa: ANN001
        t0 = time.perf_counter()
        res = await query(conn, ctx, {"project": MAIN, "query": text, "token_budget": BUDGET}, deps=read_deps)
        dt = time.perf_counter() - t0
        assert res["budget"]["used"] <= BUDGET
        return dt * 1000.0

    # warm-up: model, connection, page cache
    async with await connect() as conn:
        for text in plan[:WARMUP]:
            await run_one(conn, text)

    todo: asyncio.Queue[str | None] = asyncio.Queue()
    for text in plan:
        todo.put_nowait(text)
    for _ in range(CALLERS):
        todo.put_nowait(None)
    latencies: list[float] = []

    async def caller() -> None:
        async with await connect() as conn:
            while True:
                text = await todo.get()
                if text is None:
                    return
                latencies.append(await run_one(conn, text))

    wall0 = time.perf_counter()
    await asyncio.gather(*(caller() for _ in range(CALLERS)))
    wall = time.perf_counter() - wall0

    assert len(latencies) == N_QUERIES
    s = sorted(latencies)
    p50, p95, p99 = _percentile(s, 0.50), _percentile(s, 0.95), _percentile(s, 0.99)
    mean = statistics.fmean(s)

    async with await connect() as conn:
        cur = await conn.execute("SHOW server_version")
        pg_version = (await cur.fetchone())[0]
        cur = await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        pgvector = (await cur.fetchone())[0]
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM embeddings),"
            " (SELECT count(*) FROM memory_versions)"
        )
        n_chunks, n_emb, n_versions = await cur.fetchone()

    hw = _hardware()
    hw_line = f"{hw['chip']}, {hw['cores']} cores, {hw['ram_gb']} GB RAM, {hw['os']}"
    print(
        f"\n[G4] {N_QUERIES} queries / {CALLERS} callers: p50={p50:.1f} ms p95={p95:.1f} ms p99={p99:.1f} ms"
        f" mean={mean:.1f} ms max={s[-1]:.1f} ms wall={wall:.1f}s ({N_QUERIES / wall:.1f} q/s) on {hw_line}"
    )
    HARDWARE_MD.write_text(
        "\n".join(
            [
                "# HARDWARE.md — G4 latency gate record",
                "",
                "Written by `tests/integration/test_g4_latency.py::test_warm_p95_le_500ms_3_callers`"
                " (PHASE0-SPEC §7, VALIDATION-GATES G4). Regenerated on every run.",
                "",
                f"- Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
                f"- Machine: {hw_line}",
                f"- Python: {hw['python']}; onnxruntime {pkg_version('onnxruntime')};"
                f" psycopg {pkg_version('psycopg')}",
                f"- Postgres: {pg_version}; pgvector {pgvector}"
                " (docker compose `db`, exact `<=>` scan, no HNSW)",
                f"- Database: `{db_dsn.rsplit('/', 1)[-1]}` — {n_versions} versions, {n_chunks} chunks,"
                f" {n_emb} embeddings (G3 fixture, `fx-main` + `fx-other`)",
                "",
                "## Workload",
                "",
                f"- {N_QUERIES} `memory.query` calls = the 100 G3 fixture queries × 3, shuffled"
                f" (seed 20260922), `token_budget={BUDGET}`, project `fx-main`, reader device"
                " (read grant on `fx-main` only)",
                f"- {CALLERS} concurrent asyncio callers, one connection each, service-level (no HTTP);"
                f" {WARMUP} warm-up queries before measuring",
                "- Each measurement covers the whole call: term split, query embedding (ONNX CPU,"
                " in-process), lexical (GIN tsvector), trigram (GIN pg_trgm), exact vector scan,"
                " RRF fusion, dedupe, card slot, budget packing (o200k_base meter)",
                "",
                "## Result",
                "",
                "| metric | ms |",
                "|---|---|",
                f"| p50 | {p50:.1f} |",
                f"| p95 | {p95:.1f} |",
                f"| p99 | {p99:.1f} |",
                f"| mean | {mean:.1f} |",
                f"| max | {s[-1]:.1f} |",
                "",
                f"Wall time {wall:.1f} s ({N_QUERIES / wall:.1f} queries/s aggregate)."
                f" Gate: p95 ≤ {P95_LIMIT_MS:.0f} ms → **{'PASS' if p95 <= P95_LIMIT_MS else 'FAIL'}**.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert p95 <= P95_LIMIT_MS, f"warm p95 {p95:.1f} ms > {P95_LIMIT_MS} ms (p50 {p50:.1f} ms)"
