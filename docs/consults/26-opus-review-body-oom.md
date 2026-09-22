# Opus 5.5 review of body hardening + OOM fix (2026-09-23)

## Status

| Item | Status | Evidence |
|---|---|---|
| Under-grace trickle (the reproduced High) | **Vector closed** | I re-ran the fake-clock harness (`scratchpad/exp/grace2.py`) against the fix/body middleware. 255 KiB followed by 1 B every 29 s now gets a 408 at about 62 s (30 s + n/8192). Each IP holds at most 16 slots × 255 KiB, about 4 MiB for about 62 s. The earlier ~1028-socket attack is gone. |
| **Impact of that High (pre-auth global-budget DoS)** | **NOT closed** | Burst then trickle, same harness: 64 MiB sent at once, then 1 B every 29 s. The 64 MiB was **still held at t=8178 s** and ended with a 408 at about 8222 s, all before auth. |
| Per-client 16-slot cap / IPv6 /64 key | OK | Caddy overwrites XFF, so the pinned-subnet chain cannot be spoofed. `::ffff:a.b.c.d` becomes `::/64`, one bucket for every mapped IPv4. That only matters on a dual-stack peer; Go unmaps these, so Low. |
| `limit_concurrency` 512, keep-alive 5 s | OK with notes | Clients only talk to Caddy. A 5 s upstream idle limit against Caddy's 2 min keepalive can race and return a 502 on a POST (Low). Filling 512 slots with SSE needs trusted tokens, so it is owner-only (Low). |
| Shared embedder | OK | ORT `Run` and `tokenizers.encode_batch` are safe to call concurrently. The loop and the readiness thread share the session, and nothing is mutated after init. The worker builds its own session, which is intended. No other `Embedder(` call is reachable over HTTP. If model files are missing, the lifespan raises `FileNotFoundError` and the api crash-loops. The "model files missing" readiness branch (app.py:149) and its docstring are now dead in prod (Low). |
| tmpfs 320m inside 2560m | OK | Spooled bytes stay under the 256 MiB global budget. Measured anonymous peak is 956 MiB, plus 320 + 64 MiB tmpfs, plus the authenticated handler's copies (about 100 MiB per 38 MB write, at most about 6 in flight): about 1.9 GiB, under the 2.5 GiB limit. The 1536 "cgroup peak" figure is reclaimable page cache. |
| Spec vs code | Consistent | The formula, 8222 s / 4717.5 s, 429 `E_RATE_LIMITED` (mapped in errors.py), the uvicorn settings and the embedder lifecycle all match. No stale `total_timeout`/`grace` keys remain. |

## New defects

| file:line | Trigger | Observed vs expected | Sev | Deploy-blocking? | Conf |
|---|---|---|---|---|---|
| middleware.py:518-540 | 2 IPv4 addresses (or /64s) × 2 connections, no bearer check: send 64 MiB fast, then 1 B every <30 s | The whole 256 MiB global budget is held for about 2.3 h before auth, so every MCP POST gets 503. Cost: 256 MiB per 8222 s, about 260 kbit/s, 4 sockets. This is the same impact and cost as the old High, and cheaper in sockets. It existed before this change and the prior review listed it only as a tmpfs Medium. It is inherent to allowing 64 MiB at 8 KiB/s before auth. Expected: pre-auth bytes bounded. Fix: before reading the body, a short-lease `token_sha256` + `trusted` lookup, released before the read (no lock held, so D-037 still holds). Otherwise cap unauthenticated bodies at about 64 KiB. | High (residual of the D1 High) | yes | High (reproduced) |
| RUNBOOK:217-273 | IPv6 through the userland proxy | All IPv6 clients share one key, so one IPv6 attacker with 16 slow bodies gives legitimate IPv6 users 429s. Only registration is documented as affected. The AAAA-withhold procedure mitigates it. | Low | no | Med |
| app.py:149 / docstring | Model files missing | Crash-loop instead of readiness 503 `not_ready` | Low | no | High |
| Caddyfile reverse_proxy | Idle upstream connection closed at 5 s | Rare 502 on a non-idempotent POST. Set Caddy `keepalive` < 5 s. | Low | no | Med |

## Verdict
ANOTHER ROUND NEEDED: pre-auth global-budget hold via burst-then-trickle (middleware.py:518-540). The attack vector named in 23 is fixed, but its impact still stands at the same cost. The fix should be small: check the bearer with a short lookup before reading the body, or cap unauthenticated body bytes. If the owner decides this residual is not "new" under D-041, it must be recorded as an accepted High risk in DECISIONS, not left to BACKLOG silently. Everything else → BACKLOG.