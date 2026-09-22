# Opus 5.5 adversarial review of D-037 (2026-09-22)

## Item status

| # | Item | Status | Where / attack result |
|---|---|---|---|
| 1 | R06 lock-before-authz | FIXED | write_service.py:669 runs `_authorize_revisions` before any advisory lock and again after (681). Hidden and unused ids both get the same `E_NOT_FOUND` with no lock attempted. I found no way through error class, details, retryable, cursor or link lists to tell them apart (timing excluded). |
| 2 | R07 card scope | FIXED | write_models.py:79 and write_service.py:461. Validation only, no DB CHECK, so old rows that are not scoped "all" can still exist (the brief allows this). |
| 3 | R08 contention | FIXED | write_queries.py:101 lets lock waits use the request budget. Leftover: `hlmemo.request_db_timeout_ms` is set once when the connection is leased (middleware.py:314) and never counts down. The asyncio deadline still bounds it, so a timeout gives E_UNAVAILABLE and the connection is discarded. |
| 4 | F16 card links + paging | FIXED | write_service.py:759-785; paging in read_service.py:353-406 and 582-618. Replay order and link-id allocation match. The budget promise holds: `_pack_page` measures the full page exactly and the prefix is exact-settled. Cursors freeze valid_at/known_at. |
| 5 | F03 link survivors | FIXED | write_service.py:819-833 and replay.py:173-189. Survivors are inserted first, before item links, in both write and replay, and after `supersede_links`. So the EXCLUDE constraint cannot fire and replay is byte-identical. |
| 6 | G-A last_access_at | FIXED | write_service.py:902; replay.py:124 (old events still replay). |
| 7 | F15 re-register | FIXED | devices.py:120-140. Falls back on any fingerprint collision, not only revoked devices. This matches spec l.601. |
| 8 | F05 proxy rate limit | PARTIAL | See N4. |
| 9 | R01 readiness | PARTIAL | /ready no longer uses the pool, but it introduces N1. |
| 10 | R05 admin capacity | PARTIAL | See N3. |
| 11 | R04 inactivity timeout | FIXED | middleware.py:171-205. But it causes N2. |
| 12 | R10 discard | FIXED | middleware.py:355-376. |
| 13 | PG17 / register cap / 408 | FIXED | pool.py:26, middleware.py:164, middleware.py:383 (E_UNAVAILABLE → retryable). |
| 14 | F04 API pin | FIXED | app.py:285 and 290; /ready reports `embed_config`. |
| 15 | 64 MiB cap | FIXED in app | The installed SDK does support `max_request_body_size`. But deploy/Caddyfile still has `request_body max_size 2MB`, so the new §3 promise (38.4 MB) is false behind the edge. |

## New defects

| ID | Location | Trigger → observed vs expected | Sev | Conf |
|---|---|---|---|---|
| N1 | app.py:169-176 | `GET /ready` needs no bearer and Caddy exposes it (`@rest`). Every probe opens a new Postgres connection (SCRAM login + backend fork) with no limit on concurrency. A flood hits `max_connections=100` ("too many clients"). The pools then cannot reconnect (and R10 discards on /mcp timeouts force reconnects), and /ready itself fails. Expected: a bounded probe (single-flight or semaphore plus a ~1 s cached result). | Med | High |
| N2 | middleware.py:164-205, config.py:161-163 | The whole body is read before auth; any syntactically valid `Bearer x` is enough. The cap went 4→64 MiB and the lifetime 10 s→300 s (trickle 1 byte every 9 s). Without Caddy, about 46 connections × 64 MiB reach the api's `mem_limit: 3g` for roughly 10 MB/s of attacker bandwidth. Behind Caddy (2 MB cap) it takes about 1500 connections, still ~10 MB/s. Before, holding 3 GB cost about 300 MB/s. Net effect: memory pinning is ~30× cheaper, a G-B regression. Expected: a per-IP or global in-flight body-bytes cap, or a minimum transfer rate. | Med | High |
| N3 | middleware.py:224-231, 319-336 | The reserved admin pool is chosen by URL path (`is_revoke`, and /ready when a bearer is sent), not by who the caller is. The device being revoked, D, sends several `POST /admin/devices/1/revoke` with its own token. Each goes to the 2-connection admin pool and runs `resolve(exclusive=True)` on D's key. Each waits up to 17 s (`lock_timeout`) behind D's own /mcp shared locks, then gets E_FORBIDDEN. Meanwhile the real admin revoke hits `pool_timeout_s` (5 s) and gets E_UNAVAILABLE. Junk bearers can also churn the queue. Expected: only the constant-time-matched admin token, or an authenticated self-revoke, gets the reserved slot. | Med | High |
| N4 | config.py:167, deploy/compose.prod.yaml:148-153, deploy/api.env.example | The default `trusted_proxy_ips` guesses `172.18.0.0/16`. The prod compose creates three networks without fixed subnets and never sets `HLM_TRUSTED_PROXY_IPS`. `FORWARDED_ALLOW_IPS=*` is now dead config (`proxy_headers=False`). If Caddy's IP is outside 172.18/16, every registrant shares one bucket. The limiter runs before the secret check (devices.py:93-104), so anyone can block all registrations with 5 requests/min. Expected: pin the frontend subnet, or set the variable in deploy. | Med | Med |
| N5 | write_service.py:759 | Card survivor links stay current (`superseded_at=inf`) forever, and `current_links_from` loads all of them on every card revision. Cost grows as closes × sources. | Low | High |

## Contract changes
- §1.1 and §4.4 raw: "no valid/known-time filtering" became "overlap on both axes". This is a real change to what the provenance view shows. F16 justifies it and the D-037 row mentions it, so it is acceptable.
- §3 content rules now say card revisions supersede `derived_from` edges, but §1.1 backdated correction (4) still says links are "untouched". F03 survivors also contradict (4). §1.1 needs amending.
- Missing spec updates:
  - The canonical `payload.resolved` example (≈l.305-308) lacks `link_survivors` and `survivors[].last_access_at`.
  - The `device_registered` resolved payload lacks `fingerprint`.
  - The drilldown/raw cursor description is stale: it still says `{version_id, ordinal,…}`, but cursors now carry `h`, `i`, valid_at and known_at.
- §3 transport bound: the text is coherent, but deploy (Caddy 2 MB) contradicts it. Either raise Caddy's `max_size` or record the edge limit as a decision.
- The new settings (`trusted_proxy_ips`, `readiness_timeout_s`, `request_body_total_timeout_s`, `db_transaction_timeout_ms`) are not in the spec's config section.
- The D-037 row was written by the implementer as ACCEPTED before the gates and review that D-036 requires. It should be PROPOSED until the owner ratifies it.

## Verdict
The correctness and security items are fixed; I found no remaining hidden-vs-unused oracle. Phase 0 can be declared DONE after a small follow-up on N1, N3 and N4 (and the three spec edits above). Blockers for the first public VPS deploy are N1 (unauthenticated /ready can exhaust Postgres connections) and N4 (trusted-proxy subnet not configured in deploy). N2 and the Caddy-vs-spec body cap should be settled in the same pass.