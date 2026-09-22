# Opus 5.5 review of the pre-body authentication gate (2026-09-23) — verdict: PHASE 0 CAN CLOSE

## Status

I reviewed commit f0016fd in /Users/cemalkurt/Projects/HLMemo-bake/fix-auth. I also ran fake-pool experiments against the real `AuthMiddleware`, with timings scaled down, from `scratchpad/exp/gate.py`.

| Area | Result |
|---|---|
| **Pre-auth hold** | **Closed.** With no bearer, a junk bearer, a revoked device or a pending device, the request is rejected with 401/401/401/403. No body bytes are read and 0 bytes are budgeted. The only bytes held are the ≤64 KiB that uvicorn buffers per connection, which was already true before. |
| **Routes that skip the gate** (`/health`, `/ready`, `/devices/register`) | Trickling under the grace period was cut off at the fixed base time (0.3 s scaled, 30 s in production), capped at 64 KiB (16 KiB for register). Worst case is 16 × 64 KiB = 1 MiB per client. Globally it is ≤512 × 64 KiB = 32 MiB, well under the 256 MiB budget. |
| **Valid, trusted device** | Can still hold the budget (you said this is acceptable for backlog). A chunked upload with no Content-Length can send 64 MiB fast and then 1 byte every <30 s, holding 64 MiB for 30 + 8192 s ≈ **2 h 17 min**, renewable. Two requests fill one client's 128 MiB. Two client keys (two IPs, or all IPv6 clients sharing one bucket) fill the whole 256 MiB, so every non-empty body gets 503, including registration. **Revoking the device does not abort its in-flight uploads.** The rejection only comes after the body finishes, so the fix is to restart the API. |
| **Flood through the gate** | Not a new lever. Previously, bad bearers read the body and then waited up to 5 s for a pool connection; now each holds one of the 8 connections for about 1 ms, with a 250 ms wait cap. A flood is limited by 16 slots per client and uvicorn's 512-connection limit (that limit was already shared by everyone). The admin revoke path has its own pool (2 connections) and its own slots, so a flood does not affect it. `token_sha256` has a UNIQUE index (`devices_token_sha256_key`). The gate's `SET LOCAL` works because the pool runs with autocommit off, and the context exit commits, so there is no rollback warning per request. |
| **Correctness** | Revoke between the gate and the in-transaction resolve is still enforced: `resolve` rechecks under FOR SHARE and the advisory lock. Changing `token_generation` always rotates the hash, so the gate is safe to ignore it. Pending → 403. The admin token is compared in constant time. `/health` and `/ready` skip the gate even with a bearer (cap 64 KiB). Register with a bearer is not gated before the body (16 KiB); the in-transaction resolve still checks it. Chunked bodies without Content-Length fall back to the route cap. Expect: 100-continue is only answered after auth, because `receive` is never called on rejection. Pipelining: uvicorn discards the unread body. |
| **Readiness** | The shared embedder is built only inside the single shielded probe under `model_check_lock`, and `embedding_deferred` is cleared only on success. There is no second embedder: MCP raises an error if `read_deps` is None. Once the model files appear, recovery happens on the next probe. |
| **Spec / RUNBOOK** | Consistent with the code: the 250 ms wait, the separate admin slots and `keepalive 4s` are all described. One wording slip: the docstring at middleware.py:7-8 says register is "gated" (in fact only the post-body resolve checks it). |

## New defects

| file:line | trigger | observed vs expected | Sev | deploy-blocking? | conf |
|---|---|---|---|---|---|
| middleware.py:543, 302-306 | More than 8 legitimate requests running at once, each holding a pool connection | The gate gives up after 250 ms and returns 503 (retryable); before this change the request queued for up to 5 s | Low-Med | No | High |
| middleware.py:286 vs 358-366 | A device revoking itself while the pool is saturated | The gate's 250 ms cap cancels out the longer wait the design gives self-revocation; the admin revoke path still works | Low | No | High |
| middleware.py:586-610 | Admin revokes a device in the middle of its upload | That upload's budget is held until the body deadline (≤8222 s); expected: freed on revoke | Low (needs a trusted device) | No | High |
| app.py:329 + 250 | Model files present but not yet fully copied/verified when a probe runs | The session is loaded before the hash check; if the load succeeds on a bad file, it is never rebuilt after the files are fixed | Low | No | Med |
| middleware.py:541-549 | The database stalls at the TCP level | The shielded gate lookup has no client-side timeout, so it can hold a per-client slot indefinitely | Low | No | Med |

## Verdict

**PHASE 0 CAN CLOSE.** I found no new High and no deploy-blocking Medium. All five rows go to the BACKLOG.