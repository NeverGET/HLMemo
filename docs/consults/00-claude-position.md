# Claude (orchestrator) — independent position on the deep-research report
Date: 2026-09-22. Written BEFORE reading codex's critique, to avoid anchoring.

## Where I agree with the report
- Dual-path design (fast deterministic path, no LLM; slow librarian path, async) is right.
- Token-budgeted, clue-id progressive disclosure is the core product idea. Keep it.
- Phase 0 with zero LLM is correct: value must exist before the librarian exists.
- Append-only event log from day one (cheap, unlocks multi-machine merge later).
- Conservative silent-drop with tombstones; experience/stable classes immune.
- Do not depend on CLI hooks; risk_check is an explicit tool.

## Where I disagree / see flaws
1. **Graphiti + Postgres/AGE is internally inconsistent.** Graphiti's backends are Neo4j / FalkorDB / Kuzu(archived) / Neptune, not Postgres+AGE. Choosing Graphiti forces a second database. Graphiti also needs an LLM on every episode ingest (entity/edge extraction), which conflicts with "Phase 0 has no LLM".
   → Proposal: own bi-temporal schema in Postgres (memory_item + edge tables, 4 timestamps each). Borrow Graphiti's *ideas* (edge invalidation, contradiction prompts), not its runtime. Revisit Graphiti in Phase 2 only if our own graph proves insufficient.
2. **Apache AGE is unnecessary at this scale.** Single user, 10-20 MB memory, graph of thousands of edges. A plain `edges` table + recursive CTEs covers 2-3 hop traversal. AGE lags Postgres major versions and adds an extension to operate. Skip in Phase 0-1.
3. **Embedding model gap: content is multilingual (TR/DE/EN).** Report defaults to text-embedding-3-small / bge-small. Need a multilingual embedding (bge-m3 or multilingual-e5-small/base, ONNX on CPU). Must be decided in Phase 0 because re-embedding later is a migration.
4. **Cloud librarian + privacy is under-specified.** DeepSeek API is China-hosted; retention terms matter for a Germany-based user. Options: (a) PII/secret scrubbing gate before every librarian call, (b) route DeepSeek via an EU/zero-retention inference host (changes price), (c) tiered policy: sensitive projects → no cloud librarian. This is a user decision.
5. **Token budget enforcement needs a real tokenizer**, not character heuristics. Use tiktoken (cl100k/o200k) as the budget meter; document that budgets are approximate for non-OpenAI models.
6. **"~200 ms fast path" is unproven.** Needs a measured gate: p95 latency for query with budget 3k on 10k items, local docker.
7. **Scoring formula weights are unspecified.** Phase 0 should log recency/usage/importance raw signals; weights tuned in Phase 3 with regression tests.

## My Phase-0 proposal (2 weeks)
- Stack: Python 3.12, FastMCP (streamable HTTP), Postgres 16 + pgvector, ONNX multilingual embedding on CPU, tiktoken, docker compose, pytest. Bearer token auth.
- Tools: memory.query, memory.drilldown, memory.write, memory.call_the_day, memory.health. (raw/register_lesson/risk_check in Phase 1-2.)
- Tables: projects, memory_items (bi-temporal), edges (bi-temporal), events (append-only), clues (id → item mapping), sessions.
- Validation gate (deterministic, in CI):
  G1 docker compose up → health OK within 60s.
  G2 seed 300 synthetic items across 3 projects → query(budget=3000) returns ≤3000 tokens (tiktoken-measured) and ≥1 clue id; drilldown on clue returns next layer.
  G3 synthetic QA set (60 questions) → Recall@5 ≥ 0.85.
  G4 p95 query latency ≤ 300 ms on 10k items, local.
  G5 all three CLIs register the server and list tools (claude mcp add / codex mcp add / agy mcp_config.json), smoke-call memory.health.
  G6 project isolation: query for project A never returns project B items (100 randomized trials).

## Open questions for the owner
- Privacy tier for the cloud librarian (see flaw 4).
- Language: Python vs TypeScript for the MCP server (I lean Python for the ML/embedding ecosystem).
- Multilingual content ratio (affects embedding choice).
