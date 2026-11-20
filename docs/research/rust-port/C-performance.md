# C. Where HLMemo spends time and resources today

Workstream C of the Rust port assessment (PLAN.md). Written 2026-11-20 on main 4b6345d.

**Labels.** **M** means measured here, on Apple M3 Pro with `.venv/bin/python` 3.12.12, no server and no Docker. The scripts are in the session scratchpad and are described inline. **R** means recorded in the repo, with its source. **E** means estimated, with the reasoning given. Production is the Hostinger KVM 2 (2 vCPU x86_64, D-046), where the api, worker and db each get `cpus: 1.0` (deploy/compose.prod.yaml:63-64, 103-104, 133-134). Its single-core speed relative to the M3 was not measured. I assume it is 1.5-2.5x slower (**E**).

## 1. Verdict per path

| Path | Typical time (source) | Python CPU share | Waiting share (network / DB / LLM) | What Rust could save |
|---|---|---|---|---|
| `memory.query`, prod, seen by the client | p50 233 / p95 335 ms (R, e2e-results.json:966-967) | 10-30 % (E) | WAN ~95-130 ms; SQL is the rest | ~15-30 ms per query (E), about 6-13 % |
| `memory.query`, service only, local | p50 56 / p95 83 ms single caller (R, e2e-results.json:1815-1816); 3 callers p50 164 / p95 256 ms (R, HARDWARE.md:21-22) | ~30-45 % (M+E, §3) | SQL | most of the packing CPU; the ORT and tiktoken time stays |
| `memory.query` identifier tail | 0.8-1.5 s (R, R3-REHEARSAL.md:242) | ~0 | global trigram SQL | nothing; the fix is in the SQL |
| `memory.drilldown` | prod p50 115-126 ms; local p50 11-21 ms (R, e2e-results.json) | small | ~90 % WAN | ~0 |
| `memory.write` | VM write p95 244-305 ms (R, R3-REHEARSAL.md:236-241) | < 3 % (M, §4) | 30+ sequential SQL statements, locks | ~0-5 % |
| Worker embedding | prod 249k tokens in 4.9 min, ≈ 850 tok/s (R, E2E-PROD-REPORT.md:69) | 0.2 % (M) | the rest runs inside ONNX Runtime (C++) | ~0; Rust would use the same ORT |
| Import | 1,754 items in ~10 min, ≈ 342 ms/item (R, BACKLOG.md:254); 363 items in 82 s (R, E2E:69) | ~0 | one sequential `memory.write` per item (importers/runner.py:310-315) | ~0; batching would help, Rust would not |
| `memory.ask` | p95 31 s, $0.020/q (R, STATUS.md:86, D-200) | < 2 % (E) | LLM calls | ~0 |
| Synthesis / judged risk check | LLM path p50 1.9 s (R, e2e-results.json:2044); judged risk p50 1.87 s vs 125 ms unjudged (R, e2e-results.json:2067+) | < 5 % | LLM | ~0 |
| Librarian queue | 3.4-4.9 jobs/min (R, E2E:71); 1.8-5.2 jobs/min and 17.4 calls/job for C4 (R, DECISIONS.md:125); since D-280 also capped by spend | ~0 | LLM and spend cap | 0 |
| Capture (SessionEnd/PreCompact) | detached; LLM p50 137 s, max 300 s (M, capture.log, n=35) | ~0 | LLM | 0, and nobody waits on it |
| SessionStart brief hook | injected p50 3.72 / p90 6.11 / p95 6.55 s (M, brief.log, n=90) | client ~0.45 s (12 %); server-side packing ~0.2-0.5 s (E) | ≥ 7 WAN round trips plus server queueing | ~0.4 s on the client; a server-side brief tool would save more |
| Unmapped hook, `hlm --help` | 20-30 ms; 350-400 ms (M) | ~100 % | none | 20 ms (unnoticeable); ~0.35 s per CLI call |

