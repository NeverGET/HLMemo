# Review of the exit-abort + worker OOM round (2026-09-23) — verdict: PHASE 0 CAN CLOSE

## Status

| Area | Result |
|---|---|
| Keyset paging / exactly-once | **Holds.** Chunks are inserted with explicit ids in the same write transaction as their version and job, and never added to a version later. Paging by `chunk_id` (not `ordinal`) cannot skip or duplicate a chunk. Keeping `NOT EXISTS` + the embeddings PK `(chunk_id, model, revision, preproc)` + `ON CONFLICT DO NOTHING` means a takeover worker blocks on our uncommitted rows, then does nothing. Our `_mark_done` fence fails and the whole job rolls back. |
| D-030 / O2 crash test | **Still valid.** `_test_barrier` still runs inside the fenced transaction, just before `_mark_done`, so nothing is committed before the fence. |
| Long transaction vs idle_in_transaction | **Not affected.** The 5–15 s timeouts are set only on pool connections (`db/pool.py:22-23`). The worker opens its own connection with `AsyncConnection.connect`, which has no idle or statement timeout. Measured cost: 32 texts ≈ 8 s (`tests/auth-worker-throughput.log`), so a maximum-size item (64,000 chars ≈ 178 chunks) takes about 45 s. That is below the lease (120 s), the health window (180 s) and the worker's `stop_grace_period` (150 s). There is no loop-forever path with one worker. |
| Throughput / starvation | Jobs are strictly first-in-first-out (`ORDER BY run_after, job_id`) with one job per lease. A 50 × 64k write (~8,900 chunks × ~0.25 s) puts about 37 min of work ahead of any later write's vectors. Those later memories stay findable through lexical and trigram search. Not deploy-blocking for one user. |
| Shutdown | Clean. Probe cancel, `_run_native` drain, executor join (idle by then), `embedder.close()`, then pools. MCP is stateless with JSON responses, so there are no SSE streams to hang on. `embed_query` runs synchronously on the event loop (`read_service.py:190`), so `close()` can never run at the same time as an inference. A late query would get an AttributeError, not a native crash. The worker only checks the stop event between jobs, and a job takes ≤45 s, well inside 150 s. |
| ORT telemetry | Effective. ORT 1.30's `Privacy.md` and its dylib both honour `ORT_DISABLE_TELEMETRY`. It is set at import of `embedder.py`, which happens before the lazy `import onnxruntime` and is pulled in by both the API app and the worker `__main__`. It is also set in the Dockerfile `ENV` and the compose environment, which covers the migrate container. Alembic never loads ORT. |

## New defects (none High, none deploy-blocking)

| file:line | trigger | observed vs expected | Sev | Blocking? | Conf |
|---|---|---|---|---|---|
| worker/main.py:63, 417 | 2 or more worker replicas, or a job taking more than 120 s (only happens with a ~2.7× CPU slowdown) | There is no lease renewal, so a second worker takes over the job. The first worker's work is thrown away, and the two can ping-pong forever. Expected: renew the lease per page. Existed before this change; the compose file runs one worker. | Low | No | High |
| worker/main.py:565 plus `worker_health.py` | a job taking more than 180 s | The heartbeat is only written between jobs, so the worker shows unhealthy. `deploy --wait` could then fail if a long job was still waiting after a restart. Not reachable at current sizes (≤45 s per job). | Low | No | Med |
| worker/main.py:117 | a large write followed by small writes | The small writes wait up to about 37 min for their vectors. Expected: fair ordering, e.g. interleave jobs or prefer small ones. | Low | No | High |
| worker/main.py:122 | a job that gets the worker OOM- or segfault-killed | `attempts` goes up on every lease, but `_mark_failed` only runs on a Python exception, so the job is re-leased forever. Existed before this change. | Low | No | Med |
| compose.prod.yaml:78 (api) | SIGTERM while a 64 MiB upload is still arriving | The API uses the default 10 s stop grace, which is shorter than the body timeouts. It is SIGKILLed and exits 137. Nothing was committed, so no data is lost. | Low | No | Med |

All five should go to `docs/status/BACKLOG.md`.

## Verdict
**PHASE 0 CAN CLOSE**

This was read-only: no tests or docker were run. The findings come from reading the diff (f0016fd → 409f30d on fix/auth), `src/hlmemo/worker/main.py`, `src/hlmemo/server/app.py`, `deploy/compose.prod.yaml`, `deploy/scripts/{remote-deploy.sh,worker_health.py}`, the embeddings schema in `alembic/versions/0001_phase0.py`, and the timing numbers in `tests/auth-worker-throughput.log`.