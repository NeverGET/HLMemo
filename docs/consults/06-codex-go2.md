1. Residuals:

- **B5 RESOLVED** — Pinned dependencies survive temporal filtering for staleness evaluation.
- **D2 RESOLVED** — Shared authorization and operation-specific filters are separated; raw endpoints are explicitly authorized.
- **D4 RESOLVED** — Unset/empty admin environment resets the hash and increments generation before listening.
- **Staleness unreachable: RESOLVED** — Superseded and expired authorized sources remain available to the stale calculation.
- **One predicate for every read: RESOLVED** — Cards, historical raw reads, and ordinary retrieval now have compatible filters.
- **Unset-env credential retention: RESOLVED** — Previously bound admin credentials are disabled on restart.

2. No new blocking contradictions found.

3. **GO for Phase-0 implementation.** Local validation gates remain required before deployment.

First three tasks, in order:

1. **≤1 day:** Scaffold package/configuration, local Compose database, migration `0001`, and PostgreSQL integration-test fixtures.
2. **≤1 day:** Implement transactional device authentication, admin startup binding, and shared project/device authorization.
3. **≤1 day:** Implement the initial-create write path: atomic events, versions, chunks, links, and outbox entries, with device-scoped idempotency and replay reauthorization.

**First test:** `test_device1_restart_without_env_disables_admin`: bind token → successful authentication → restart unset/empty → placeholder, generation increment, old bearer `E_AUTH` → rebind → authentication succeeds, old cursor `E_INVALID_CURSOR`. Check cursor rejection after authentication is restored so middleware error precedence stays intact.