## 2. Recorded numbers not in the table above
- G4 (local, 3 callers, budget 2000, 11,574 chunks, exact `<=>` scan, no HNSW): p99 439 ms, 16.7 q/s aggregate (HARDWARE.md:21-27); D-044 had p95 264-273 ms.
- Remote embedder trial: `query()` minus embedding p50 220 / p95 311 ms; the OpenRouter round trip alone p95 ~450 ms, so e5 stayed local (bench/embeddings/hybrid/RESULTS.md:47-53, D-042). Local e5 query embedding 4-5 ms (bench/embeddings/RESULTS.md:25).
- Prod under an import: 1,434 queries p50/p95/max 268/405/491 ms incl. ~95 ms WAN (E2E-PROD-REPORT.md:70); 2 vCPU VM G-L3 p95 268/375 ms (E2E:43).
- WAN Mac to prod p50 132 / p95 145 ms (R3-PROD-DEPLOY.md:17); to the rehearsal VM 16 / 45 ms (R3-REHEARSAL.md:204).
- Brief: 2.3-3.2 s at D-252 (DECISIONS.md:2262), budget now FETCH_S 6.5 / WALL_S 8 s (brief/config.py:20-22); brief.log (2026-10-01..11-20) holds 113 mapped runs: 90 injected, 9 empty, 8 errors, 6 timeouts (mean 6.7 s).

## 3. The `memory.query` path, step by step

From read_service.py:226-367 and retrieval.py:456-555. Everything below runs in one uvicorn process (app.py:522, no `workers`) on one event loop.

| # | Step | Cost class | Evidence |
|---|---|---|---|
| 0 | Starlette/MCP framing, auth middleware, device token check | Python CPU plus 1-2 DB round trips | not measured |
| 1 | `resolve_project`, `clock_now`, `term_stats.get` | 2-3 DB round trips | |
| 2 | `split_terms` | Python CPU, < 0.1 ms | M |
| 3 | `embed_query` | ORT C++. 5.1 ms with 2 threads, 8.9 ms with 1 (M). It runs **on the event loop** (read_service.py:261) | M |
| 4 | Lexical, title, trigram (with `set_trigram_threshold`) and exact vector legs | 4-5 sequential DB round trips; SQL CPU. These are the dominant cost and the tail | R |
| 5 | `indexing_pending`, `supersession_among`, `hit_rows`, `superseded_hits`, `pending_block`, `card_version`, `pinned_sources` | 7+ sequential DB round trips | code |
| 6 | `rrf_fuse` plus `dedupe_and_order` over 240 candidates | Python CPU, 0.11 ms | M |
| 7 | `pack_query`: previews, tiktoken meter, `settle` fixed point | **Python CPU: 10.2 ms at budget 2000, 18.3 ms at 3000, 51 ms at 8000** (fixture chunk texts) | M |

A cProfile of step 7 at budget 3000 splits the time like this:

- `query_preview` takes ~80 % of the step. Most of it is `term_matches`, together with `normalize.py:23`, a per-character genexpr (~1M calls over 20 runs), and the Python genexpr inside tiktoken's `decode_with_offsets`.
- The tiktoken `encode` call, which is already Rust, takes ~19 %.

So roughly 25-30 ms of Python CPU per query on the M3 at the usual budgets (steps 0, 3 and 7). That is ~40 % of the 56 ms single-caller local time (**E**). On prod it is perhaps 30-75 ms (**E**, using the speed factor above) out of ~100-140 ms of server time.

Because the process has one event loop and the api has `cpus: 1.0`, this CPU also serialises concurrent callers. That probably accounts for part of the jump from 56 ms with one caller to 164 ms with three (**E**; the db has its own CPU too). Rust would shrink step 7 several times over, but steps 1, 4 and 5 are unchanged.

## 4. Write, worker and import

- **Write path.** write_service.py:782-1292 issues roughly 30+ sequential statements: locks, `allocate_ids`, `insert_version` per version, `insert_link` per link, and the jobs.
- **Write CPU (M, fixture bodies).**

  | Body | chunk + normalize | meter count | secret scan |
  |---|---|---|---|
  | 1.4k chars | 0.31 ms | 0.06 ms | 0.08 ms |
  | 6k chars | 1.4 ms | 0.22 ms | 0.30 ms |
  | 24k chars | 5.1 ms | 0.72 ms | 1.1 ms |

  Bodies of 2,048 characters or more are offloaded to a 2-thread pool (write_service.py:101-119). That CPU is a few percent of a 250-300 ms write.
