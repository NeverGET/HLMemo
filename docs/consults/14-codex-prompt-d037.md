You are the implementer on HLMemo. Work ONLY in the git worktree /Users/cemalkurt/Projects/HLMemo-bake/fix-d037 (branch fix/d037, created from main). Do not git commit. A separate process is editing deploy/ in the main checkout — do not touch deploy/, Makefile, or tests/deploy/.

Read DECISIONS.md D-033, D-036 (the D-037 backlog list), docs/bakeoff/r3/RESULT.md, docs/bakeoff/r3/blinded-findings.md (R01..R10 claims; subA = the code now on main, which is YOUR R3 branch), and docs/bakeoff/r1/repro/VERDICTS.md (F-series). Fix ALL of the following in src/hlmemo/**, each with a regression test under tests/:

SECURITY / CORRECTNESS
1. R06 High — advisory locks on logical_id/request key are taken BEFORE revision authorization, so a caller can distinguish a hidden id (LockNotAvailable / E_UNAVAILABLE under contention) from an unused id (E_NOT_FOUND). Authorize first, or make lock failures on unauthorized targets indistinguishable (uniform E_NOT_FOUND). Test under contention.
2. R07 High — a `project_card` written with device_scope other than "all" hides the shared card from other devices and locks them out of card updates forever (and reveals existence when expected_version_id=null). The project card must be device_scope="all" (reject other scopes with E_INVALID_ARG at validation; migrate-safe for existing rows is not needed in Phase 0 but add a check).
3. R08 Med — `lock_timeout` turns contended same-logical_id revisions into E_UNAVAILABLE instead of the spec's E_VERSION_CONFLICT / stored replay / E_SESSION_CLOSED. Use a per-statement lock timeout sized to the request DB deadline for these advisory locks (SET LOCAL), or re-check and map to the spec'd code.
4. F16 Med — card `derived_from` links are never superseded on a card revision → permanent staleness via old sources and unbounded link lists (raw E_BUDGET_TOO_SMALL after ~30 closes). Supersede the previous card version's derived_from links on every card revision; make raw/drilldown link lists temporally filtered and cursor-paged within the budget. Repro: docs/bakeoff/r1/repro/test_f16_card_links.py.
5. F03 Med — corrections do not create link survivor segments (a correction on [cf,ct) removes edges outside that window). Emit link survivor rows like version survivors, record them in `resolved`, replay them. Repro: test_f03_link_survivors.py. Update the existing test that asserts "exactly one current link" if it encodes the defect.
6. G-A (F02/F07) Low — corrections copy an unlocked, unrecorded `last_access_at` into survivors → replay divergence. Record the survivor's last_access_at in `resolved.survivors` (or read FOR UPDATE). Repro: test_f07_replay_last_access.py.
7. F15 Med — after revoke, re-registering the same machine fails on the fingerprint unique constraint (spec §2 requires a fallback). Allow re-registration of a revoked device's fingerprint (new device row, old one stays revoked) or implement the spec's random-id fallback. Repro: test_f15_fingerprint_reregister.py.
8. F05 Med — the /devices/register rate limiter keys on the proxy IP behind Caddy and its dict grows unbounded. Use the real client IP only from a trusted proxy (configure uvicorn forwarded_allow_ips / proxy_headers for the Caddy network, or read X-Forwarded-For only when the peer is a configured trusted proxy), and make the store bounded (LRU/TTL). Repro: test_f05_register_rate_limit.py.

AVAILABILITY
9. R01 Med — `/ready` acquires a pooled connection and returns 503 under pool pressure. Readiness must use a dedicated short-lived connection (or a reserved slot) with its own short timeout.
10. R05 Med — revoke can still fail under a request flood from the device being revoked. Reserve capacity for admin/revoke (a small dedicated admin pool or a global in-flight semaphore that always leaves a slot for /admin and /devices/revoke). Test: flood + revoke must succeed (not just fail fast) in ≥ 10/10 attempts.
11. R04 Low — replace the total body-read deadline with an inactivity / minimum-rate timeout so a legitimate large body on a slow link is not 408'd; keep the total cap generous.
12. R10 Low — only discard the pooled connection on /mcp cancellation/timeout, not on every REST route HlmError.
13. Port from the other R3 branch: server-side `transaction_timeout` on PostgreSQL ≥ 17 as an additional safety net; a small fixed body cap (16 KiB) for unauthenticated /devices/register; `retryable: true` on the stalled-body 408.
14. F04 (API side) — `create_app`/lifespan must fail fast when HLM_EMBED_MODEL/HLM_EMBED_REVISION differ from models.lock (use `require_pinned_embed_config` from core/embedder.py), and `/ready` must report `embed_config`.
15. MCP SDK 4 MiB transport cap vs spec §3 maxima (50 × 64,000-char bodies): pick one and implement: either raise the SDK's max request body size via its supported configuration to match the app cap, or lower the documented contract maxima (write_models limits + PHASE0-SPEC §3 note) so every contract-valid request fits. Report which and why.

DATABASE: create your own DB `hlm_d037` and ALWAYS run pytest with `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_d037`; never without it; never use hlm, hlm_retr, hlm_verify, hlm_mainfix, hlm_r1judge. The worktree has no models/: export `HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models`. For the F-series repro tests: `PYTHONPATH=. HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_r1judge` (their conftest requires that DB name; run them sequentially, they truncate it).

ACCEPTANCE (report exact summary lines):
- ALL 17 repro tests: `PYTHONPATH=. HLM_TEST_DSN=.../hlm_r1judge uv run --frozen pytest docs/bakeoff/r1/repro -q -p no:cacheprovider` → all pass (this is the Phase-0 DONE criterion in D-033).
- Full suite on hlm_d037 (same ignores as before) → green, run twice.
- `uv run ruff check src tests/unit tests/integration tests/fixtures` + `ruff format --check` on the same paths → clean.
- G3/G4 once at the end: `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr uv run --frozen pytest tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py -q -s` (read-only; restore HARDWARE.md afterwards) → Recall@5 ≥ 0.90, p95 ≤ 500 ms.

FINAL MESSAGE (max ~500 words): per item "fixed: file:line — what; test"; acceptance lines; G3/G4 numbers; any contract change you made (spec section + one line why).
