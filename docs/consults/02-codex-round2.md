**A. Dedicated close tool**

Concede: add `memory.call_the_day`. Discoverability merits the estimated 150 tokens, subject to measuring actual schemas. Keep one implementation: close delegates to the same atomic, idempotent write service, accepting session ID, request ID, notes, an optional card update, and expected revisions. No hidden LLM summarization.

Compare dedicated-tool versus `kind`-enum variants on completed-session capture rate, omitted decisions, duplicates, and schema tokens.

**B. Forgetting**

Accept reversible archival. Start with **N=3 consecutive successful daily cycles**, at least 24 hours apart. Use query-independent retention `r = 2^(-idle_days/14)`, threshold **r<0.1**: approximately 47 idle days before eligibility. Count idleness from creation or last explicit drilldown/raw access; preview exposure does not reset it. Useful access resets the consecutive-cycle counter.

Archive only volatile, unpinned items without live dependencies. Stable facts and lessons remain exempt. Record an archive event, retain evidence and immutable IDs, and support `query(include_archived=true)` plus restoration. Record policy version and cycle IDs for replay.

First run in shadow mode. Replay frozen TR/DE/EN task histories—including dormant topics returning—against ranking-only decay at identical budgets. Require no lost previously successful gold-evidence retrievals, no Recall@5 decrease, and successful archived-ID lookup/replay. **No finite regression suite proves universally harmless forgetting.**

**C. Day-one project card**

Agree: the client LLM authors it through `memory.write(kind=project_card)`; Phase 0 means no **backend** LLM. Create a deterministic skeleton at project creation, then have the first client populate it during onboarding. Waiting until session close leaves the first response uncovered.

Cap the authored card at 512 pinned-tokenizer tokens; reference immutable source versions; require expected-revision checks. Close can atomically submit evidence and an optional card update, resolving references within that batch. Mark cards stale when dependencies change. Provenance supports auditing; it does not certify the author’s interpretation.

**D. Invocation contract**

Without hooks, my predicted ranking is **(3) injected prompt > (1) instruction file > (2) tool description**. None guarantees a model-issued call.

Upgrade (3): `hlm` itself executes `memory.query`, waits for success, injects the bounded result, then launches the CLI. Apply this preflight to all three:

| CLI | Task injection and persistent instruction |
|---|---|
| Claude Code | Positional prompt + `CLAUDE.md`. [Reference](https://code.claude.com/docs/en/cli-reference) |
| Codex | Positional `PROMPT` + `AGENTS.md`. [Reference](https://learn.chatgpt.com/docs/agent-configuration/agents-md) |
| Antigravity | `agy -p` for headless tasks + `GEMINI.md` or always-on workspace rules. [Headless](https://antigravity.google/docs/cli/headless/), [context compatibility](https://antigravity.google/docs/cli/gcli-migration/) |

The guarantee covers tasks entering this wrapper. Later tasks inside an existing session need an adapter intercepting every task; startup injection is insufficient. Optional **(4) hooks** can enforce that boundary; reminder-only hooks remain probabilistic.

Measure successful, correct-project, task-relevant query completion before first substantive action, correlating server and CLI traces. Include resume, compaction, later tasks, and outages. Start with 300 independent trials per pinned CLI/configuration; 300/300 yields approximately a 99% one-sided 95% lower bound. Enforcing wrappers must block on query failure.

**E. Final Phase-0 surface**

**Five tools:** `memory.query`, `memory.drilldown`, `memory.raw`, `memory.write`, `memory.call_the_day`. Operational health stays on `/health`.

**Seven tables:**

- `projects` — identity and scoped policy.
- `events` — authoritative payloads, provenance, idempotency.
- `memory_versions` — bi-temporal assertions, cards, lifecycle.
- `chunks` — immutable source spans.
- `embeddings` — vectors and model/preprocessing revisions.
- `links` — bi-temporal relationships and derivation dependencies.
- `jobs` — transactional outbox, retries, maintenance cycles.

Sessions are event aggregates; clues reference immutable versions/chunks. Access and archival events rebuild retention state. Events and immediate projections commit atomically.

**F. One research risk**

The Luna EU fallback assumes unverified account eligibility. Non-US residency requires approved abuse-monitoring controls and a Modified Retention amendment; an EU endpoint alone is insufficient. [Official OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

Make eligibility and the exact model/provider route a bench prerequisite. The current OpenRouter catalogue discovery does not establish either.