# D-037 middleware implementation exchange

Date: 2026-09-22. Participants: root integrator and delegated middleware implementer
(native Codex collaboration; shared worktree `fix/d037`).

Implementer proposal: use an independent admin connection pool so queued device
requests cannot occupy revocation capacity; change the existing body timeout into
a progress/inactivity timeout and retain a separate generous total deadline; cap
registration bodies at 16 KiB; keep registration counters bounded with TTL/LRU;
accept forwarded client IPs only through configured trusted proxy networks.

Integrator response: create `app.state.admin_pool` with two connections, add
`request_body_total_timeout_s=300` and `trusted_proxy_ips` settings; wire registration
to `trusted_client_ip(scope, settings.trusted_proxy_ips)`. MCP requests must also
publish their remaining DB deadline through transaction-local
`hlmemo.request_db_timeout_ms` for the write-path advisory locks. PostgreSQL 17+
revocations must extend the transaction timeout along with statement/lock timeouts.

Resolution: reserve pool access for revoke routes and `/admin/*` requests bearing
the configured admin token; other callers cannot gain reserved capacity simply by
choosing an admin URL. Self-revoke retains reserved access. The existing exclusive
device advisory gate queues subsequent readers behind revocation. Roll back and
reuse REST connections on errors/cancellation; retire dispatched MCP connections
only on cancellation/timeout because detached SDK tasks may still reference them.
Only nonempty body chunks renew inactivity time; empty messages cannot keep a
request alive. Stalled-body 408 responses use transient `E_UNAVAILABLE` with
`retryable=true`.

Readiness integration follow-up: authenticated `/ready` uses reserved capacity for
its status gate; the actual readiness SQL uses a dedicated short-lived connection.
This preserves bearer denial behavior without joining the ordinary request queue.

Regression evidence targets: ten revocations under a 64-request device flood
(alternating admin/self-revoke), exact REST backend-PID reuse, progressing slow
bodies, idle/empty-body timeout, fixed registration cap, bounded/expired buckets,
and trusted versus spoofed forwarding chains. Parent integrator owns serialized DB
test execution and final acceptance results.

Independent native review: the middleware implementer inspected the other worker's
`write_service.py`, `write_queries.py`, `write_models.py`, and `replay.py` diff for
R06/R07/R08/F03/G-A/F16. No concrete defect found. Checked authorization before all
three advisory-key classes and reauthorization afterwards; link survivor interval
copying and replay after supersession; explicit nullable survivor access timestamps;
and card source replacement even when the new source list is empty. This is a
native Codex review, not the separately required external Opus adjudication.

Focused validation (explicit `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_d037`,
`HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models`):
`pytest tests/unit/test_d037_middleware.py tests/integration/test_d037_middleware.py tests/integration/test_request_lifetime.py -q -p no:cacheprovider`
reported `31 passed in 4.28s`. The flood regression completed all ten revocations;
the existing detached-MCP-worker timeout and repeated-cancellation cleanup checks
also passed. Focused Ruff lint/format checks passed.
