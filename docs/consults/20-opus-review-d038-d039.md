# Opus 5.5 adversarial review of D-038 (deploy) + D-039 (src) (2026-09-23)

## A: D-039 status (tree fix-d039 @ 63b18e4)

| Item | Status | Evidence / attack result |
|---|---|---|
| N1 bounded /ready | FIXED | app.py:163-192. There is one shared task and one DB permit, and the connect and query are bounded by a 2 s timeout. The cache TTL of 1 s is set when the probe completes, so a stale "ready" lasts at most about 3 s after the DB dies, and the cache cannot be poisoned. |
| N3 admin-only reserved pool | FIXED | middleware.py:335. The pool is chosen by a constant-time match on the admin token, not by the path. Trailing slashes, URL-encoding or a spoofed `/admin/` prefix cannot reach the admin pool without the token. |
| N2 body budget + min rate | PARTIAL, and it adds a regression (D1, D2) | middleware.py:140-185, 257-301. Chunked uploads without Content-Length only reserve bytes actually received, so they are fine. The lever is a declared Content-Length. |
| N4 no default trusted proxies | PARTIAL | config.py:172 is fixed in code. The deploy side is not: neither tree sets `HLM_TRUSTED_PROXY_IPS`, compose.prod.yaml:148-153 still has no pinned subnets, and api.env.example:7 keeps the dead `FORWARDED_ALLOW_IPS=*`. So every public client now keys to Caddy's IP (D3). XFF parsing peels trusted hops right-to-left correctly and a spoofed left-most entry is ignored. One flaw: a junk entry anywhere in the chain makes middleware.py:130 fall back to the proxy IP (Low). |
| N5 card links | FIXED (mitigated) | write_queries.py:210-235. There is now a range pre-filter, and the overlap check at write_service.py:780 keeps the same meaning. There is still no LIMIT, so the cost now grows with the links overlapping the interval. |
| Spec sync | PARTIAL | See the spec issues below. |

## B: D-038 status (main working copy)

| Item | Status | Evidence / attack result |
|---|---|---|
| N1 immutable tags + atomic env | FIXED, with one hole (D4) | remote-deploy.sh:158-215 and release_env.py. The baseline is tagged from the running image ID, and the env file is switched only after internal readiness. I found no path where the env file names an image that does not exist locally, except the harmless initial-failure case (`hlmemo:prod` remains, compose can build it). |
| N2 runner from target ref | FIXED | deploy.sh:95-111 uses `git show` on FETCH_HEAD. The D-035 runner (5624ec1) takes the same 5 arguments, takes its own lock after the bootstrap releases it, and writes the status file. The small lock hand-off window is harmless. |
| N3 PID/heartbeat/timeout | FIXED | deploy.sh:61-80, 124-158. The observer checks whether the PID is alive and not a zombie; it does not judge progress by heartbeat age. A long migration therefore cannot cause a false positive. The only effect is the 1800 s observation timeout exiting with 124 while the remote run continues and keeps the lock. |
| N4 no rollback across compose change | FIXED | remote-deploy.sh:121-124 refuses any change to `compose.prod.yaml` before build or stop. Mounted files come from the checkout of `previous` during rollback. |
| Unique backup names | FIXED | backup.sh:35-43 adds an mktemp suffix. The timer and a deploy serialize on `.operation.flock`, and a deploy that loses the race aborts before stopping services. |
| Ruff debt | PARTIAL | tests/deploy is clean. `uv run ruff check deploy` still finds 14 errors, including I001 at retention.py:3, a file this diff touched. |

## New defects

| # | file:line | Trigger → observed vs expected | Sev | Conf |
|---|---|---|---|---|
| D1 | middleware.py:271, 292 | An unauthenticated `POST /mcp` with `Bearer x` and `Content-Length: 2000000` (under Caddy's 2 MB cap) reserves the full declared size immediately. The client then trickles 1 byte every 9 s and stays under the 64 KiB grace, so the rate check never runs, for up to 300 s. About 64 such connections fill the 128 MiB per-client budget, which behind Caddy is shared by everyone (D3). About 128 connections from 2 IPs fill the 256 MiB global budget. Observed: every POST with a body, i.e. every MCP tool call, gets 503. Expected: charge only bytes received, or apply the rate check before the grace. This is cheaper than the memory-pinning attack N2 was meant to fix. | High | High |
| D2 | middleware.py:292-301; min rate 128 KiB/s | 128 KiB/s is about 1.05 Mbit/s. Any body over 64 KiB from a slower uplink, such as mobile or 3G, gets 408. Because the next-chunk deadline equals `rate_deadline`, any pause longer than the client's current lead also kills the upload. The 408 maps to retryable E_UNAVAILABLE, so the client retries into the same failure. | Med | High |
| D3 | config.py:172 + deploy (both trees) | With the empty default, all traffic keys to Caddy's IP. The register limiter (5/min) and the per-client body budget become global, so anyone can block registrations. This is worse than the old 172.18/16 guess. | High (deploy) | High |
| D4 | remote-deploy.sh:186 with an older target runner | Deploy a pre-D-038 ref after a D-038 release (the usual "redeploy previous" rollback). The D-035 runner runs `dc build` against the env's `HLM_IMAGE=hlmemo:<R_new>` and retags that tag with old code. A later redeploy of R_new skips the build because the tag exists, migrates with the old alembic, passes /ready, and reports success while running the wrong code. Expected: refuse pre-D-038 runners, or verify an image label equals the revision. | Med | Med-High |
| D5 | remote-deploy.sh:216-228 | If writing the marker files fails after cutover (for example, disk full), `current-ref` stays at R1 while R2 is running. Every later deploy then stops at :175 ("Previous release tag differs"). | Low | Med |

Also noted, not new: writes accepted between the live pre-upgrade dump and `dc stop` are lost if the snapshot is restored. The RUNBOOK documents this.

## Spec (PHASE0-SPEC) inconsistencies introduced by A

- §D-037 (line 347) now says the edge limit must be at least 64 MiB unless a lower public contract is documented. Caddyfile:26-28 is still `max_size 2MB` in both trees, and no lower contract is documented.
- The documented minimum rate times the total timeout is 128 KiB/s × 300 s = 37.5 MiB. That is below the 64 MiB envelope cap, and the 38.4 MB contract body only just fits. The spec's claim that the minimum rate supports the contract maximum does not hold.
- The spec says to "configure the actual Caddy subnet explicitly", but no deploy artifact does or can do this, because compose leaves the subnets unpinned.

## Verdict

- **A:** Not ready to merge. N1 and N3 are solid, but D1 (declared-length pre-reservation) and D3 must be fixed first, and the min rate should be relaxed (D2).
- **B:** Ready for a first deploy on an empty host once D3 is fixed in the deploy config (pin the frontend subnet and set `HLM_TRUSTED_PROXY_IPS`) and Caddy's `max_size` is reconciled with the spec. D4 should be fixed before any rollback-by-redeploy is relied on.

Files: /Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/server/middleware.py, /Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/config.py, /Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh, /Users/cemalkurt/Projects/HLMemo/deploy/Caddyfile, /Users/cemalkurt/Projects/HLMemo/deploy/compose.prod.yaml, /Users/cemalkurt/Projects/HLMemo/deploy/api.env.example