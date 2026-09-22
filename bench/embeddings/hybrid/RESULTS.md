# Hybrid G3 / G4 with google/gemini-embedding-2 (@1536, task prefixes)

Date: 2026-09-22/23. Machine: Apple M3 Pro (same as HARDWARE.md), Postgres 17 + pgvector 0.8.6 (docker), exact scan.
Scripts: `load_vectors.py` (loads the vectors), `run_hybrid.py` (runs G3 and G4). Raw numbers are in `results.json`.

## Setup
- **DB:** `hlm_retr_g2` = `CREATE DATABASE ... TEMPLATE hlm_retr`. `hlm_retr` was only read, over READ ONLY sessions, and still has 11,574 e5 rows.
- **Vectors:** in the copy, all e5 rows were **deleted**. It then got the 9,642 cached fx-main chunk vectors from `bench/embeddings/cache/google__gemini-embedding-2_at_1536_prefix.npy`, stored as `model='google/gemini-embedding-2'`, `model_revision='d1536-prefix-v1'`, preproc 1, dims 1536, L2-normalised. Each row's chunk_id and text were checked against the copy.
  - fx-other has no vectors in the copy. `vector_candidates` filters by `model/revision/preproc` and by the project/scope `hit_where`, so fx-main queries only see the new vectors.
- **Read path:** production `read_service.query` is used unchanged. The script swaps only these seams, inside its own process:
  - `ReadDeps._embedder` becomes an OpenRouter query embedder. It sends `task: search result | query: <q>` with `dimensions=1536` and L2-normalises the result.
  - `read_service.MODEL_ID/MODEL_REVISION` point at the new rows.
  - In the `concurrent` variant only, `read_queries.vector_candidates` is wrapped. The embedding starts as an asyncio task before `query()` and is awaited just before the vector SQL, so it overlaps the term split and the lexical and trigram SQL. This is **measured, not estimated**.
- **Gate settings:** identical to `test_g3_recall.py` / `test_g4_latency.py`, using the same reader context and maps imported from `_read_fixtures.py`.
  - G3: budget 8000, Recall@5 on deduped hits.
  - G4: 100×3 queries with shuffle seed 20260922, 3 callers each on its own connection, 20 warm-ups, budget 2000.
- **Check:** the same harness re-ran the e5 hybrid on `hlm_retr` and got exactly 0.930.

## G3 hybrid Recall@5
| slice | e5 hybrid | gemini-2 hybrid | (gemini-2 vector-only, earlier bench) |
|---|---|---|---|
| overall (100) | **0.930** | **0.920** | 0.920 |
| TR (34) | 0.971 | 0.912 | 0.912 |
| DE (33) | 0.909 | 0.939 | 0.970 |
| EN (33) | 0.909 | 0.909 | 0.879 |
| identifier (25) | 1.000 | 1.000 | 1.000 |
| non-identifier (75) | 0.907 | 0.893 | 0.893 |

**Changed outcomes (gold rank)**

| query | language | e5 hybrid | gemini-2 hybrid |
|---|---|---|---|
| q020 | TR | 3 | 6 |
| q055 | TR | 4 | not returned |
| q026 | DE | 3 | not returned |
| q095 | EN | 4 | 6 |
| q065 | DE | 13 | 3 |
| q066 | EN | 18 | 5 |
| q094 | DE | 7 | 3 |

- gemini-2 lost 4 queries (q020, q055, q026, q095) and won 3 (q065, q066, q094).
- Both models miss q022, q025, q045 and q064.

## G4 (300 queries, 3 concurrent callers), end-to-end ms
| variant | p50 | p95 | p99 | query-embed API p50 / p95 / p99 |
|---|---|---|---|---|
| e5 baseline (re-run now; HARDWARE.md run: 182 / 263 / 452) | 199 | 322 | 515 | local ONNX |
| gemini-2 sequential (async embed, then SQL) | 560 | 736 | 928 | 348 / 450 / 596 |
| gemini-2 concurrent (embed ∥ lexical+trigram, measured) | 488 | 552 | 660 | 392 / 447 / 583 |
| gemini-2 warm query-embedding cache (2nd pass, 100% hits) | 259 | 350 | 541 | 0 |
| gemini-2 literal sync drop-in (blocking HTTP in `embed_query`) | 803 | 1122 | 1235 | 206 / 275 / 354 |

- **SQL and packing time:** in the sequential variant, `query()` minus the embedding took p50 220 / p95 311 ms.
- **Warm cache vs e5:** the warm-cache variant is slower than e5 by about 60 ms at p50. That is the cost of scanning 1536-d vectors instead of 384-d.
- **Sync drop-in:** it blocks the event loop, so the 3 callers are serialised. Its API time is lower only because the calls never overlap.

## Spend
$0.0061 for 1,060 query embeddings: 100 for G3, and 960 for G4 including warm-ups. The cap was $1.50. The corpus was not re-embedded because the cache was used.

## Interpretation
- **G3 does not clear 0.930.** The gemini-2 hybrid scores 0.920, 1 query below e5 hybrid.
  - It still passes the formal G3 gate (≥ 0.90, identifier 1.000).
  - With gemini-2, the lexical and trigram legs add nothing over vector-only (0.920 both). With e5, hybrid adds 13 points (0.80 → 0.93).
  - TR drops (0.971 → 0.912) while DE rises. Better vectors do not help once RRF fuses them with the lexical legs, so fusion weights or k would need re-tuning to benefit.
- **G4 fails with the live API.** p95 is 736 ms sequential and 552 ms concurrent, both over the 500 ms limit.
  - The query-embedding round-trip (p50 about 350 ms, p95 about 450 ms from here, via OpenRouter) is on its own almost the whole budget.
  - Only the warm-cache path passes (p95 350 ms). That covers repeated queries only, not new ones.
- **The literal drop-in is unusable.** A sync HTTP embedder in `embed_query` gives p95 1.1 s. A remote embedder therefore needs an async embed seam in `read_service`, and the concurrent orchestration is still not enough to clear 500 ms.
