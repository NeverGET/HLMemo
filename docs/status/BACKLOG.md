# Backlog (Phase 1+) — items deliberately NOT blocking the first deploy (D-041)
Updated 2026-09-23. Source of each item in parentheses.

## Security / availability (authenticated or low impact)
- A revoked device's in-flight upload keeps its body budget until the body deadline (≤ 8222 s); abort in-flight reads on revoke (consults/28).
- A trusted device can hold 64 MiB per request for ~2 h 17 min with a slow chunked upload (two requests → per-client 128 MiB; two client keys → global 256 MiB). Owner-only lever; consider per-device body budget and shorter total cap for chunked bodies (consults/28).
- Gate pool wait capped at 250 ms → 503 under >8 concurrent legitimate requests (previously queued up to 5 s); tune pool size / wait (consults/28).
- Self-revoke under pool saturation loses its longer wait at the gate (admin revoke unaffected) (consults/28).
- Gate lookup has no client-side timeout on a TCP-level DB stall → can hold a per-client slot (consults/28).
- Readiness: model loaded before hash check; a bad-but-loadable file is never rebuilt (consults/28).
- IPv6 through docker userland proxy: all IPv6 clients share one limiter key; withhold AAAA or enable IPv6 on the compose network (consults/26, RUNBOOK).
- Caddy keeps the client socket open until the next body write after the API's 408 (API budget is freed on time) (closing/recheck 4a).
- `::ffff:a.b.c.d` mapped addresses collapse to one /64 bucket (consults/26, Low).
- XFF junk entry makes the limiter fall back to the proxy IP (consults/20, Low).
- No uvicorn-level protection beyond limit_concurrency 512 against pure slowloris (consults/23, Low-Med).
## Correctness / ops
- Marker write failure after cutover leaves current-ref behind → later deploys refuse (consults/20, D5 Low).
- Card survivor-link load still grows with links overlapping the interval (N5 residual, consults/20).
- Writes accepted between the live pre-upgrade dump and `dc stop` are lost if that snapshot is restored (documented in RUNBOOK).
- bootstrap.sh re-run with a changed --admin-cidr may stop before 80/443 under set -e; provider "allow 22" rules make --admin-cidr ineffective (consults/23, Low).
- middleware.py docstring says register is "gated" (it is checked only post-body).
## Retrieval / embeddings (D-042)
- Embedder provider interface + async query embedding; gemini-embedding-2 / pplx-embed-v1-4b as opt-in profiles after RRF re-tuning and latency measured from the VPS.
- int8-quantized e5 ONNX to cut RAM/CPU.
- memory.raw paging instead of E_BUDGET_TOO_SMALL for oversized payload_item (D-026).
## Worker (consults/30)
- No lease renewal: >1 worker replica or a job >120 s → takeover ping-pong (compose runs one worker; jobs ≤ ~45 s today).
- Heartbeat written only between jobs → a job >180 s marks the worker unhealthy.
- FIFO queue: small writes wait behind a contract-max write (~37 min) for their vectors (still found via lexical/trigram) → fair ordering.
- OOM/segfault-killed job is re-leased forever (attempts grow, _mark_failed only on Python exceptions) → cap attempts.
- API stop grace 10 s < body timeouts → SIGKILL (exit 137) during an in-flight 64 MiB upload (nothing committed).
- Contract-max write embeds in ~38 min on 1 CPU (2 × 512-token texts per inference) → faster batching / int8 model.

- Worker cgroup memory.peak reaches its 1536 MiB limit through file cache after reboot (anon ~830 MiB, no OOM kill) — watch on the VPS; consider 1792m or dropping page cache pressure (D-045).
- first_deploy.sh `--host-fingerprint` pins only the ED25519 host key; an ECDSA/RSA fingerprint from a provider console is rejected as a mismatch. Accept any key type or name the expected type in the error (hit on the real deploy 2026-09-23).

## Process
- Test suite wall time regressed to ~52 min in the auth-gate round (under investigation in the closing verification).

## Retrieval (W-E findings, 2026-09-23)
- Drill budget vs D-055 "drill the top 5" guidance: on corpus B, 53 of 80 top-5 drilldowns hit the 4000-token drilldown budget and were truncated. Raise the default drill budget for multi-clue calls, or split the budget fairly per clue. Small, pre-librarian; measure on corpus A + B dev.
- W0a /ready detail gating trusts the raw socket peer (loopback = details). This is safe while Caddy reaches the API over the docker network, but a reverse proxy running inside the API's own network namespace would expose details to the public. If the topology ever changes, gate the details on a separate loopback-only listener or an ops token (verifier note, 2026-09-23).
- [FIXED 4e9b00b] W2e synthesis fallback is ineffective under the 6 s cap: the provider retries the primary with 1/2/4/8 s backoff before trying the fallback, so an outage or stall of the primary times out before the fallback runs (found by gpt-5.6-sol in the D-084 bake-off). Fix: a synthesis-specific attempt budget (one bounded primary attempt, then the fallback within the deadline) + a timeout/503 failover test. The same applies to risk_judge (4 s cap).
