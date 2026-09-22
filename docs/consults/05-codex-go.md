1. Specification re-check:

- **B1 RESOLVED** — Single canonical text result; no duplicate structured output or output schema.
- **B2 RESOLVED** — Embedded grants require atomic project-admin authorization; global revocation is restricted.
- **B3 RESOLVED** — Immutable home, old∪new membership authorization, replay reauthorization, and device-scoped request IDs are specified.
- **B4 RESOLVED** — Temporal exclusions permit surviving segments; corrections use one timestamp and preserve historical rows.
- **B5 PARTIAL** — Version pinning exists, but temporal endpoint filtering removes stale dependencies before staleness evaluation.
- **B6 RESOLVED** — Recorded identities, boundaries, defaults, and transformation versions define deterministic projection replay.
- **D1 RESOLVED** — Dedicated integrity rules and tests cover nonexistent/null/duplicate/missing-home memberships.
- **D2 PARTIAL** — The shared predicate conflicts with card and historical raw reads; raw endpoint authorization needs explicit wording.
- **D3 RESOLVED** — Transaction ordering, fresh authorization, and device/generation-bound cursors are specified.
- **D4 PARTIAL** — After an earlier token binding, restarting without `HLM_ADMIN_TOKEN` leaves the stored token active, contradicting “admin unusable.”
- **D5 RESOLVED** — Middleware and tests cover all routes and MCP initialize/list/call.

2. New blockers introduced by the fixes:

- **Staleness becomes unreachable (§1.1, §4.10):** A superseded/expired pinned source fails endpoint visibility before `stale` is computed. **Fix:** authorize pinned versions independently of temporal liveness; retain authorized dependencies for staleness evaluation. Test both superseded and expired sources.
- **One predicate cannot serve every read (§4.4):** `kind <> 'project_card'` excludes cards, while temporal filters exclude historical raw versions. **Fix:** separate shared project/device authorization from operation-specific kind/time filters; explicitly authorize raw edge endpoints.
- **Unset-env restart retains admin credentials (§2):** The startup update only handles a supplied token. **Fix:** when absent, reset the hash to the reserved placeholder and increment generation, or refuse startup; add a previously-bound restart test.

3. **NO-GO** — Resolve these three contract contradictions before Phase-0 implementation; successful DDL constraint tests do not exercise them.