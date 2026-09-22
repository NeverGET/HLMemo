You are my senior co-architect (coworker) on a new project called HLMemo (Human-Like Memory): a self-hosted, unified long-term memory backend exposed as a single remote MCP server to CLI coding agents (claude-code, codex, antigravity-cli/agy). Today is 2026-09-22.

Read the deep-research report at docs/research/00-deep-research-report.md fully (it is in Turkish; answer in English). It proposes: Postgres+pgvector(+Apache AGE) core, Graphiti reuse for a bi-temporal graph (L1), RAPTOR-style summaries (L2), project card (L3), cross-project experience layer (L4), a token-budgeted clue-based MCP tool set (query/drilldown/raw/write/call_the_day/register_lesson/risk_check/ingest_document), a "librarian" LLM that does placement/contradiction/consolidation asynchronously, a scoring+decay+silent-drop consolidation protocol, and a phased roadmap (Phase 0 MVP with no LLM).

Constraints already decided by the owner:
- Librarian model will be a CLOUD model (not self-host for now). Candidate: DeepSeek V4 Flash 0731. Open to better price/performance.
- Hosting on a VPS (provider TBD; must not block future upgrades, on-prem model deploy, or model switching).
- Everything must first run and be validated locally in containers (docker compose) before any VPS deploy.
- Single user for now, multiple machines, three CLIs.

I want a rigorous, opinionated critique, not a summary. Return ONLY the following sections, max ~900 words total:

1. TOP-5 RISKS / FLAWS in the report's architecture as proposed (each: what, why it bites, concrete mitigation).
2. DISAGREEMENTS: where you would deviate from the report (e.g., Graphiti dependency vs. own bi-temporal schema in Postgres; Apache AGE vs. plain relational edges; FalkorDB; embedding model choice; event-log-first). Be concrete and say why.
3. PHASE-0 MVP SCOPE you'd actually ship in 2 weeks: exact tool list, data model (tables), tech stack (language/framework, embedding model, DB), and a deterministic validation gate (what tests prove "works as promised").
4. CLOUD-LIBRARIAN GUARDRAILS: given DeepSeek V4 Flash as librarian, what prompt/caching/idempotency/privacy design do you insist on.
5. THREE QUESTIONS you want the owner (Claude orchestrator + human) to answer before Phase 0 starts.
