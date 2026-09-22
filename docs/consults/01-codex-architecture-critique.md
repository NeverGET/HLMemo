**1. TOP-5 RISKS / FLAWS**

1. **Unsupported integration premise.** Graphiti’s documented backends exclude Postgres/AGE. Combining them means maintaining an adapter; choosing FalkorDB introduces another database. **Mitigation:** establish one authoritative Postgres store and defer Graphiti until measured requirements justify it. [Graphiti documentation](https://github.com/getzep/graphiti).
2. **Concurrency is deferred; correctness cannot be.** The report postpones event logging to Phase 4 while promising concurrent writers. An append-only log preserves contradictory assertions; it does not resolve them. **Mitigation:** atomically persist events and visible projections from day one; enforce idempotency, expected revisions, server timestamps, and explicit conflicts.
3. **Forgetting destroys evidence and entrenches mistakes.** Query-dependent relevance is unsuitable for global deletion; usage rewards already-visible memories. Recursive summaries amplify errors, while near-immortal L4 lessons resist correction. **Mitigation:** decay ranking only; retain sources, version derivations, invalidate stale summaries, and give lessons applicability conditions and review dates.
4. **Placement is confused with authorization.** “Write to both places” can leak private material; clue/raw lookups lack explicit scope. **Mitigation:** credential-bound project permissions, authorization on every dereference, quarantine ambiguous placement, and explicit cross-project promotion. Retrieved instructions remain untrusted data.
5. **Proactivity lacks an invocation contract.** A server cannot warn a CLI that never calls it; replacing hooks with `risk_check` does not solve that. **Mitigation:** test a task-start/pre-action wrapper, or explicitly promise best-effort recall. Return “no matching evidence,” never “no risk.”

**2. DISAGREEMENTS**

Own a small bi-temporal Postgres schema with ordinary relational edges; borrow Graphiti’s concepts, not its ingestion machinery. Neither AGE nor FalkorDB earns its operational cost for this workload yet.

Make events authoritative immediately, without Kafka or a general event-sourcing framework. Treat L1–L4 as rebuildable views with provenance. Start with an explicitly authored project card and extractive previews; defer RAPTOR until it beats flat hybrid retrieval on HLMemo tasks. Vendor dialogue scores do not establish coding-memory quality.

**3. PHASE-0 MVP SCOPE**

**Exactly four tools:**

- `memory.query(project_id, query, token_budget, valid_at?, known_at?, cursor?)`
- `memory.drilldown(project_id, clue_ids, token_budget, cursor?)`
- `memory.raw(project_id, version_id, token_budget, cursor?)`
- `memory.write(project_id, request_id, items, expected_versions)`

Session close and lessons are `write` kinds. Defer other tools, generative calls, PDF/DOCX ingestion, consolidation, and cross-project retrieval. Clues reference immutable versions. Budget the complete serialized payload using a named, pinned tokenizer; reject undersized budgets. Token counts are not interchangeable across clients.

**Seven tables:** `projects` (stable IDs); `events` (original payload, provenance, schema version, unique request key); `memory_versions` (logical ID, kind, status, valid-time/system-time intervals, source event); `chunks` (version, offsets, text); `embeddings` (chunk, model/preprocessing revision, vector); `links` (explicit relations/derivations); `jobs` (transactional outbox, deduplication, retries).

`valid_at` means when an assertion applies; `known_at` means what HLMemo had recorded by that time. Backdated corrections preserve both histories.

**Stack:** Python 3.12, [official MCP SDK 2/MCPServer](https://github.com/modelcontextprotocol/python-sdk), psycopg/Alembic, Postgres 17 + pgvector. Compose runs API, worker, and database locally; no VPS deployment. Expose Streamable HTTP with scoped bearer credentials; require TLS beyond localhost.

Use CPU [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small): 384 dimensions, pinned revision, normalized vectors, required prefixes, and 400-E5-token chunks. Combine SQL lexical search and exact vector search with deterministic rank fusion. Writes become lexically visible immediately; embedding status remains explicit.

**Required gates:** a frozen 100-query Turkish/English/identifier fixture over 10,000 chunks achieves mean Recall@5 ≥0.90 after indexing; valid budgets never overflow; unauthorized project reads always fail. Duplicate requests, concurrent revisions, crash/restart, replay, backdated corrections, and backup/restore preserve acknowledged events and expected temporal answers. All three pinned CLIs complete write→query→drilldown→raw against Compose.

Separately, require warm p95 ≤500 ms with three concurrent callers on recorded hardware, including query embedding. These are proposed gates, not completed tests.

Week 1: durability and all three client integrations. Week 2: retrieval, failure tests, restore, and documentation.

**4. CLOUD-LIBRARIAN GUARDRAILS**

First, update the candidate: DeepSeek retired V4 Flash on September 10; `deepseek-v4-flash` now routes to V4.1 Flash. “0731” is not a stable deployment assumption. Evaluate the actually served model and gate provider/model changes with regression fixtures. [Official changelog](https://api-docs.deepseek.com/updates/).

Use stateless, bounded jobs behind a replaceable provider adapter. Separate trusted policy from evidence; require schema-validated proposals citing source versions. Give the model no execution credentials. Deterministic code validates references, permissions, and expected revisions before applying changes; unresolved contradictions remain unresolved.

Deduplicate by operation, input revisions, and prompt/schema/model versions. Persist accepted outputs and apply atomically; retries may duplicate API charges, never database effects.

Put stable instructions/schema first and variable evidence last. Measure cache hits; budget misses, outputs, and retries, with hard spending caps. Provider caching is best effort, not durable memory. [Caching documentation](https://api-docs.deepseek.com/guides/kv_cache/).

Enforce local egress allowlists, secret filtering, minimal excerpts, project-separated caches, and redacted logs. Verify provider retention/training terms before sending private content. Select models by cost per accepted, correct proposal—not cached-input price.

**5. THREE QUESTIONS**

1. Which real recall tasks define success, and will CLI wrappers guarantee memory invocation?
2. Which source categories may reach the cloud, and which projects may share lessons?
3. Who can approve supersession of trusted facts, and what retention/deletion policy applies to original evidence?