- **Embedding (M, one ~321-token passage, the repo's `Embedder`).** It takes 85.9 ms: `session.run` 85.7 ms, tokenisation 0.15 ms (HF tokenizers, which is Rust), numpy feeds 0.016 ms. A batch of 32 runs at 87 ms per chunk (about 3,700 tok/s). With 1 thread it is 167 ms per chunk. Loading the model takes 0.4-0.6 s.
- **Prod embedding throughput.** About 850 tok/s (R). The worker runs `threads=2` (config.py:179) under `cpus: 1.0`. The levers here do not depend on the language: an int8 model, matching the thread count to the cgroup, or more CPU.
- **Import.** The importer awaits one write per record (runner.py:310-315), so its throughput is WAN RTT plus write latency.

## 5. Client side (M)

| Process | Wall time |
|---|---|
| `python -c pass` | 10 ms |
| Brief hook, unmapped (real stdin, `-P -m hlmemo.brief.hook`) | 20-30 ms |
| `import hlmemo.brief.hook` | 20 ms (stdlib-light by design, hook.py:52-53) |
| Mapped-path imports (fetch, assemble, cli.credentials, mcp_client) | **310-330 ms** warm, 470 ms first run. `mcp` alone is ~200 ms; `hlmemo.config` with pydantic_settings is ~45 ms |
| o200k vocab load (first `count_tokens`) | 124 ms |
| `hlm --help` | 350-400 ms (`hlmemo.cli.hlm` imports 295 ms, mostly `mcp`) |
| Keychain token read | not measured (I did not touch credentials) |

Of the brief's 3.7 s p50, ~0.45 s is client CPU (imports and vocab). The rest is network and server time:

- The brief opens a session, then sends 3 `memory.query` calls at budget 8000 (fetch.py:49-55, 332). That is ~3 × 51 ms of packing CPU on the M3, more on prod.
- It then sends up to 22 `memory.raw` reads at concurrency 8 and a card-date read.
- That is at least ~7 WAN round trips (≈ 0.9 s at 130 ms) before any queueing on the single-CPU api.

A Rust client would cut ~0.4 s. Collapsing the ~25 calls into one server-side brief call would remove ~6 round trips (≈ 0.8 s) in any language (**E**).

## 6. Memory footprint

| Service | Limit (compose.prod.yaml) | Recorded |
|---|---|---|
| api | 2560m, 1.0 CPU | 1.215 GiB (e2e-results.json:66); 453 MiB smoke (FINAL-GATE-RESULTS.md:34) |
| worker | 1536m, 1.0 CPU | 856 MiB, peak 985 MiB (E2E:68, 74); cgroup peak hits the limit through file cache, anon ~830 MiB (BACKLOG.md:53) |
| librarian | 512m, 0.5 CPU | 120 MiB |
| db | 2g, 1.0 CPU, `shared_buffers=512MB` | 75-96 MiB container RSS |
| caddy | 256m | 12-17 MiB |

Local RSS breakdown (**M**, macOS arm64, `ps`):

- Interpreter: 15 MiB.
- Server and librarian modules imported: 96 MiB.
- The o200k meter: +76 MiB.
- The ORT e5 session: +1.5 GiB on macOS. The model file `onnx/model.onnx` is 448 MB fp32, and it is loaded by both the api and the worker.

The memory is the model. A Rust process would save perhaps 100-170 MiB per process (interpreter, modules, Python objects) out of ~1 GiB (**E**).

## 7. What this means for the port

- **Rust cannot shrink** WAN (95-130 ms per round trip), SQL (the main share of query and write time, and all of the identifier tail), ONNX inference (99.8 % of embedding), or LLM time (ask, synthesis, risk judge, librarian, capture). Those cover nearly all of the wall time users wait on.
- **Rust could shrink:**
  - Query packing and preview CPU: ~10-50 ms per query on the M3, the one real Python hotspot.
  - Its serialising effect on the single-CPU api under concurrency.
  - Client start-up: ~0.35-0.45 s per brief or `hlm` call.
  - ~0.1-0.2 GiB RSS per process.
- **Cheaper ways to the same gains, in Python** (found along the way; none of them changed):
  1. `term_matches` and per-character `normalize` in `query_preview` are ~80 % of pack CPU. Normalise once per row, or vectorise, and keep the output byte-identical (it is gated).
  2. `embed_query` runs on the event loop (read_service.py:261), and ORT `threads=2` runs under `cpus: 1.0`.
  3. The brief and CLI import the whole `mcp` SDK (~200 ms) for a handful of JSON-RPC calls.
  4. The brief makes ~25 calls in 3 waves.
  5. The importer writes one item per request.
- **Uncertain.**
  - There is no per-stage server timing on prod. The Python share of server time comes from local CPU probes plus an assumed speed factor.
  - The synthetic packing probe uses fixture texts. Real hit texts and budgets vary.
  - One decisive local check, which needs Docker, so it was not done here: run G4 with `py-spy record` on the api, or with `pg_stat_statements` and per-step timers. That would give the real Python/SQL split before any port decision leans on CPU.
