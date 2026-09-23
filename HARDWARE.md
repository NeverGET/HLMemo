# HARDWARE.md — G4 latency gate record

Written by `tests/integration/test_g4_latency.py::test_warm_p95_le_500ms_3_callers` (PHASE0-SPEC §7, VALIDATION-GATES G4). Regenerated on every run.

- Date: 2026-09-23 23:17 UTC
- Machine: Apple M3 Pro, 12 (6 performance + 6 efficiency) cores, 36 GB RAM, Darwin 25.6.0 (arm64)
- Python: 3.12.12; onnxruntime 1.30.0; psycopg 3.3.6
- Postgres: 17.11 (Debian 17.11-1.pgdg12+2); pgvector 0.8.6 (docker compose `db`, exact `<=>` scan, no HNSW)
- Database: `hlm_retr_a` — 2402 versions, 11576 chunks, 11576 embeddings (G3 fixture, `fx-main` + `fx-other`)

## Workload

- 300 `memory.query` calls = the 100 G3 fixture queries × 3, shuffled (seed 20260922), `token_budget=2000`, project `fx-main`, reader device (read grant on `fx-main` only)
- 3 concurrent asyncio callers, one connection each, service-level (no HTTP); 20 warm-up queries before measuring
- Each measurement covers the whole call: term split, query embedding (ONNX CPU, in-process), lexical (GIN tsvector), trigram (GIN pg_trgm), exact vector scan, RRF fusion, dedupe, card slot, budget packing (o200k_base meter)

## Result

| metric | ms |
|---|---|
| p50 | 160.9 |
| p95 | 257.2 |
| p99 | 446.9 |
| mean | 176.9 |
| max | 488.2 |

Wall time 17.8 s (16.9 queries/s aggregate). Gate: p95 ≤ 500 ms → **PASS**.
