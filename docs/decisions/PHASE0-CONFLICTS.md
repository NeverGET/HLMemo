# Phase-0 spec merge — conflict log (2026-09-22)

Sources: `docs/consults/03-claude-phase0-spec.md` vs `docs/consults/03-codex-phase0-design.md`. "Chosen" is what `PHASE0-SPEC.md` specifies. Rows marked **Y** are judgment calls the owner should confirm; N rows are resolved on stated justification and need no action.

| # | Topic | Claude choice | Codex choice | Chosen | Why (1 line) | Needs orchestrator decision? |
|---|---|---|---|---|---|---|
| 1 | Primary key type | `bigint` identity everywhere; `request_id` text | `uuid` everywhere, composite `(project_id, x)` PKs | bigint; `request_id`/`session_id` uuid | Clue ids ride in every response; `v1234.2` costs ~4 tokens vs ~25 for a UUID (D-013 token-cost consequence). | N |
| 2 | Open interval representation | `NULL` for open `valid_to`/`superseded_at` | `'infinity'` + `EXCLUDE USING gist` non-overlap constraint | `'infinity'` + EXCLUDE (JSON still shows `null`) | The exclusion constraint makes "one current version per (valid_at, known_at)" a DB invariant, which G6 backdated-correction tests need. | N |
| 3 | Lexical index | `tsvector('simple')` GIN with prefix tsqueries + secondary `pg_trgm` GIN | `pg_trgm` GIN only, `word_similarity` top-100 | Claude's dual index; Codex's `<%` operator + threshold 0.1 for the trigram list | Prefix matching recovers TR/DE morphology cheaply; trigram alone ranks poorly on natural-language queries. | N |
| 4 | Budget tokenizer | `o200k_base` | `cl100k_base` | `o200k_base` | Current-generation encoding; closer to the pinned client models' real cost. | N |
| 5 | Budget range | 256..32000 | 0..8000, min 128 | 256..32000 | G2 samples up to 8000 but drilldown/raw legitimately need more; 256 keeps a card + one hit feasible. | N |
| 6 | What the meter counts | Canonical compact JSON of the result, once | Complete serialized MCP result incl. `structuredContent` + `TextContent` duplicate | Canonical JSON once | Counting the transport duplicate halves the effective budget for every client; but the gate text says "serialized response tokens" and could be read either way. | **Y** |
| 7 | Write granularity | Batch `items[1..50]` with intra-batch `$index` link targets | One `change` per write | Batch | `call_the_day` must commit note + lessons + card in one transaction via the same service (D-013). | N |
| 8 | Optimistic concurrency | `expected_version_id` | `expected_revision` integer (0 = create) | `expected_version_id` | One id type fewer; the conflict error can return `current_version_id` directly. | N |
| 9 | Item kinds | 7 kinds (`fact, episode, lesson, experience, project_card, session_note, doc_chunk`) | 4 kinds mapped to layers 1–4 | Claude's 7 kinds, no `layer` column | D-012 needs `stability`/`lesson` distinctions; layer is derivable from kind if ever needed. | N |
| 10 | RRF score shaping | 3 lists (L,T,V), weights 1.0/0.8/1.0, kind multipliers 1.2/1.1 | 2 lists, equal weights, layer preference only as tie-break | Claude's weights + multipliers; specificity tie-break added (forced by D-023) | Multipliers favour lessons/experiences at query time, but they also can distort G3 recall on the synthetic set; untuned. | **Y** |
| 11 | Candidate list sizes | L≤50, T≤20, V≤50 | lexical 100, vector 100 | L=100, T=20, V=100 | Exact scan at 10k rows makes 100 free; deeper lists improve fusion recall. | N |
| 12 | Query pagination | `omitted:int`, no cursor | Signed cursor over a 10-min cached ranking | `omitted`, no cursor on query (cursors on drilldown/raw only) | Stateless and deterministic; cached rankings break G1 restart semantics and add a cache to operate. | N |
| 13 | Token budget on writes | Not a write parameter | Required on every tool; reject before mutation if ack cannot fit | Optional on write/close (default 2000); ack size pre-checked before mutation | Keeps G2's "no partial effect" guarantee without burdening clients. | N |
| 14 | `memory.raw` addressing | `version_id` + chunk cursor | `event_id` + Unicode char offset | `version_id` + cursor, source event embedded | Callers hold clues (versions), not event ids; the event is returned inside. | N |
| 15 | Chunk overlap | 40 tokens | 32 tokens | 40 | 10 % of a 400-token chunk is the common default; no measured difference. | N |
| 16 | Preview length | 48 tokens, top-3 extended to 120 if room | 64 tokens flat | Adaptive 48/120 | More hits fit small budgets; large budgets still get richer previews. | N |
| 17 | Embedding column | `vector(384)` fixed | untyped `vector`, expression-cast partial HNSW | untyped `vector` + `dims` column, cast in `0002_hnsw` | D-017 makes the embedding model configuration; a dimension change must be a re-embed job, not a DDL migration. Cost: no plain ANN index later. | **Y** |
| 18 | Preflight failure exit code | exit 3 | exit 69 | 69 (`EX_UNAVAILABLE`), 77 for pending device | sysexits.h semantics are scriptable and self-documenting. | N |
| 19 | Preflight query text | `--task` else `git branch: last 3 commit subjects` | `--prompt TEXT` required | Claude's, with `"session start"` fallback | Zero-argument `hlm claude` must still preflight (D-014, D-021 dogfooding). | N |
| 20 | Outbox locking | `locked_by/locked_at`, FOR UPDATE SKIP LOCKED | `lease_token/lease_until`, fenced completion | Lease + fence (Codex), SKIP LOCKED pick (both) | Leases recover from a crashed worker without manual reset; fencing prevents double completion. | N |
| 21 | Cut order | 1 drop agy; 2 drop async worker | 1 drop auto MCP registration; 2 drop interactive adapters | 1 MCP auto-registration, 2 agy, 3 interactive adapters, 4 async worker | Cheapest-to-restore first; worker last because it changes write latency. Ordering is a scheduling judgment. | **Y** |
| 22 | Fixture query split | 34 TR / 33 DE / 33 EN, 25 identifier-heavy | 30/30/30/10 identifier | 30/30/30/10 with Claude's morphological-variant generation | Matches G3 wording literally ("TR/EN/DE/identifier"). | N |
| 23 | Migration tooling | Alembic `0001_phase0.py` | Raw `migrations/0001.sql` | Alembic (SQL executed verbatim) | Task requires Alembic names; raw SQL kept inside the revision for readability. | N |
| 24 | Compose/base image tags | `pgvector:0.8.1-pg17`, `python:3.12.7` (ASSUMPTION) | `pgvector:0.8.2-pg17`, `python:3.12.10` | `pgvector:0.8.3-pg17`, `python:3.12.10` (ASSUMPTION) | pgvector 0.8.3 observed in the local `pgvector/pgvector:pg17` image during DDL validation; python tag unverified until first build. | N |
| 25 | Auth model (both sources) | One bearer per project (`projects.token_sha256`) | Bearer per project | Device token + `device_project_grants` matrix — **forced by D-023** | Both sources predate D-023; per-project tokens survive only as a device whose grant list has one project. | N |


## Orchestrator rulings (2026-09-22, see D-024)
- Row 6: canonical JSON once; single wire representation.
- Row 10: OVERRIDE merge choice → plain RRF k=60, equal weights, no kind multipliers in Phase 0.
- Row 17: accept untyped vector + dims.
- Row 21: accept merged cut order.
