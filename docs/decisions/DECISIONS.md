# HLMemo — Decision Log (append-only)

Format: `D-NNN | date | status | decision | rationale | consequences`

D-001 | 2026-09-22 | ACCEPTED | Deep-research report is the design baseline; deviations are logged here. | Owner request. | Every architectural change references a D-number.
D-002 | 2026-09-22 | ACCEPTED | codex/gpt-6-astra is a standing co-architect; every non-trivial design step gets a written consult in docs/consults/. | Owner request; cross-model review catches blind spots. | Adds ~5-15 min per design step.
D-003 | 2026-09-22 | ACCEPTED | Librarian LLM is a cloud model (not self-host) for now. Candidate: DeepSeek V4 Flash 0731; open to better price/perf. | Owner constraint. | Privacy tier policy required (see open questions).
D-004 | 2026-09-22 | ACCEPTED | Full local docker-compose validation with a deterministic gate precedes any VPS deployment. | Owner constraint. | Gate defined in docs/decisions/VALIDATION-GATES.md.
D-005 | 2026-09-22 | PROPOSED | VPS: Hetzner Cloud CX43 (8 vCPU/16 GB/160 GB NVMe, FSN/NBG), fallback OVHcloud. ≈€24–32/mo incl VAT with backups (+object storage). | Lowest EUR/GB-RAM in DE after 2026 hikes; in-place rescale, Terraform, native dedicated (AX/EX) and GPU (GEX44) paths; egress 20 TB incl. See docs/research/02-vps-analysis.md. | Owner confirmation required. Avoid CPX/CCX lines. Deploy only after local gate passes (D-004).
D-006 | 2026-09-22 | ACCEPTED | Own bi-temporal schema in Postgres; NO Graphiti runtime in Phase 0-1. Borrow its concepts (edge invalidation, contradiction handling). | Graphiti backends exclude Postgres/AGE (would force a 2nd DB) and need an LLM per ingest, conflicting with LLM-free Phase 0. Claude + codex converged (consults/00, /01). | Revisit only if own graph proves insufficient (Phase 2+).
D-007 | 2026-09-22 | ACCEPTED | Plain relational `links`/edges table + recursive CTEs; no Apache AGE, no FalkorDB. | Single-user graph of thousands of edges does not justify a graph extension; AGE lags PG majors. Converged. | Re-evaluate at >1M edges or >3-hop query needs.
D-008 | 2026-09-22 | ACCEPTED | Embedding: intfloat/multilingual-e5-small (384-d, pinned revision, ONNX CPU, required query:/passage: prefixes, ~400-token chunks). Hybrid retrieval = SQL lexical + exact vector + deterministic rank fusion. | Content is TR/DE/EN; small model fits CPU VPS; embedding model/revision stored per row so re-embedding is a migration, not a rewrite. Converged. | Gate: Recall@5 ≥ 0.90 on frozen 100-query fixture (see VALIDATION-GATES.md).
D-009 | 2026-09-22 | ACCEPTED | DeepSeek V4 Flash 0731 is NOT a viable librarian: retired on first-party API 2026-09-10 (routes to V4.1 Flash) and DeepSeek first-party API fails EU privacy (PRC storage, training use, no DPA). Librarian shortlist: qwen3.8-flash (Alibaba Frankfurt), gpt-5.6-luna (EU residency), mistral-small-2603 (api.eu.mistral.ai). Final pick by empirical bench (bench/). | docs/research/01-librarian-model-analysis.md; independently confirmed by codex. Cost spread ($2–8/mo) is noise vs JSON reliability + privacy. | Owner to confirm privacy tier. Provider adapter must be swappable (OpenAI-compatible).
D-010 | 2026-09-22 | ACCEPTED | Events table is authoritative from day one (Phase 0), with idempotent request keys; L1–L4 are rebuildable projections with provenance. | Report deferred this to Phase 4; both reviewers judged concurrency correctness cannot be retrofitted. | Adds `events`, `jobs` (outbox) tables to Phase 0 schema.
D-011 | 2026-09-22 | ACCEPTED | Public GitHub repo github.com/NeverGET/HLMemo (owner's personal account). Secret gate: .gitignore + .githooks/pre-commit (gitleaks + pattern grep); `.env` never tracked. | Owner request. | Every contributor must `git config core.hooksPath .githooks`.
D-012 | 2026-09-22 | ACCEPTED | Forgetting = decay affects ranking; reversible ARCHIVE (never physical delete of evidence) when retention r=2^(-idle_days/14) < 0.1 (~47 idle days) for N=3 consecutive daily cycles, only volatile ∧ unpinned ∧ no live dependents. Archive event recorded; `query(include_archived=true)` + restore supported; stable facts and lessons exempt. | Converged in consults/02 (codex conceded reversible archival; Claude conceded no physical delete). | Ship in SHADOW mode first; regression: replay frozen TR/DE/EN histories, require no Recall@5 drop and no lost gold-evidence retrievals. Phase 3.
D-013 | 2026-09-22 | ACCEPTED | Phase-0 MCP surface = 5 tools: memory.query, memory.drilldown, memory.raw, memory.write, memory.call_the_day. Health on HTTP /health, not a tool. 7 tables: projects, events, memory_versions, chunks, embeddings, links, jobs. | codex conceded the discoverability value of a distinct close tool; call_the_day delegates to the same idempotent write service (no hidden LLM). | Measure actual schema token cost per session.
D-014 | 2026-09-22 | ACCEPTED | Proactive recall contract: an `hlm` wrapper (`hlm claude|codex|agy ...`) executes memory.query preflight, injects the bounded result as the first prompt, then launches the CLI. Instruction files (CLAUDE.md/AGENTS.md/GEMINI.md) are belt-and-braces; hooks optional. Server returns "no matching evidence", never "no risk". | Ranking from consults/02: injected prompt > instruction file > tool description; none guarantees a model-issued call, so the wrapper makes it deterministic. | Gate: 300 trials per pinned CLI, query-before-first-action rate; wrapper blocks on query failure.
D-015 | 2026-09-22 | ACCEPTED | L3 project card exists from day one: deterministic skeleton at project creation, populated by the CLIENT LLM via memory.write(kind=project_card) during onboarding and at call_the_day. Cap 512 pinned-tokenizer tokens; references immutable source versions; marked stale when dependencies change. | Phase 0 has no backend LLM; the client model is the only author available. Converged. | —
D-016 | 2026-09-22 | ACCEPTED (owner) | Librarian runs via OpenRouter in production. Privacy tier for now: cloud models are acceptable for all projects; no per-project opt-out in Phase 0-2. Stricter privacy (self-hosted model) is deferred until a local model deployment becomes necessary. | Owner decision 2026-09-22: early-stage risk judged low; codex's F-point (consults/02) acknowledged and consciously deferred. | Model choice is purely capability + cost (bench/); DeepSeek variants are back in the candidate set. Cheap mitigations still applied: OpenRouter `provider.data_collection="deny"` where it does not break routing, secret/PII filtering before every librarian call, redacted logs. Provider adapter stays OpenAI-compatible so a local vLLM endpoint is a config change.
D-017 | 2026-09-22 | ACCEPTED (owner) | HLMemo is provider-agnostic and user-configurable. The librarian model (base URL, model id, API key, reasoning/JSON options), the embedding model, the database DSN and the hosting target are all configuration, never code. Any OpenAI-compatible endpoint works (OpenRouter, a direct vendor API, or a local vLLM/Ollama). Our bench result becomes the shipped DEFAULT + a documented recommendation; `bench/` ships as a user tool ("pick your own librarian with your own key"). | Owner requirement: anyone adopting the public repo must be able to choose their own model and configure HLMemo freely. | Config surface: `.env` / `hlm.toml` with `HLM_LLM_BASE_URL`, `HLM_LLM_MODEL`, `HLM_LLM_API_KEY`, `HLM_LLM_REASONING`, `HLM_EMBED_MODEL`, `HLM_DB_DSN`, plus documented profiles (openrouter, openai, mistral-eu, alibaba-eu, local-vllm). Embedding model + revision recorded per row so switching triggers a re-embed job, not data loss. Any prompt that depends on a model quirk must be gated behind a provider profile, not hard-coded.
D-018 | 2026-09-22 | ACCEPTED (owner) | Two tracks run in parallel: (1) PUBLIC product track — generic, provider-agnostic HLMemo in github.com/NeverGET/HLMemo; (2) PRIVATE ops track — the owner's own deployment (chosen librarian model, VPS, keys, project ids, backups) goes live for daily use as soon as Phase 0 passes the local gates. | Owner decision 2026-09-22. | Private config never enters the public repo: it lives in a separate PRIVATE repo `NeverGET/hlmemo-ops` (to be created when the first deploy artifact exists) holding `hlm.toml`, compose overrides, Terraform vars and runbooks; secrets stay in `.env` there too. The public repo ships `hlm.example.toml` + profiles. Every feature must work from the public repo alone (no hidden dependency on the private one). Dogfooding findings feed back as public BUG:/LESSON: entries with private details stripped.
D-019 | 2026-09-22 | ACCEPTED | Default librarian (our deployment + shipped default profile): `deepseek/deepseek-v4.1-flash` via OpenRouter with `reasoning:{enabled:false}`, `response_format=json_object`, temp 0. Fallback profile: `openai/gpt-5.6-luna` (different provider = outage hedge). | Empirical bench bench/results/20260922-122010-final.md (11 models × 33 tasks × 2 runs + reasoning-off variant, $0.25 total): v4.1-flash reasoning-off scored 100/100/100/100 with 0/99 JSON failures across 3 runs, 1% variance, ~$6/mo cached; Luna 100% everywhere, 0% variance, 1.7 s. Eliminated: deepseek-v4-flash 0423 (9% corrupted-key JSON), 0731 (5–12% schema-echo wrapper), glm-5.3-flash (reasoning cannot be disabled), qwen3.8-flash (17K hidden reasoning tokens, 8.6 s), gemini-3.1-flash-lite (perfect but pricier), gpt-5.4-nano (T1 65%), minimax-m3 (prose preamble), qwen3.5-flash (100% non-object), mistral-small-4 (unmeasurable: upstream 429s). | Librarian adapter MUST: validate every response against a JSON schema and retry once on failure; treat HTTP 429/5xx as retryable with exponential backoff (1/2/4/8 s ×5); record model id + prompt/schema version per job so a provider change is regression-gated by re-running bench/. Reasoning-off deepseek numbers are single-run; re-bench monthly.
D-020 | 2026-09-22 | ACCEPTED (owner) | HLMemo is the migration TARGET, not a NotebookLM client. This project is NOT migrated to NotebookLM-first memory (the global CLAUDE.md protocol does not apply here). Once HLMemo runs, the owner's existing memories are migrated INTO it, project by project over time: NotebookLM notebooks (notes + sources), serena MCP memories (many projects were built with serena), Claude auto-memory dirs, and CLAUDE.md/AGENTS.md context files. The owner expects this legacy-import subsystem to be the hardest part of the system. | Owner decision 2026-09-22. | New subsystem "hlm import" (Phase 1.5, before the librarian): importers per source (serena markdown dirs, nlm export/API, auto-memory markdown, CLAUDE.md), provenance = source system + path + original timestamps (bi-temporal recorded_at = original mtime where known), idempotent re-import, dry-run + diff report, per-project mapping table, and a quality gate (sampled recall against the source notebook's own answers). Ingest pipeline must accept markdown-with-frontmatter natively. Inventory of the legacy estate is measured first (docs/research/03-legacy-memory-inventory.md).
D-021 | 2026-09-22 | ACCEPTED (owner) | Self-hosting ("git hosts git"): once HLMemo passes the Phase-0 gates, project `hlmemo` becomes the first project INSIDE HLMemo. This repo's docs/decisions, docs/consults, docs/status and the Claude auto-memory for this project are imported (D-020 importer) and from then on session memory for building HLMemo lives in HLMemo itself. | Owner decision 2026-09-22; the strongest possible dogfooding signal. | Bootstrap rule: HLMemo must never depend on a running HLMemo to build, migrate or start (docs/ on disk remain the cold-start fallback and are exported back from HLMemo periodically). The `hlm claude|codex` wrapper is used for every HLMemo dev session from that point; wrapper friction found here is a P1 bug. Gate G7 gains a "self-project" smoke: write→query→drilldown on project `hlmemo`.
D-022 | 2026-09-22 | ACCEPTED (owner) | Legacy migration is a per-project RECONSTRUCTION campaign, not a file import. For each project, in order: (1) inventory memories + the live codebase; (2) diff memory claims against code truth (memory says X, repo says Y); (3) fill gaps (missing architecture/structure knowledge derived from the code); (4) drop details that lost relevance; (5) extract lessons; (6) validate experiences with evidence (links to files/commits/tests); (7) map every memory to the files/structures it describes; (8) import with full provenance. Target: the project ends up with a memory as if it had been built with HLMemo from day one. Applies to the work computer's estate too (≈ same size again → plan for ~2× the inventory, and far more work than the byte count suggests). | Owner decision 2026-09-22. | This needs the librarian LLM + code-exploring agents, so it lands AFTER Phase 2 (librarian), as "Phase 2.5 — Reconstruction". Phase 1.5 keeps only the plumbing: raw importers (serena/auto-memory/context files/codex SQLite/NotebookLM) with provenance + `describes` links to code paths. Per-project acceptance gate: a frozen Q&A set about that codebase (architecture, gotchas, decisions) answered correctly via memory.query at Recall@5 ≥ 0.9, plus a "stale claim" test (no memory contradicts current code on the sampled facts). Codebase reads must be done via the wrapper on the machine where the repo lives; the work computer runs the same protocol against the same HLMemo server.
D-023 | 2026-09-22 | ACCEPTED (owner) | Device identity is first-class. Every access is attributed to a registered DEVICE even when authenticated: `devices` table (device_id, user_id, name, class ∈ {personal, work, server, ci, other}, fingerprint, os, registered_at, status ∈ {pending, trusted, revoked}, last_seen_at). Authorization is a DEVICE × PROJECT matrix (owner clarification: both, not either): a bearer token identifies a device; a `device_project_grants(device_id, project_id, role ∈ {read, write, admin}, granted_at, granted_by_device_id)` table says which projects that device may touch and how. Per-project tokens also exist for headless/CI use (device class `ci`/`server`), so a project-scoped token is simply a device token whose grant list has one project. Every event carries `device_id`. Memory items and links are scoped on BOTH axes: `project_ids[]` (existing, cross-project lessons allowed via links) AND `device_scope` (all | class:<name> | device:<id>) so device-specific rules (e.g. "on the work machine never push to X", "on the personal machine use Y") apply only where relevant; memory.query answers are shaped by the calling device's class (small but important differences). New device onboarding: `hlm device register` creates a pending device; it becomes `trusted` only after approval from an already-trusted device (or the admin token) and gets a class + optional notes ("positioning" the device in the system). Pending devices can only call /health. | Owner requirement 2026-09-22: personal vs work machine must be distinguished and memory must behave accordingly; new devices must be positioned deliberately. | Phase 0 schema gains `devices` (8 tables total); `events.actor` is split into `device_id` + `client` (cli name/version); tokens table or hashed token on devices. Query gets `device` context server-side from the token (never from the client payload). G5 isolation gate extends to device scope. Wrapper stores the device token in the OS keychain where available, else `~/.config/hlm/` with 0600.
D-024 | 2026-09-22 | ACCEPTED | Orchestrator rulings on PHASE0-CONFLICTS.md rows marked Y: (6) Budget meter counts the canonical compact JSON once; the server MUST emit a single representation on the wire (text content carrying that JSON, no duplicate structuredContent) so what the client model sees equals what was metered. (10) Phase-0 ranking = plain RRF, k=60, three lists (lexical, trigram-if-identifiers, vector), all weights 1.0, NO kind multipliers; kind/recency/usage are logged as signals only and tuned in Phase 3 against G3 with a regression test. (17) `embeddings.vec` is untyped `vector` + `dims` + `model@revision` per row (D-017: model change = re-embed job, never DDL); ANN index later is a per-model partial expression index (`0002_hnsw`). (21) Cut order if the 2-week budget blows: 1 MCP auto-registration, 2 agy adapter, 3 interactive launch adapters, 4 async worker (sync embed in the write tx). | Merge agent validated the DDL in a live pgvector/pg17 container; the four Y rows are judgment calls, decided for simplicity and gate integrity. | PHASE0-SPEC.md is now the implementation contract; codex round 4 reviews it once, then coding starts.
D-025 | 2026-09-22 | ACCEPTED | Phase-0 implementation GO after codex round 6 (docs/consults/06-codex-go2.md): all round-4/5 blockers resolved, no new contradictions. PHASE0-SPEC.md as of commit HEAD is the frozen contract; changes to it during Phase 0 require a DECISIONS entry. Implementation order: (1) scaffold + compose + migration 0001 + PG test fixtures; (2) device auth + admin binding + shared authz; (3) initial-create write path (events/versions/chunks/links/outbox, device-scoped idempotency, replay re-authz); then retrieval, MCP server, wrapper, gates. First test: `test_device1_restart_without_env_disables_admin`. | Six review rounds with an independent co-architect converged; DDL validated live twice. | Agents work on disjoint paths, no commits by agents; orchestrator commits after gate checks.
D-026 | 2026-09-22 | ACCEPTED | Phase-0 implementation deviations from PHASE0-SPEC (all measured, none change contracts): (1) Alembic: `0002_hnsw` is a separate branch (`hnsw@head`); everything runs `alembic upgrade phase0@head` because plain `head` is ambiguous. (2) pg_trgm `word_similarity_threshold` 0.9 not 0.1: at 0.1 the GIN recheck scanned every chunk (3–7 s/term); recall identical at 0.7–0.9. (3) Candidate queries use `prepare=False`: psycopg server-side prepare after 5 executions led Postgres to a generic plan that dropped the GIN index (p50 2 s → 250 ms). (4) tsquery built from `simple`-parser lexemes via `::tsquery` cast, not `to_tsquery`, so `svc-xg6` stays one prefix term. (5) MCP uses the low-level `mcp.server.lowlevel.Server` (schemas verbatim, errors unwrapped) rather than `MCPServer` decorators. (6) Wrapper exit codes follow spec §5 sysexits (69 unavailable, 77 no token, 64 bad args), MCP server name `hlm`, codex env var `HLM_DEVICE_TOKEN`. (7) pool.py `configure_connection` now commits (bug: INTRANS connections were discarded by psycopg_pool). | Reported by implementation agents and independently re-run by the orchestrator. | Spec text to be synced in the Phase-0 closeout commit. TODO Phase 1: `memory.raw` should page via cursor instead of E_BUDGET_TOO_SMALL when payload_item exceeds budget (58/1000 random budgets in G2 hit this).
D-027 | 2026-09-22 | ACCEPTED | Codex Phase-0 code review (docs/consults/07-codex-code-review.md) = NO-GO for "Phase 0 done" until fixed. Backlog, in order: (S1) raw survivor provenance leaks restricted corrections [read_service.py:392] + payload_item.links unfiltered [:453]; (C1) middleware sends response before outer commit [middleware.py:94] → buffer + commit before ack; (C2) replay inserts links before later items' versions [replay.py:162] → two-pass; (C3) spanning link corrections keep one segment [write_service.py:581]; (S2) link resolution ignores target device_scope → hidden-target oracle [write_service.py:425]; (S3) preflight delimiter spoofing [preflight.py:77] → escape + instruction-before-data; (S4) compose publishes Postgres on all interfaces with hlm/hlm → bind 127.0.0.1; (C4) copied chunk scope can hide authorized versions [read_queries.py:156]; (C5) idempotency hash over normalized args, not verbatim [write_service.py:192]; (C6) worker copied vectors commit before lease fence [worker/main.py:217]; (O1) models not baked / readiness ignores deps; (O2) worker restart policy; (O3) test image lacks dev deps. | Independent reviewer found defects tests could not; each has file:line. | Each fix ships with a regression test; codex round 8 re-reviews; only then D-021 self-hosting starts.
D-028 | 2026-09-22 | ACCEPTED | D-027 backlog closed: 12 of 13 items VERIFIED with named regression tests, O2 (worker crash/restart + progress monitoring) remains WEAK (`unless-stopped` set, no crash test). Implementation was finished and verified by codex/gpt-6-astra acting as implementer (docs/consults/08-codex-finish.md) after the three Claude fix agents were cut off by a model rate limit mid-task; orchestrator re-ran every gate independently. Measured after the fixes: ruff clean, 199 unit/fixture + 83 integration + 8 gate tests = 290 green; G3 Recall@5 0.930 (unchanged); G4 p95 262.8 ms (improved from 371 ms). | Notable: the C4 fix first introduced a p95 regression to 5279 ms (query lost the GIN index); codex caught and fixed it in read_queries.py:239 before reporting. Models are now baked into the image (Dockerfile:27) and the host mount that shadowed them was removed (compose.yaml:17); readiness verifies model hashes + ONNX inference + tokenizer offline. | Open: O2 crash/restart test; S4 verified in rendered compose config but the already-running dev db container still publishes on all interfaces until recreated; G7 not re-run after these changes.
D-029 | 2026-09-22 | ACCEPTED | Codex round-9 adversarial self-review (docs/consults/09-codex-final-review.md) audited its own round-8 fixes: S1, S1b, C4, C2, C3, S2, C6 CORRECT; C1 and C5 INCOMPLETE; 3 new defects; NO-GO for "Phase 0 done". Remaining work: (N1) middleware buffers indefinite SSE responses so headers/pings never flush [middleware.py:118] — buffer finite responses only, pass streaming through after committing; (N2) hashing change breaks idempotent retries of events written before the upgrade [write_service.py:567] — version the hash algorithm and accept the recorded version on replay; (N3) readiness cache is unlocked, concurrent cold `/ready` calls can produce a false 503 and duplicate model loads [app.py:142]; (O2) implement the crash/restart test codex specified (`test_worker_sigkill_after_vector_inserts_restarts_and_reclaims`) plus a ≤10 s worker heartbeat exposing last committed job/time, ready-job count and oldest-ready age; (S4b) recreate the running dev db container so the 127.0.0.1 bind actually applies; (G7b) re-run G7 with all three real CLIs after the above. Confirmed non-issue: the C4 repair is a MATERIALIZED boundary, not a new limit, so it drops no hits. | The reviewer was told to attack its own work; it found what the 290 green tests did not. | Phase 0 is declared DONE only after N1-N3, O2, S4b and G7b.
D-030 | 2026-09-22 | ACCEPTED | D-029 fully closed. N1/N2/N3 fixed by codex round 10 (commit 09e1947) and re-verified by the orchestrator. O2 now has a real crash test: `test_worker_sigkill_after_vector_inserts_restarts_and_reclaims` (isolated compose project, barrier inside the fenced transaction, container self-destruct, Docker RestartCount increments on its own, job completes with attempts=2, exactly one embedding per (chunk,model,revision,preproc), copied vector byte-identical, inferred vector within 1e-5) — orchestrator-run: 2 passed in 146 s with the heartbeat test. S4b verified on the running container: db publishes 127.0.0.1:5432 only, LAN connect refused, `hlm_retr` still holds 11,574 embeddings. G7b re-run green with the three real CLIs (claude 2.1.280, codex 0.155.1, agy 1.2.8 — both claude and agy self-updated; VERSIONS re-pinned), all four tools per CLI, user MCP configs byte-identical afterwards. | Two findings worth keeping: (1) `kill(1, SIGKILL)` inside a container is silently dropped (namespace init is SIGNAL_UNKILLABLE), so the crash test forces an unignorable fault instead; the barrier is env-gated and off by default. (2) The heartbeat fields codex specified report the crash window as *idle* (`ready_jobs=0` for the whole 120 s lease), so `in_flight_jobs` and `oldest_in_flight_age_s` were added — without them a stalled worker looks healthy. | Remaining before "Phase 0 DONE": codex round 11 final GO/NO-GO. Process note: an orchestrator `git add -A` swept ~20 lines of an agent's in-progress test into commit be237f1; all of it has since been verified by re-running the tests.
D-031 | 2026-09-22 | ACCEPTED (owner) | Model strategy re-evaluation: the session now runs on Claude Opus 5.5 (reported ahead of Fable 5.1). Run a multi-round bake-off Opus 5.5 vs codex gpt-6-astra under docs/bakeoff/PROTOCOL.md. If Opus 5.5 clearly wins → Astra returns to advisor/reviewer; if tie or Opus behind → keep current strategy (Astra implements). | Owner decision 2026-09-22. | Judge conflict of interest mitigated by deterministic gates, reproduction-based adjudication, blinded verification and committed raw data.
D-032 | 2026-09-22 | ACCEPTED (owner goal) | Standing goal: continue until HLMemo is deployed to a VPS and works end-to-end. Deploy track after Phase 0 DONE: (DP1) production deploy tooling in `deploy/` — compose.prod with Caddy automatic TLS in front of the API, DB never published, backups with restore drill, Terraform for Hetzner (server + firewall + cloud-init), deploy script, runbook; (DP2) private ops repo NeverGET/hlmemo-ops for real values (D-018); (DP3) provision the server (needs owner: Hetzner account/API token, spending approval, domain or sslip.io fallback); (DP4) deploy + remote gates: TLS health/ready, remote G5 isolation, remote G7 with the three CLIs from this Mac, WAN latency; (DP5) backup/restore drill on the server; (DP6) D-021 self-hosting against the remote server. | Owner goal statement 2026-09-22. | DP1 is built locally first and must pass its own gates on this Mac before any server exists (D-004).
D-033 | 2026-09-22 | ACCEPTED | Phase-0 final audit (bake-off R1) = NO-GO from both reviewers; 14 distinct defects confirmed by blinded reproduction (docs/bakeoff/r1/repro/, one failing test each). Pre-VPS fix backlog, highest first: F13 revision/expected_versions ignore device_scope → existence oracle + overwrite of another device's private item (High); F11+F14 (G-B) request/stream holds a pooled connection and the device-row FOR SHARE lock → revocation blocked, pool exhausted by slow bodies or idle SSE (High/Med); F16 card derived_from links never superseded → permanent staleness, unbounded link lists → raw E_BUDGET_TOO_SMALL (Med); F03 link survivor segments missing on corrections (Med); F05 register rate limiter keyed on proxy IP + unbounded dict (Med); F08 device_scope `device:02` accepted on write, unreadable by the writer (Med); F15 re-register after revoke fails on fingerprint uniqueness (Med); F02+F07 (G-A) survivor last_access_at breaks replay determinism (Low); F01 malformed clue → E_UNAVAILABLE retryable (Low); F04 HLM_EMBED_MODEL ignored (Low); F06 validation errors echo inputs (3 MB envelope) (Low); F09 worker logs the DSN password (Low); F12 oversized call_the_day → E_UNAVAILABLE (Low); F17 drilldown packing rejects budgets that fit (Low). Also: D-015 "skeleton card at project creation" was dropped without a decision (F10 note) → record or restore. | Blinded adjudication; raw reports and scoring in docs/bakeoff/r1/. | Each fix turns its repro test into a regression test under tests/. Phase 0 DONE only when all 17 repro tests pass.
D-034 | 2026-09-22 | ACCEPTED | Bake-off R2 winner (gpt-6-astra, −0.5 vs −9.0) merged to main as `deploy/` (Caddy TLS edge, prod compose with unpublished DB, backup/restore + systemd timer, Hetzner Terraform + cloud-init, deploy.sh, RUNBOOK). All judge gates re-run on main after merge: G-D1, G-D5 (terraform validate + fmt), G-D6 shellcheck, G-D7 gitleaks pass. Deploy backlog from adjudication: D11 retention deletes same-day pre-upgrade dumps the rollback depends on (Med); D13 deploy stops writers before the backup, so a backup failure (S3 upload / stale mkdir lock) leaves prod down (Med); D01 cloud-init `sshd -t`/`systemctl reload ssh` under set -e before directory creation on Ubuntu 24.04 socket-activated sshd (Med, needs a real VM to confirm); D09 ports bound to 0.0.0.0 only → no IPv6 listener although Terraform enables IPv6 (Low); D05 one env file injects backup S3 keys + admin token into every container (Low); D02 smoke_mcp.sh leaves a new project per run (Low). | docs/bakeoff/r2/RESULT.md. | Fix before first real deploy (DP4).
D-035 | 2026-09-22 | ACCEPTED | D-034 fixes (implemented by gpt-6-astra) verified by the neutral judge harness on main: G-D1..G-D7, neutral G-D4, D02 (smoke reuses one project), D11 (pre-upgrade dumps survive; 7 daily + 4 weekly over a 35-day simulation), D13 (failed S3 + stale lock → exit 0, stack untouched; held lock → fails before writers stop), D09 (80/443/tcp + 443/udp without host_ip), D05 (S3 keys in no container, admin/registration secrets only in api), D01 (cloud-init 26.1 schema valid in an ubuntu:24.04 container). Opus 5.5 adversarial review (docs/consults/12-opus-review-d034.md) then found issues the gates cannot see; next fix round: (1, High, pre-existing) deploy.sh pipes the remote script into `bash -s`, and `docker compose exec -T … pg_dump` / `run --rm migrate` inherit stdin and swallow the rest of the script — first deploy exits 0 with the app never started (reproduced: `printf 'echo a\ncat>/dev/null\necho b\n' | bash -s` prints only `a`); (2, Med) restore.sh `up --no-deps` skips migrate → current code on an older schema; (3, Med) an empty backup.env makes common.sh KeyError → all backups fail; (4, Med) a /ready failure caused outside the app (DNS/ACME/AAAA) triggers a destructive DB rollback that discards accepted writes; (5, Med) remote run is not detached — ssh drop → SIGPIPE mid-migration, no recovery, secret-bearing rollback file left behind; (6, Low) first upgrade from a pre-D-034 ref renders rollback config without POSTGRES_*/DSN; (7, Low) HLM_BACKUP_DIR default falls back into the git checkout; plus residuals: failed `sshd -t` leaves a bad drop-in; IPv6 clients appear as the docker gateway IP to the per-IP limiter. | Judge logs docs/bakeoff/r2/judge/logs/judge-main-*; review in consults/12. | Fix round D-035 goes to gpt-6-astra (implementer), then neutral gates + Opus review again.
D-036 | 2026-09-22 | ACCEPTED | Bake-off verdict after R1–R3 (docs/bakeoff/SCOREBOARD.md): Opus 5.5 is the stronger reviewer (R1 15 vs 10; more precise cross-reviews each round), gpt-6-astra the stronger implementer (R2 −0.5 vs −9.0, R3 −10 vs −12). Per the owner's rule (D-031) the current strategy stays: gpt-6-astra = implementer; Claude (Opus 5.5) = orchestrator + adversarial reviewer + neutral judge. Every Astra change gets (a) neutral gates run by a separate worker and (b) an Opus adversarial review before it counts as done. R3 winner bake/r3-astra merged (567a275, 362 tests green). Next source-code fix round (D-037) carries: R3 defects common to both branches — R06 High (advisory lock taken before authorization → LockNotAvailable vs E_NOT_FOUND oracle), R07 High (a device-scoped project card locks every other device out of card updates; fix: project_card must be device_scope=all), R01 Med (/ready takes a pooled connection → 503 under pool pressure), R05 Med (revoke can still fail under a request flood — reserve admin capacity), R08 Med (lock_timeout turns E_VERSION_CONFLICT into E_UNAVAILABLE under contention); Astra-branch-only R04 Low (use a minimum-rate/inactivity body timeout) and R10 Low (discard connections only for /mcp cancellation/timeout); good ideas from the Opus branch worth porting: server-side `transaction_timeout` on PG ≥17, a small body cap for unauthenticated /devices/register, retryable=true on 408; the MCP SDK 4 MiB transport cap vs spec §3 maxima (align the contract or raise the SDK limit); plus the remaining D-033 items F04 (API side), F16, F03, F05, F15, G-A (F02/F07). | Scores and raw data committed under docs/bakeoff/. | D-037 runs in its own worktree/branch and is merged only after neutral gates + Opus review.
D-037b | 2026-09-22 | ACCEPTED | D-035 deploy fix round (gpt-6-astra) verified by the neutral judge: G-D2..G-D7 + neutral G-D4 pass; the STDIN High is fixed and the judge's own fake-ssh/fake-docker harness (children drain stdin) is proven sensitive — previous deploy.sh exits 0 WITHOUT starting the stack, new one reaches "Deployment ready"; restore now migrates a behind-head dump on a real stack; empty/comment-only backup.env OK; external-only /ready failure does not roll back (remote-deploy.sh:173-175). Opus review (consults/15) confirms items 1,3–7 fixed, restore PARTIAL. Next round D-038: N1 failed deploy leaves `hlmemo:prod` retagged to the failed release → a later restore/up runs failed code (Med-High); N2 runner taken from the local tree instead of the deployed ref → deploying any pre-D-035 ref always fails, runner/tooling drift (Med); N3 observer polls forever if the runner dies without status, `command -v a b c` preflight never fires (Low-Med); N4 rollback runs the old image under the new compose model (Low); same-second daily backups overwrite each other (Low); ruff debt in tests/deploy (22 errors, 6 new); judge G-D1 harness needs the per-service env files (judge-side update). | consults/13, 15; judge logs judge-main-d035-*. | Fix before the first real deploy.
D-038 | 2026-09-22 | PROPOSED (owner decides) | VPS provider: owner proposed Hostinger KVM 4 on a 24-month term (4 vCPU / 16 GB / 299 GB, free weekly backups, API + MCP + official Terraform provider, ~552 TRY ≈ €9.88/mo as quoted). Orchestrator assessment: recommended for this workload — best price/performance of all options researched (Hetzner CX33 ≈ €12.70 for 8 GB, CX43 ≈ €19.60, Radore ≈ €31) and 16 GB covers the librarian worker + legacy import; the "no GPU/dedicated path" objection is weak because the librarian is an external OpenAI-compatible endpoint (D-017). Conditions: (1) renewal after 24 months ≈ 2.5× (research: ≈ €28/mo for KVM 4); (2) confirm whether 552 TRY includes KDV; (3) Hostinger has no object storage → off-site backups to an S3-compatible bucket elsewhere (Cloudflare R2 free tier or Backblaze B2); (4) KVKK answer still decisive — if work-computer memories must stay in Turkey, neither Hostinger nor Hetzner. | Owner message 2026-09-22; docs/research/02 and 04. | Plan change regardless of provider: replace the Hetzner-only Terraform/cloud-init entry path with a provider-agnostic `deploy/bootstrap.sh` run over SSH on a fresh Ubuntu 24.04 host (Docker, deploy user, firewall, fail2ban, unattended-upgrades, directories); keep Terraform/hetzner as optional. First action on the new host: run G4 there to measure shared-vCPU performance.
D-040 | 2026-09-22 | ACCEPTED (owner direction + measurement) | Embeddings move from the local ONNX e5-small to a cloud API (owner: embeddings are cheap as a service; local only when the LLM also goes local). Model: `google/gemini-embedding-2` via OpenRouter's OpenAI-compatible /embeddings, stored at 1536 dims (Matryoshka), WITH task prefixes (query: `task: search result | query: …`; memory: `title: <title or none> | text: …`). Fallback profile: `perplexity/pplx-embed-v1-4b` @1024 (open weights → future local GPU). Evidence (bench/embeddings/RESULTS.md, vector-only, same fixture, $3.56 total): e5-small R@5 0.80 / non-identifier 0.76 / Turkish 0.68; gemini-embedding-2+prefix 0.92 / 0.89 (McNemar p=0.01 vs e5) / 0.91, query embed 193 ms median; without prefix 0.85; gemini-001 0.93 but 1063 ms; pplx-4b 0.90 at 182 ms; OpenAI te3-small 0.78, voyage-4 0.79, qwen3-4b 0.56. | Consequences: (1) embedder becomes a provider interface in config (local-onnx | openai-compatible) with model, dims and prefix-template version recorded per embedding row (D-017/D-024: switching = re-embed job); (2) query path embeds concurrently with lexical/trigram SQL, has an LRU query-embedding cache, a hard timeout, and degrades to lexical+trigram when the API fails; (3) the F04 startup pin check becomes "configured embedder profile == profile of stored vectors, else refuse to serve vector search until a re-embed completes"; (4) the image no longer needs the 470 MB model (BAKE_MODELS=0 for the api profile) → smaller VPS (D-038 revisited: Hostinger KVM 2 or OVH VPS-2 instead of KVM 4). Gates: hybrid G3 must not drop below 0.930 (target > 0.93) on a copy of hlm_retr re-embedded with gemini-2; G4 p95 measured with the real API — if > 500 ms the owner decides between relaxing G4 and more mitigation. Memory content now leaves the host for embedding (same trade-off already accepted for the librarian, D-016). Implementation starts after the D-039 src round merges (file overlap).
D-041 | 2026-09-23 | ACCEPTED | Convergence rule for closing Phase 0 (each review round has found something in the previous fix; the owner asked to converge): Phase 0 is DONE when (a) all 17 R1 repro tests pass, (b) G1–G8 are green, (c) the latest Opus review lists NO High and NO deploy-blocking Medium, and (d) the neutral judge confirms the fixes. Everything else goes to docs/status/BACKLOG.md for Phase 1 and does not block the first deploy. Final round from consults/20: src — D1 (High) declared Content-Length is reserved in full before bytes arrive → cheap budget exhaustion: charge only received bytes / apply the rate check from the first byte; D2 (Med) 128 KiB/s minimum rate kills slow uplinks and pauses → lower default (e.g. 8 KiB/s after a 256 KiB grace) and make the next-chunk deadline independent; deploy — D3 (High) pin the compose frontend subnet (172.30.39.0/24) and set HLM_TRUSTED_PROXY_IPS to it, drop dead FORWARDED_ALLOW_IPS; Caddy request_body max_size reconciled with the spec (64MiB); D4 (Med) refuse deploying a ref whose runner predates D-038 (or verify an image label equals the revision before skipping the build); ruff clean on deploy/. To BACKLOG: D5 marker-write failure after cutover (Low); N5 remaining unbounded overlap load (Low); XFF junk-entry fallback (Low); writes between live dump and stop lost on snapshot restore (documented). | consults/20. | After this round: one neutral verification + one Opus review; only a new High or deploy-blocking Medium triggers another round.
D-042 | 2026-09-23 | ACCEPTED (supersedes D-040's switch; data reversed it) | Launch with the LOCAL e5-small embedder. Measured with the unchanged production hybrid read path (bench/embeddings/hybrid/RESULTS.md): gemini-embedding-2 @1536+prefix gives hybrid Recall@5 0.920 vs e5 hybrid 0.930 (TR 0.912 vs 0.971; DE 0.939 vs 0.909), because with gemini the lexical/trigram legs add nothing while with e5 they add 13 points; G4 p95 with the live API 736 ms sequential / 552 ms with the embedding run concurrently / 350 ms only on a warm cache, vs e5 322 ms — the OpenRouter round trip alone is ~450 ms p95 from this machine. Keep gemini-embedding-2 (fallback pplx-embed-v1-4b) as a Phase-1 opt-in profile: requires the embedder provider interface with an async embedding step (a sync HTTP call inside embed_query pushed p95 to 1.1 s), RRF re-tuning to benefit from better vectors, and latency measured from the VPS. VPS sizing stays small: e5-small (~0.5 GB per process) fits Hostinger KVM 2 / OVH VPS-2 (8 GB). | Owner asked to evaluate cloud embeddings for cost/practicality; measurement shows no quality gain today and a latency-gate failure. | BACKLOG: embedder provider interface + async query embedding + fusion re-tuning + VPS-side latency test; int8-quantized e5 ONNX to cut RAM/CPU.
D-037 | 2026-09-22 | PROPOSED | Source fix round requested by owner: authorize hidden revision targets before advisory locking and recheck afterward; project cards require device_scope=all; write advisory waits use the request DB budget; correction link survivors and their replay are explicit, survivor last_access_at is recorded; card source edges are superseded per correction window. Raw links overlap the addressed version on both temporal axes (historical endpoints retained), raw/drilldown page links with text. Readiness uses an independent bounded DB connection; admin/revoke has reserved pool capacity; request body timeout is inactivity-based with a generous total cap; register trusts only configured proxy peers and bounds limiter state. PG17 transaction_timeout is an additional bound; API validates embed pins. SDK supported max_request_body_size is raised together with application cap to 64 MiB, retaining §3 50×64,000-character maxima, including escaped Unicode (38.4 MB); complete wire envelopes must fit cap, register cap 16 KiB. | Implementation and regression evidence: docs/consults/14-d037-*.md; tests under tests/. No commit/deploy authorized. | Merge remains subject to D-036 neutral gates + Opus adversarial review; native independent review is not an Opus review.
D-039 | 2026-09-22 | PROPOSED | Source follow-up to review17: readiness shares a shielded single-flight probe, caches success/failure for 1 s, and holds a dedicated DB semaphore through cleanup; reserved admin capacity requires a constant-time admin-token match. Bound retained request bodies globally/per client, retain reservations through handling, and enforce minimum average upload rate with byte grace. Trust no proxy by default; invalid CIDR configuration fails readiness. Restrict revision edge loading to the corrected valid-time interval while preserving historical survivors and replay. Synchronize resolved payloads, cursor shapes, resource settings and edge body-limit contract in PHASE0-SPEC; D-037 remains PROPOSED pending ratification. | Opus review docs/consults/17-opus-review-d037.md (main checkout); coarchitect exchanges and source verification: docs/consults/18-d039-source-followup.md. | No deploy changes or commit authorized. Deploy owner must pin frontend subnet and set matching HLM_TRUSTED_PROXY_IPS, and raise Caddy max_size to 64MiB. D-036 neutral gates and Opus review remain orchestrator ratification requirements.
D-043 | 2026-09-23 | ACCEPTED | gpt-6-astra (codex) hit its usage limit mid-round (available again 2026-09-29). Until then Claude (Opus 5.5) subagents take the implementer role; the D-036 safety structure stays: the implementer never self-certifies, a separate neutral verifier runs the gates, and a separate reviewer attacks the diff. The interrupted round (exit abort rc=134 + worker OOM on a 38.4 MB write + one unidentified flake) left uncommitted work in fix-auth with notes in tests/AUTH-WORKER-VERIFICATION.md: native abort root-caused to ONNX Runtime telemetry thread vs PosixTelemetry shutdown (Apple crash reports) → ORT_DISABLE_TELEMETRY=1; worker switched to keyset-paged chunk reads + token-capped batches; acceptance loops not yet run. | Codex log: "You've hit your usage limit … try again at Sep 29th". | A Claude agent finishes and proves that round; then the usual verify + review.
D-044 | 2026-09-23 | ACCEPTED | **PHASE 0 DONE** (criteria D-041): (a) all 17 R1 repro tests pass; (b) G1–G8 green — suite 520 passed rc=0 on merged main c737fc5 (orchestrator re-run), G3 Recall@5 0.930, G4 p95 ~264–273 ms, G2 0 overflow, G5/G6 incl. pre-body auth gate, O2 crash/heartbeat, G7 real CLIs (last run 2026-09-22), G8 gitleaks clean; deploy gates G-D1..G-D7 + neutral G-D4, real client IP through Caddy, 38.4 MB via /mcp, bootstrap.sh dry-run; memory: 38.4 MB write fully embedded under a 1536m worker limit (no OOM), api/worker peaks ~1.25 / ~0.94 GiB, stack ~2.6 GiB of the 8 GB plan; docker stop exit 0; (c) latest review verdicts "PHASE 0 CAN CLOSE" (consults/28, 30); (d) neutral verifier "FINAL: PASS" (docs/bakeoff/closing/final/). Non-blocking items in docs/status/BACKLOG.md. | Closing sequence: consults/20–30, docs/bakeoff/closing/. | Next: VPS stage (D-032 DP2–DP6) — needs the owner's provider purchase (Hostinger KVM 2 or OVH VPS-2, Ubuntu 24.04), the SSH public key installed at purchase, a domain or the sslip.io fallback, and the KVKK answer.
D-045 | 2026-09-23 | ACCEPTED | End-to-end deploy rehearsal on a local Ubuntu 24.04 VM sized like the target plan (lima, arm64, 2 vCPU / 8 GiB / 60 GiB), following deploy/RUNBOOK.md exactly: bootstrap.sh (dry-run, prepare, finalize-ssh, idempotent re-run: 35 s / 6 s), first deploy from GitHub (160 s incl. model bake), edge checks, neutral probe, the real `hlm` CLI (register/approve/query/close with strict TLS against Caddy's CA), backup/restore drill, redeploy (17 s), upgrade + rollback, reboot self-recovery (/ready 200 ~12 s after boot). REHEARSAL: PASS after fixing one real pre-production defect the local gates could not see: the runner's `umask 077` made the checkout 0600, so the worker (uid 10001) could not read its bind-mounted entrypoint → crash loop on the first real deploy; fix: `checkout_release` makes bind-mounted files group/other-readable after every checkout incl. rollback (remote-deploy.sh). RUNBOOK: re-run command after --finalize-ssh uses the deploy user + sudo. | docs/bakeoff/rehearsal/. | Still unproven until the real VPS: public ACME + DNS, provider firewall/console, native IPv6 client IPs, HTTP/3 over UDP 443. Watch item: worker cgroup memory.peak reaches its 1536 MiB limit via file cache (anon ~830 MiB, no OOM kills) → BACKLOG.
D-046 | 2026-09-23 | ACCEPTED (owner) | Production target: Hostinger VPS 2002259 (srv2002259.hstgr.cloud), KVM 2 (2 vCPU / 8 GiB / 100 GB), datacenter Vilnius (LT, EU), IPv4 153.92.1.166, IPv6 2a02:4780:c:9bec::1, **Ubuntu 26.04.1 LTS** x86_64 (owner kept 26.04; tooling adapted and rehearsed on 26.04 before touching the host — sudo-rs + uutils coreutils). Domain: owner bought `hlmemo.com` at Hostinger (registered 2026-09-23, expires 2029-09-23, locked, privacy on, Hostinger DNS). Public endpoint: `mcp.hlmemo.com` A → 153.92.1.166 (TTL 300), no AAAA yet (BACKLOG: IPv6 limiter keying); apex keeps the parking page for a future site. Hostinger API/MCP access at local scope only (token file ~/.config/hostinger/api_token; rotate after deploy). The account's four other VPSs (incl. a production mail server) are out of scope and must not be touched. | Owner messages 2026-09-23. | Deploy only after the 26.04 rehearsal passes.
D-047 | 2026-09-23 | ACCEPTED | **PRODUCTION LIVE** at https://mcp.hlmemo.com: `first_deploy.sh --host 153.92.1.166 --domain mcp.hlmemo.com --tls acme` (no ACME email, owner) deployed release 912f2d3 to Hostinger VPS 2002259 (Ubuntu 26.04, x86_64, 2 vCPU/7 GiB). Bootstrap 44 s; the client SSH session dropped mid-build when the owner's Mac slept, the detached remote runner finished (status 0, "Deployment ready") — first real-world proof of D-035 item 5; the client's public /ready check timed out before ACME issuance completed (false negative; issuance succeeded minutes later). remote_gates.sh: 8/8 PASS (health/ready, 404, Let's Encrypt YE2 trusted, 5432 closed, MCP probe write/read, hlm CLI, WAN p50 163 ms / p95 324 ms, backup->restore 20 s). G7 against the remote URL: claude PASS, agy PASS after removing a stale local-scope `hlm` entry (127.0.0.1:8765) that shadowed the user-scope registration; codex FAIL only on its OpenAI usage limit (preflight hook reached production; re-run after 2026-09-29). No Hostinger panel firewall; host ufw/fail2ban only (0 bans). Evidence: docs/bakeoff/production-2609/. Follow-ups: BACKLOG (host-fingerprint key type), token rotation, D-021 self-hosting.
D-048 | 2026-09-23 | ACCEPTED | **E2E COMPLETE on production**: owner restored the codex quota (reset coupon); `remote_gates.sh --url https://mcp.hlmemo.com --no-drill --g7` → RESULT PASS: all 7 run gates PASS (WAN p50 165 ms / p95 199 ms) and g7-claude, g7-codex, g7-agy each PASS a headless memory.write -> memory.query round-trip against the remote URL. Closes the D-032 goal (VPS deploy + e2e). gpt-6-astra is available again as implementer (supersedes the D-043 availability window). Evidence: docs/bakeoff/production-2609/05-g7-all-pass.log.
D-049 | 2026-09-23 | ACCEPTED (owner) | KVKK question deferred: HLMemo is in personal use for now; work-computer memories and third-party personal data will be assessed before any shared/multi-user or employer-data use. Owner also states the Hostinger API token was created without expiry (verified valid: API 200); rotation remains recommended because it was pasted into chat — owner's call.
D-050 | 2026-09-23 | ACCEPTED (owner) | Coworker model: codex `gpt-6-sol` replaces `gpt-6-astra` (Astra burned the weekly quota too fast). Default implementers are now Claude (Opus 5.5) subagents; Sol acts as co-architect and adversarial reviewer, consulted in fewer, denser rounds. Supersedes the implementer role in D-036/D-048. Invocation in CLAUDE.md.
D-051 | 2026-09-23 | ACCEPTED (owner) | GOAL: make Phases 2, 3 and 4 fully work as promised by the design (docs/research/00 roadmap + D-series), pass every gate, and deploy them to production; then migrate the HLMemo project itself into production memory as the first real prod test (a fresh chat after an MCP reload). Until that prod test passes, keep the local dev stack (compose project `hlmemo`) and the lima VMs; delete them afterwards. Backup timer and off-host copies are deferred by the owner until the system is complete and optimized (the owner will handle them; the daily timer is intentionally NOT installed yet). The owner gave write permission to the production server.
D-052 | 2026-09-23 | ACCEPTED (owner) | Device registration becomes manual and server-side: device tokens are minted only by a script run on the server (over SSH), with the orchestrator doing the registration together with the owner; production disables public self-registration so unauthorized clients cannot even queue a pending device. A future admin panel may take this over. The concrete design (which routes close publicly, how the token is delivered, the existing registration-secret flow) goes into the Phase 2 spec as workstream W0.
D-053 | 2026-09-23 | OPEN | VPS security posture: no Hostinger panel or panel firewall; host access is SSH pubkey only (root and password disabled, fail2ban, ufw 22/80/443). The owner is open to an additional layer; options (e.g. Hostinger firewall as an outer layer, WireGuard/Tailscale-only SSH, admin routes via SSH tunnel only) are to be proposed in the Phase 2 spec W0.
D-054 | 2026-09-23 | ACCEPTED | **First real-data retrieval baseline** (corpus A: a private owner project, 288 items / 1,427 chunks imported mechanically by eval/realdata/import_corpus.py; 100 hold-out questions written blind, 92 answerable + 8 negative; scored by eval/realdata/run_eval.py; private data under docs/private/realdata-yt/). Budget 3000: source hit@1 .315, hit@3 .489, hit@5 .554, hit@10 .685, MRR .437; answer visible in the query response (L1) .098; after drilling the top-3 clues (L2) .500. Temporal: 0/15 at L1, 3/15 at L2. Negatives: 8/8 `evidence:matched` (evidence carries no signal). Query p50 44 ms / p95 61 ms. All 46 L2 misses have the answer inside one chunk of the right source → ranking/preview, not chunking. Causes: lexical noise from function words (12), TR↔EN mismatch (12), temporal (7), titles not indexed (6), wrong chunk of the right doc (4), other (5). Synthetic G3 (0.90+) overstates real quality. Pre-Phase-2 fix round: DF/stop-word query term filtering, query-centred previews, title/path indexing, drill-top-5 guidance, CLI second-device fingerprint bug. TR↔EN rewriting, temporal supersession and a real no-evidence signal are Phase 2 (librarian). This baseline is W-E's corpus-A reference.
D-056 | 2026-09-23 | ACCEPTED | **Incident + guard:** the local dev DB `hlm` (compose project `hlmemo`) was truncated at 10:02:58 UTC during the D-055 verification round, losing the corpus-A import (re-importable: eval/realdata/import_corpus.py is deterministic) and any other dev data. Root cause: tests/conftest.py fell back to the compose `hlm` database when HLM_TEST_DSN was unset, and its autouse fixture truncates every table; one pytest invocation without the DSN (exact caller still being traced) wipes the dev DB. The rule "never run pytest without HLM_TEST_DSN" existed only as a memory note. Fix: the fallback now uses a dedicated `hlm_test` database (created if missing), and any DSN naming a protected DB (`hlm`) is refused with pytest.UsageError before migration/truncation (tests/unit/test_conftest_guard.py). Production is unaffected.
D-055 | 2026-09-23 | ACCEPTED | **Pre-Phase-2 retrieval fix round** (implements D-054; merge 98bec35, commits 7d317e6 + 611598f): (1) query-term filtering by per-project prefix document frequency over the shared corpus: drop non-identifier terms present in >20% of chunks, no filtering below 100 chunks, keep the 2 rarest if all are common, no stop lists; the DF cache is invalidated by the project's max version id (TTL fallback), single-flight, capped at 50k rows / 2 s (unfiltered on timeout), LRU 64 projects / 2M lexemes; historical (valid_at/known_at in the past) queries are not filtered. (2) Query-centred previews plus a min(216, room/4)-token reserve so the top-3 PREVIEW_EXT step fires, then a refill. (3) Titles/paths as a 4th RRF list (migrations 0003_title_lexical + 0004_title_norm_fold: online GIN expression index; SQL `hlm_title_norm` folds both sides by construction). (4) Guidance: drill the top 5 hits in one call. (5) The CLI fingerprint includes an atomically created per-config-dir install id. Corpus A (budget 3000, pre-review code): hit@3 .489→.739, MRR .437→.660, L1 .098→.533, L2 .500→.750 (.772 with top-5). G3 .930→.980, G4 p95 ≈265→251 ms. Neutral verifier (consult: agent report) CONFIRMED lint/unit/integration (231 passed, 1 opt-in skip)/G3/G4/migration round-trip on 7d317e6; the real-data claim could not be re-derived (corpus lost, D-056) and must be re-measured after re-import. Sol review 33: MERGE-WITH-FIXES, 8 findings, 7 fixed in 611598f; #5 (not a clean hold-out estimate) stands: thresholds frozen (0.20/100/2/20/216), confirmation on sealed corpus B required. Process violation noted: the implementer migrated the dev DB `hlm` to 0003 (dev API /ready 503 from 09:16 UTC until redeploy).
D-057 | 2026-09-23 | ACCEPTED | **D-055 re-measured on merged main after re-import** (corpus A byte-identical: 288 items / 1,427 chunks; dev stack rebuilt at 0004). Budget 3000: hit@1 .533, hit@3 .739, hit@5 .793, hit@10 .902, MRR .662, L1 .533, L2 .750 (top-3) / .772 (top-5) — reproduces the implementer's numbers independently. Budget 1500: L1 .467, L2 .685/.707. Per category L2 (top-3→top-5): identifier .933→1.0, cross-lingual 1.0, multi-hop 1.0, decision .733, fact/config .682→.727, procedural .714, temporal .400; TR .667→.711, EN .830. Negatives: still 8/8 evidence=matched, but top-1 score AUC .635→.792. Query p50/p95 59/74 ms. **Regression:** stale-first on temporal questions rose from 4/15 to 7/15 (L1) and 8/15 (L2): better previews surface superseded text more often; the fix belongs to Phase 2 temporal supersession (W2b) and is a named gate there (stale-first ≤ D-054's 4/15). The D-055 CLI fingerprint fix is confirmed (two devices registered from one OS user without an override).
D-058 | 2026-09-23 | ACCEPTED (owner) | Answers to the roadmap §7 questions. Q1: enable the Hostinger VPS firewall now (22/tcp, 80/tcp, 443/tcp, 443/udp inbound); Tailscale later. Q3: `hlm-global` visible to personal devices via explicit grants, work devices only when granted, cross-project widening propose-only. **Q2 replaced by a Phase 5 (controlled legacy-memory migration):** all of the owner's existing projects (with, without or with a primitive memory system; ~1 year of memory) are migrated into HLMemo ONE PROJECT AT A TIME under a controlled protocol that is audited on every project; whenever the audit shows a needed improvement, it is fixed before quality degrades. This migration replaces the 14-day shadow mode: the librarian starts as an OBSERVER (proposals only), is promoted to ASSISTANT only with owner approval once it has proven itself during migration, and after it stabilizes the protocol is standardized and the remaining projects migrate semi-automatically (still sequentially). **Q4: no spend cap during development** (OpenRouter wallet ≈ $23); the budget is set after development, targeting maximum price/performance (not minimum cost). The cost-reservation mechanism stays (safety against runaway loops) with generous development defaults. **Q5: final test order:** HLMemo first, then another of the owner's projects; if that is not enough to exercise the librarian, the owner provides more projects, migrated sequentially; a roadmap for the remaining projects follows once the librarian is optimized. **Q6: quality motto — "the better we optimize, the better":** first pass the initial gates (+3 points overall, no category −3, W3c ≥ −1), then keep optimizing toward the best achievable score. Release cadence: incremental R1–R4.
D-059 | 2026-09-23 | ACCEPTED | D-053 step 1 done: Hostinger VPS firewall `hlmemo-prod` (id 365389) active on VM 2002259 only (other VPSs untouched), inbound accept 22/tcp, 80/tcp, 443/tcp, 443/udp, all else dropped. Verified: inbound HTTPS /ready 200; outbound OpenRouter/GitHub/Let's Encrypt/DNS OK (the provider firewall is stateful); 5432 closed; a port opened temporarily in ufw (8099, local 200) was unreachable from outside, which proves the provider layer filters; the temporary rule was removed (0 rules left, verified). Next layer (Tailscale-only SSH) deferred. Lesson: heredoc scripts containing `ssh` must use `ssh -n` (stdin swallowing, same class as D-035), and `pkill -f` over ssh can match its own command line.
D-060 | 2026-09-23 | ACCEPTED | **W-C contract freeze**: PHASE2-4-ROADMAP.md "Cross-cutting contracts" CC-1..CC-5 as committed in e5d79bb are frozen. They cover the migration chain 0005..0010 (with `main` label at 0005; agents never pick down_revision, the orchestrator links at merge), the new event kinds, the system actor + capability recheck at apply, the 9-tool budget with G-SURF, and the llm/1 audit payload + cassettes + release-blocking live gate. Changes need a new decision. Fan-out order: W0a and the corpus-B builder (W-E) start now in parallel. Each agent gets its own DB (hlm_test_<ws>); hlm, hlm_verify and hlm_retr are off-limits to implementers (D-056).
D-063 | 2026-09-23 | ACCEPTED (orchestrator) | **Query latency under write load** (found by the honest G-L3 rebuild in W2a): the query p95 during 100 concurrent ~6 kB writes was 6.7 s (269 ms without writes). Causes: (1) the trigram/lexical GIN pending list (fastupdate) makes candidate SQL scan unmerged entries, 6.5 s; (2) each write invalidated the D-055 DF cache, so the next query blocked on a ~210 ms rebuild. Decisions: (a) the chunk GIN indexes get `fastupdate=off` (ALTER INDEX, no rewrite; flush the existing pending list with gin_clean_pending_list), with slightly slower writes accepted for predictable reads; (b) the DF cache becomes stale-while-revalidate: when a cached DF exists, no query ever blocks on a rebuild; a single-flight background refresh starts on invalidation; maximum staleness 5 s; a cold start (no DF yet) keeps the 2 s-capped blocking load. This deliberately relaxes the Sol 33 #1 test "the next query sees the write" to "a query ≥5 s after a write sees it": the DF is a heuristic term filter, one write barely moves a 20 % threshold, and Sol's concern was 600 s staleness. G-L3 (queries timed DURING writes, the librarian stalled or 503, through the real API) becomes a release-blocking gate, run at R2 and before Phase 5 bulk imports, not only opt-in. Chunk tokenization for bodies ≥2048 chars runs on a bounded 2-thread executor (W2a 6b88877).
D-064 | 2026-09-23 | ACCEPTED (orchestrator) | D-063 clarification after Sol 37: the DF staleness bound means (1) no query blocks when a cached DF exists; (2) any query that finds the DF older than 5 s past an invalidation triggers (or joins) the single-flight refresh and may be served the old DF only while that refresh is in flight; (3) if refreshes fail, or the DF is more than 30 s past an unrefreshed invalidation, queries run UNFILTERED (no DF) until a refresh succeeds, so staleness is never unbounded; (4) a cold load has ONE 2 s total budget (not per statement). The migration flushes GIN pending lists OUTSIDE the transaction that alters the indexes (autocommit step, no long table lock on a live DB), or leaves the flush to autovacuum.
D-062 | 2026-09-23 | ACCEPTED (orchestrator) | **W2a librarian contract** (after Sol 35/37/38): (1) CC-2-kind events use schema_version 2; payload.request = the actor's arguments; LLM jobs carry the llm/1 audit record with the exact CC-5 field names (flat for one call, calls[] of that shape for several); payload.resolved holds only applied effects, plus completion time, attempts and run_after; request_id = uuid5(job dedupe key) → one event per job; replay never calls an LLM. (2) Privacy default-deny: items enter a prompt only if the triggering device is trusted and unexpired, holds a current grant and the capability on every item's project, no project has policy.librarian=off, and the scope is not device:*. The check runs at plan time and immediately before EVERY provider attempt, including retries and fallback. **Race semantics (decided):** a revocation or grant removal that commits before an attempt's precheck prevents that attempt; an attempt already in flight, or in the precheck-to-send window, may complete, but its result is never applied (the apply-time recheck holds FOR SHARE locks). Holding a DB transaction open across an LLM call is rejected. Working-memory rules must be librarian-authored, rule-shaped and redacted, and may not reproduce item text (overlap guard). (3) Roles: observer by default; set_role is a `librarian` event with op=set_role (replaces §4b's `librarian_role` kind); assistant and autonomous need a matching decision; approvals are `answer` events. (4) `librarian_questions` (accepted replacement for the §4b sketch): question_id uuid PK, job_key text, batch_id uuid, project_id FK, project_ids bigint[], kind CHECK(contradiction|widen_scope|merge|promote|card_refresh|quarantine|pack_accept|link), subject_clues text[], subject_version_ids bigint[], proposal jsonb (redacted), status CHECK(open|approved|rejected|superseded), created_at, decided_at, decided_by FK devices, source_event_id FK events. (5) Spend guard: hour/day/month atomic reservations at peak price; a per-lineage 20-call ceiling counts only attempts that actually reach the provider; 5xx/transport/timeout settle at worst case, a 4xx before generation at 0; a trip pauses the librarian (breaker_state=budget). (6) Fallback profile: openrouter-luna (a future bench v2 decision may change the profiles). (7) memory.raw adds payload_body paging (D-026, additive).
D-065 | 2026-09-23 | ACCEPTED (orchestrator) | **W0a is a one-way door** (after Sol 39 found a Critical: the rollback guard trusted HLM_REGISTRATION_MODE=closed, which pre-W0 code ignores, so a manual rollback to 912f2d3 could reopen registration). Decisions: (1) Manual rollback (`deploy.sh --rollback`) to any pre-W0 release is REFUSED unconditionally; it is allowed only between W0+ releases, whose code is closed-by-default. (2) The automated in-deploy recovery (failure before the cutover completes) stays: it restores the pre-cutover env WITH its registration secret (verified by the fake-harness test and the upcoming VM rehearsal). (3) Disaster recovery from a bad W0+ release = roll FORWARD: the W0+ code plus a restore of the recorded pre-upgrade/quiesced dump (restore.sh migrates). (4) Rollback and --accept-release must verify that the running release (image label/revision) equals release-state current_ref before acting; rollback helpers are copied from the CURRENT release into a temp dir before any checkout, so an interrupted rollback can be re-run; state consumption and legacy-marker removal become one derive step. Production holds only test data today, so nothing of value is lost by forbidding a downgrade to Phase 0.
D-066 | 2026-09-23 | ACCEPTED (owner) | **Librarian model tiers after bench v2** (bench/results/20260923-19*/20*-v2.md): default profile = `openai/gpt-6-luna` (94.2%, best $/correct); fallback = `deepseek/deepseek-v4.1-flash` (89.3%, different provider = outage hedge; weak T9 risk_check 61%, false-warn 43%); `google/gemini-3.1-flash-lite` EXCLUDED (T11 prompt injection: complied 21/30). The owner wants a **two-tier librarian**: gpt-6-luna for routine tasks and `openai/gpt-6-luna-pro` (95.1%, +2.4 on hard, +3.1 on real data) for hard tasks (e.g. consolidation, contradiction resolution), live in Phase 5, with the task→tier mapping chosen on real migration data. The pro tier needs a NON-OpenAI fallback. Candidates to bench-v2 at Phase 5: deepseek-v4-pro-0813 ($0.46/$1.39, first choice), deepseek-pro-latest (unpinned alias, not for prod), glm-5.3, kimi-k2.6, qwen3.8-27b. **Outage behaviour (owner):** when the primary provider is down and a fallback tier is in use, HLMemo tells the project's coding LLM through MCP (librarian status: degraded, active model/tier), so it can weigh proposals accordingly. The profile switch to gpt-6-luna happens after the W2a integration merge; the two-tier split and pro fallback are Phase 5 work (W5a).
D-067 | 2026-09-23 | ACCEPTED (owner) | **Annotation to D-066: even the ceiling model fails, so LLM usage must be engineered, not trusted.** In the bench v2 calibration the strongest model (`openai/gpt-6-sol`, ~14x the cost of gpt-6-luna) scored 95.8%, not ~100%. It missed 11/200 cases: cross-lingual query rewrite T7 89.9% (a missed identifier in 3 cases), risk_check T9 91.8% (1 false warning, 3 partial lesson sets), abstention T10 a 10% false-answer rate (a confident 0.96 answer where none existed), and a long-JSON field miss. Some misses may be over-narrow gold (these go to the pending adjudication), but at least the confident false answer and the false warning are genuine model errors. Consequence (owner): a near-flawless system cannot rest on model quality alone; the LLM layer must be precise and robust by construction. Binding requirements for Phase 2-5 work: (1) per-task prompt + schema optimization against bench v2 and the real-data evals (the W-O optimize loop), with every change measured; (2) deterministic guards around every LLM decision: schema and enum validation, evidence/clue-reference checks (a claim must cite existing clues), no mutation without referenced versions (D-062); (3) calibrated confidence: thresholds tuned on data, low confidence → a question to the owner, never an action; (4) for high-impact mutations (invalidate/supersede, promote, cross-project widen): self-consistency (≥2 samples or a second tier, luna vs luna-pro) or an independent verifier call must agree before a proposal is even raised; (5) abstention is first-class: "no answer / no contradiction / no lesson applies" is rewarded in the prompts and scored; (6) everything runs under the observer→assistant ladder (D-058) with audited proposals, and per-task error rates are tracked in the leaderboard next to cost. The gold adjudication of the 11 ceiling misses runs before any bench-v2-based threshold is fixed.
D-068 | 2026-09-23 | ACCEPTED | **Release R1 in production** (ee6ce9c): D-055 retrieval fixes, W0a access hardening, W2a librarian (DISABLED), migrations 0003..0006. It was rehearsed first on hlm-2604 (docs/bakeoff/rehearsal-r1: 8/8 PASS, including a fault-injected auto-recovery), and the rehearsal findings were fixed (retired-secret backups recorded and swept, short-SHA resolution, memory table, hlm init hint). Production: `deploy.sh --accept-compose-change=2cca62f6…` EXIT=0; route table PASS in-deploy and public; remote_gates 8/8 PASS (+ drill skipped): routes, hlm-cli (register refused, ops mint+login, revoke --self), WAN p50 126 / p95 206 ms; public /ready is status-only. Device inventory: every old gates/judge device already revoked; trusted = reserved admin (no HTTP bearer in prod), librarian (system), g7-cemals-mb-pro-3 (gates-g7). **Owner device minted** via hlm_ops.sh: id 21 `cemals-mb-pro-3` (personal, hlmemo:write), token via `hlm device login --token-stdin` into the keychain; project `hlmemo` created; claude (user scope), codex (needs `hlm codex` or HLM_DEVICE_TOKEN) and agy registered to https://mcp.hlmemo.com/mcp; the repo's gitignored hlm.toml now points to production (backup of the local-dev version plus the 3 CLI configs in deploy/.local/backups/owner-20260923T222015). Pending: `deploy.sh --accept-release` after a soak (deletes the pre-W0 secret backups), a Sol pre-R2 review, and G-L3 on the 2 vCPU VM before enabling the librarian.
D-069 | 2026-09-24 | ACCEPTED (orchestrator) | **Overnight fan-out + migration renumbering (amends CC-1).** The owner's overnight plan runs Phase 1.5 + all of Phase 2 (roadmap order) to R2. Migration chain: `0007_import` (W1.5: memory_versions.source, source_key, code_refs, the D-015 skeleton-card backfill) → `0008_librarian_tasks` (W2b/W2c/W2d: version_signals, librarian_batches, the librarian_questions extension for memory.answer). Phase 3/4 revisions shift to 0009+. Wave 1 in parallel (own worktrees and DBs): A = W1.5 import/export + skeleton card + ops status; B = W2b + W2c + D-067 guards (+ ops librarian subcommands); C = W2d risk_check/register_lesson (+ preflight wrapper); D = W2f hlm bench + bench-v2 gold adjudication + leaderboard. Wave 2: W2e synthesis after B. Ownership to limit conflicts: cli/preflight.py → C; ops/librarian* → B; ops status + project create → A; new MCP tools live in their own modules, with a one-line registration each. The orchestrator relinks 0008 → 0007 at merge. Every branch gets a Sol review (max 2 fix rounds) plus neutral verification before merge. R2 = librarian ON in the observer role only after all gates pass, including G-L3 on the 2 vCPU VM and the live gates on gpt-6-luna and the deepseek fallback. The HLMemo self-import into prod is a TEST (wiped before the final release). Corpus-B sealed stays sealed; the first e2e prod test uses the dev split.
D-070 | 2026-09-24 | ACCEPTED | **W2f merged** (f239fcc; Sol 40 → fixes fb527d7). `hlm bench` runs on the production provider, Redactor.from_settings() and the shared Postgres llm_budget by default (`--budget local` is explicit and not shared). The bench-v2 gold adjudication is STRICT (adj-2): of the 11 ceiling misses, T10-07, T9-03, T9-01, T9-15, T9-25 and T12-10 remain model errors; T7-16/T7-18/T8P-02 gold was widened; T7P-08/09 identifier optional. So D-067 stands: the ceiling model gave a confident non-null answer where the contract required null. Adjusted scores: gpt-6-sol 96.6, gpt-6-luna-pro 95.6, gpt-6-luna 94.9, deepseek-v4.1-flash 89.8 (the ranking and the D-066 tiers are unchanged). The leaderboard (eval/results/leaderboard.json, schema 2) is append-only; the W-E rules are enforced; partial runs are never ranked. The cassette key now includes the attempt number (backward compatible).
D-071 | 2026-09-24 | ACCEPTED (orchestrator, after Sol 41) | **risk_check fallback policy:** G-LIVE-C is release-blocking and the deepseek fallback fails it (false-warn 0.25 vs ≤0.10; catch 1.0), so deepseek is DISABLED for the `risk_judge` task (profile disabled_tasks). During a primary outage, or with the judge disabled, memory.risk_check returns the deterministic verdict labelled `judged:false, judge:"retrieval_only"`; it never presents a weaker model as a qualified judge. (The orchestrator's first proposal, labelled deepseek, was withdrawn: labelling does not satisfy a quality gate, per D-067.) A pro-tier or other fallback that passes G-LIVE-C is chosen in the Phase 5 fallback research (D-066). Primary gpt-6-luna: catch 1.0, false-warn ≤0.05 (PASS).
D-072 | 2026-09-24 | ACCEPTED (orchestrator) | **W1.5 temporal rule + G-I4 status.** (a) Imported memories keep server recorded_at; file mtime and commit date are provenance only (`source`); valid_from comes only from explicit evidence (frontmatter date, dated decision row, dated heading; a date without a time is read in the importer's --tz), else the import time; a future date beyond 5 min rejects that item. This is the D-020 deviation proposed in the roadmap. (b) G-I4 (D-020 sampled recall ≥ 0.90) FAILS at 0.794 strict / 0.853 with drilldown on 34 self-authored questions over HLMemo docs (first run 0.559; heading-section items fixed the import-granularity part). The remaining misses are ranking, not import: TR↔EN (2), very self-similar docs ranked 11–36 (3), right item but wrong chunk (2). The bar is NOT lowered: G-I4 stays open as a known retrieval gap (the same limitation as the corpus-B baseline, evidence R@5 .451). It does not block R2, because import fidelity is proven by G-I1..G-I3 and W1.5 introduces no retrieval regression. It is carried into the e2e report as a gap, with cross-lingual query rewriting as the lead candidate fix.
D-073 | 2026-09-24 | ACCEPTED | **W1.5 merged** (0c0c013; Sol 42 → fixes 0afc34a/f69630d/e0a785e). 0007_import is ONLINE: a UNIQUE index built CONCURRENTLY on (project_id, source_key) over open-validity rows, with a 500 ms lock_timeout per step plus retry (proved: no write waits >1 s with a 2.5 s reader). The source CHECK requires system/path/sha256. Evidence dates are explicit-only (frontmatter, `D-NNN | date |` rows, date-led headings). Revision request_ids include expected_version_id. Exports carry `origin: <project>/<logical_id>` and map back by id only in their own project and only at head; elsewhere the origin becomes the item source. Renamed sections are re-mapped by similarity. **Write-contract extension: `Item.close`** ends an item's validity (nothing is deleted) for sources that truly disappeared; it has a mass-close guard and `--keep-missing`. Imports carry `librarian_priority=6` in the resolved payload (same field as register_lesson=2, W2d). G-I4 stays open (D-072): the remaining chunk misses are fixed by one drilldown, a read-path choice.
D-074 | 2026-09-24 | ACCEPTED (orchestrator, after Sol 48 NO-GO) | **In the OBSERVER role, `memory.answer` is a LABEL, never an action.** accept/reject/custom answers are recorded (answer event + question status), feeding the Phase-5 proposal audit and the promotion metrics, but NO user item, link, validity or scope is changed while any touched project's effective librarian role is observer. Accepted-but-unapplied answers stay in an `accepted_pending` state; after an explicit promotion to assistant (set_role decision), they can be applied through the normal batch path with the full staleness recheck. This makes D-058's "observe first" literal: in R2 the librarian and the answer tool together cause zero user-data mutations. Also required before R2: the post-cutover check enforces enabled + live + observer consistently in api, librarian and heartbeat, and fails on a judge-config error; batch TTL is re-checked against a fresh clock after the last lock wait.
D-075 | 2026-09-24 | ACCEPTED (orchestrator, after Sol 49) | 0008_librarian_tasks has never been released (production is at 0006; R2 migrates 0006 → 0007 → 0008 with the final file), so the D-074 `accepted_pending` status is added by EDITING 0008 in place rather than by a new revision; dev/test/eval DBs at the pre-edit 0008 are rebuilt, not migrated. After R2, 0008 is frozen like every released revision. Remaining Sol-49 blockers are assigned: apply_batch checks the effective role of every touched project (cross-project observer safety), promotion enqueues accepted_pending applies (no stranding), check_librarian rejects stale heartbeats.
D-076 | 2026-09-24 | ACCEPTED (orchestrator) | **Hold-out librarian gates FAIL → R2 ships the librarian in OBSERVER only (as planned), the apply path stays gated.** On a disposable copy, with the librarian at assistant + approve-all (gpt-6-luna, deepseek verifier; $0.25, 379 calls): G-E-TEMP stale-first 8/15 → 8/15 (gate ≤ 4) FAIL; G-E-W2b corpus A hit@5 .793 → .783, corpus B evidence R@5 .451 → .438 (gate +3, no category −3) FAIL; stale-claim not higher PASS. Proposal precision on 50 blind-labelled proposals: strict .42 (CI .29–.56), lenient .94; duplicate 0/6; contradicts+close 2/8 (closing a whole multi-fact item ends still-valid facts); the auto 'action' tier is worse (6/23) than the 'question' tier (15/27); refine direction errors. Per the W-E rule, a feature that misses its gate ships disabled: in R2 no librarian mutation is possible (observer, D-074), so retrieval cannot regress, and the librarian's value is realised only as audited proposals during the Phase-5 migration. Promotion to assistant requires passing G-E-TEMP/G-E-W2b. The improvement backlog (the e2e report): fact-level supersession instead of whole-item close; contradiction for doc_chunks; duplicate detection; refine direction; stricter action tier; approve-all staleness chains. Note: the eval runner (not an implementer) briefly printed fragments of 3 corpus-A questions into its own context; the blind judge never saw them and no implementer did.
D-077 | 2026-09-24 | ACCEPTED (orchestrator) | **R2 GO in OBSERVER mode despite Sol 50 NO-GO, with the reasoning recorded.** Sol 50 confirms that the cross-project apply role check, the stale-heartbeat rejection and the 0008 ruling are fixed, and states that in the configured OBSERVER role the librarian worker and memory.answer cannot change any user item, link, validity or scope, and that librarian/provider failures do not degrade query/write. Its one remaining NO-GO item is promotion stranding (roles.py:137: a promotion selects questions only by their recorded projects, so an accepted_pending answer whose action later touches project C is not re-queued when C is promoted). That path exists only after a role promotion, which R2 does not perform: the post-cutover check enforces observer, and the RUNBOOK forbids `ops librarian role set` above observer until this is fixed. **Hard prerequisite before any promotion (Phase 5):** fix the stranding (select by the action's current touched projects, re-queue on any touched project's promotion) plus a Sol re-review.
D-078 | 2026-09-24 | ACCEPTED | **W2e integrated** (int-w2e 35065b5; Sol 51/52 fixes f64b65a/3fbf1d2). `memory.query` gains `synthesize?: bool` (query/2) and is app-bound. Without the flag, the output is byte-identical to the pre-W2e path (1,230 comparisons, including librarian blocks and applied supersession). With the flag: D-057 supersession shapes the hits before synthesis; the post-call re-check drops any sentence citing an item that is no longer citable (superseded/closed/unreadable) and removes such hits and the card; a whole-token support guard (numbers/identifiers/paths/commands verbatim in the cited excerpt); a 7 s total deadline including the recheck SQL; ≤2 unpooled fallback connections; fast-path fallback, never an error. G-LIVE-D: luna +16 pts (0/8 negatives answered after the guard), deepseek +19 (labelled tier:fallback). The preflight wrapper default is `HLM_PREFLIGHT_SYNTHESIZE=off` (only `--ask`) until the hold-out W-E gate decides. Residual: the librarian block is not refreshed by the post-call re-check (clue ids + template text only).
D-079 | 2026-09-24 | ACCEPTED | **Release R2 LIVE in production** (6902f91): Phase 1.5 + Phase 2 (W1.5 import/export, W2a–W2f), the librarian ON in the OBSERVER role (profile openrouter-gpt6-luna, fallback openrouter/deepseek; risk_judge fallback retrieval-only per D-071; synthesis flag-gated, wrapper default off), migrations 0007 + 0008. Path: VM rehearsal of 3535bcc ALL PASS (G-L3 on 2 vCPU p95 268/375 ms; import burst p95 217 ms; observer 829 proposals / 0 applied; kill switch; reboot). The final ref added only app-level fixes (W2e + Sol 51/52, Sol 49, the risk/synthesis ledger deadline), verified by the full local suite (lint; unit 535; deploy 132; integration 443/0 failed); the deploy path and compose (sha256 99fecbf4…) were unchanged, so there was no second VM pass. llm.env installed via install_llm_env.sh (key over ssh stdin, 0600). The deploy passed the in-deploy and public route checks and the librarian check (enabled/observer/live, heartbeat 6.7 s). remote_gates --librarian: 10/10 PASS (risk-check judged=true; librarian job in 8 s, observer, 0 links/closes/applied; WAN p50 134 / p95 161 ms). E2E test next: HLMemo's own memory imported as a TEST into a separate project `hlmemo-e2e` (the real `hlmemo` project stays clean for the final migration).
D-061 | 2026-09-23 | ACCEPTED (logged late on 2026-09-24; referenced since W0a) | W0a access hardening: registration_mode/admin_http fail closed (defaults closed/disabled; only local compose and test fixtures opt in); compose.prod.yaml pins HLM_DEPLOYMENT=production + closed + disabled and the API refuses unsafe config; a middleware route filter answers 404 for register and every admin route before reading a body byte; public POST /devices/revoke is self-only; device 1 is never bound in production; devices.expires_at (0005_w0_access, label main) is enforced like revocation; all admin work goes through python -m hlmemo.ops over SSH (hlm_ops.sh); tokens reach clients only via `hlm device login --token-stdin`; remote-deploy gains migrate_env_w0 and route checks. Released in R1 (D-068).
D-080 | 2026-09-24 | ACCEPTED | **First e2e production test (R2, HLMemo self-import as TEST into `hlmemo-e2e`)**, report docs/status/E2E-PROD-REPORT.md, data docs/status/e2e/2026-09-24/. Proven in prod: import of 363 items in 82 s, 0 errors; query p95 ≈ 400 ms during the import burst, 0 errors; the librarian observer drained 368 jobs (310 proposals) with 0 mutations (byte-identical export); spend $0.51. Gaps: corpus-B dev hit@5 .500 (baseline .625), Turkish .278 (a single Turkish report floods the top-5); synthesis 11% cited-false → the wrapper default stays OFF; risk_check 0/12 because the automemory importer mis-parses `metadata.type` (no lessons created); a cross-project librarian proposal from the test project onto `hlmemo` (not applied). Next: the quick fixes, TR/EN retrieval, librarian judgement, fallback research, Phase 5 (report §7).
D-081 | 2026-09-24 | PROPOSED (owner idea, orchestrator design; Sol consult 53) | **English as the retrieval pivot language, original preserved.** The owner proposed making every stored memory and every query English (translating on the way in). Adopted with one change: the ORIGINAL text stays the authoritative body (events are authoritative; translation errors in numbers, identifiers, paths or nuance must never become permanent). (1) Queries: the tool descriptions tell agents to query in English; the server detects non-English queries and, when the librarian is available, rewrites them to English (bench T7 task), searching original + English and fusing the results (RRF); the fallback is the original query only. (2) Storage: for non-English items, the librarian adds an async English index rendition (retrieval-only derived chunks, never replacing the body) with a verbatim guard (every number/identifier/path/command in the source must appear in the rendition); previews prefer the English rendition, and drilldown/raw keep the original. The core never waits for the LLM. (3) A per-source cap in the top-5 (≤ 2 slots per source) against single-document floods. (4) Gate: the W-E hold-out on corpus A/B: Turkish questions must improve materially, English must not regress (≤ −1 pt). Migration 0009_language_pivot.
D-082 | 2026-09-24 | ACCEPTED (owner idea) | **D-081 amended: client-first English rendition, librarian as backstop.** The project's coding LLM (Claude/Codex/agy, which already holds the full context) supplies the English index rendition itself at write time via an optional `index_en` field per item. The tool description asks for it whenever the body is not English. Benefits: better translation (full context), zero server LLM cost/latency, rendition available immediately. The server VALIDATES a client rendition before indexing (anti index-poisoning): every number/identifier/path/command/quoted string of the body must appear verbatim, the length ratio must be plausible, and the text must be detected as English. It is recorded with `rendition_source: client`. When a rendition is missing (hlm import, non-compliant clients, legacy items) or rejected, the librarian fills it asynchronously (`rendition_source: librarian`) with the same validation. The original body stays authoritative (D-081). Queries: agents query in English; server-side detection + rewrite only as a backstop for non-English queries.
D-083 | 2026-09-24 | ACCEPTED | **Post-e2e quick fixes merged** (Q: 7661009, 4169ea6, 547d7d7; Sol 54). Auto-memory type is read at the top level or under `metadata:`; feedback→lesson, user/project/reference→fact. Lesson files split into one lesson per rule when there are ≥2 rule headings or labelled/bulleted rules (not numbered procedures or a single rule heading); the keys come from the heading, a bold label or `rule-N`; the old whole-file item is closed as `replaced_by_split`; a kind-only change is a revision. The risk judge gets the title plus the best-matching readable window for lessons > 1,200 chars. `projects.policy.librarian_cross_project=exclude` is two-way (`candidates.relation_allowed`: an item/relation touching an excluded project is allowed only within exactly that project), enforced in candidates, risk_check SQL and at APPLY time (`actor.policy_blocked`, FOR SHARE → authority_lost/policy_excluded); policy changes are replayable. Cosmetics: hlm.export validation skip, ops status breaker naming, realdata client keep-alive.
D-084 | 2026-09-24 | ACCEPTED (owner asked the orchestrator to decide) | **Coworker/reviewer model: codex `gpt-5.6-sol` replaces `gpt-6-sol`** (both at reasoning xhigh). Blind bake-off (docs/bakeoff/sol-5.6-vs-6/): the same neutral review prompt on W2e @ e2f30d8, scored against a pre-written key of 6 known defects: gpt-5.6-sol 6/12 vs gpt-6-sol 4/12. 5.6-sol also found two extra real high-impact defects: llm.env not mounted into the api, and the 6 s synthesis cap defeating the fallback given the 1/2/4/8 s retry backoff (the latter is still OPEN → backlog). gpt-6-sol was ~34% faster (315 vs 477 s). Caveat: one task; periodic re-checks. Supersedes the model choice in D-050.
D-085 | 2026-09-24 | ACCEPTED (owner) | **Reviewer policy (amends D-084):** ROUTINE reviews/consults use codex `gpt-6-astra` at reasoning **low** (best in the blind bake-off: 8/12 vs gpt-5.6-sol 6/12 and gpt-6-sol 4/12; fastest at 166 s; fewest tokens at 67k; per-token price ~5× sol, so the effective cost is similar to sol xhigh/max). CRITICAL reviews (release gates, security/privacy, data-integrity, migrations) run BOTH `gpt-6-astra` low AND `gpt-5.6-sol` xhigh in parallel and merge their findings: their blind spots differ, and the union covers 10/12 of the known defects. Evidence: docs/bakeoff/sol-5.6-vs-6/RESULT.md (n = 1 task; re-check periodically on known-defect reviews).
D-086 | 2026-09-24 | ACCEPTED (orchestrator, after dual review 57) | (1) **widen_scope is propose-only in EVERY role** (D-058): an accepted widen is never applied by the batch path or the sweeper, even after promotion. It stays `accepted_pending` and is applied only by an explicit `memory.answer` from a device with write on every touched project; the sweeper and release jobs skip it (no requeue loop). (2) **Librarian job events (amends D-062/Sol 38 #6):** exactly ONE terminal librarian event per job (done|failed, request_id uuid5("job:<key>")), plus at most 4 compact non-terminal `defer` events per job. Systemic hand-backs (breaker/budget/outage) change only the job row and are NON-authoritative for replay: replay compares the terminal state and the event-recorded fields, not run_after/last_error of a still-queued job. First use of the D-085 dual review (astra-low + 5.6-sol): both found the signal-only replay race and the widen requeue loop independently, and each found one issue the other missed.
D-087 | 2026-09-24 | ACCEPTED (orchestrator) | **Hold-out re-run of librarian judgement v2 (J@a65a8f5, pinned via git-archive) FAILS both gates → observer-only stays (D-076 unchanged).** G-E-TEMP (corpus A, assistant + approve-all): L2 top-3 stale-first 8/15 → 8/15 in approve rounds 1 and 2 (gate ≤4, FAIL; same as D-057/D-076); temporal L2 .400 → .400 (PASS). G-E-W2b (A ∪ B-dev, 92+72 q): overall .643 → .616 (r1) / .595 (r2), needs +3 → FAIL; worst categories A decision −6.7/−20.0, procedural −14.3, B gotcha −16.6 → FAIL; stale-claim not higher PASS. Cause: v2 proposed **0 closes** and applied 30 (A) / 13 (B) `scope=part` supersedes; the a65a8f5 read-side rule 3 moves a partly superseded item behind its superseder wherever that ranks (gold fell 1→48, 1→27). The 6a96ba1 statement-aware read side over the same links scores exactly the baseline on all 180 questions (neutral). Proposal precision (blind judge, 50 random r1 proposals): strict .48 (CI .35–.62), lenient .94 (D-076 strict .42); pooled with a replicate 100 → .54; partial supersedes .68, contradicts-only .12 (mostly real updates whose supersede a guard dropped: `supersedes_against_time`, `verifier_direction_disputed`); action tier .86, question tier .42. Throughput 4.23 jobs/min at concurrency 3 (6.9 calls/job; v1 ≈5.5). Spend $1.45 total. Consequences: (1) J merges only for its safety/liveness content (stranding, replay, widen, events) and must ship a read side that is at worst neutral (6a96ba1-style or stricter) — a65a8f5 rule 3 must not ship. (2) Stale-first is NOT fixed by partial links; a diagnosis of the 15 A / 16 B stale-first cases (link present? relation? which guard dropped it?) precedes any v3 judgement change. (3) Leaderboard branch eval-librarian-v2 merged (b6962f8). Data: docs/private/realdata-{yt,hlmemo}/results-librarian-v2/.
D-088 | 2026-09-24 | ACCEPTED (orchestrator, after dual review 58) | **English pivot (L: d5c8209 + d3ce0ac) is FIX-NEEDED before merge; policy decisions.** Both reviewers (astra-low, 5.6-sol xhigh) found no provider/cache/G5 leak or double fusion vote. Merged must-fix list: (HIGH) protected-token validator must be case-sensitive NFC exact for paths/identifiers/commands/flags/numbers incl. sign (no casefold, no flag splitting); translate must enforce the D-083 `librarian_cross_project=exclude` policy at enqueue and before every provider attempt; translate must cover every chunk (resumable batches) or record `partial`, never `accepted` with a full-body digest over a 16-chunk prefix; translate/rewrite jobs follow D-086 §2 (systemic hand-back row-only, ≤4 defer, one terminal `uuid5("job:<key>")` event) by rebasing onto merged J rather than re-implementing; 0009 downgrade refuses when rendition artefacts/jobs/events exist (operational rollback = W0a snapshot restore only). (MEDIUM) DF counts the canonical original unit once (union of original + rendition terms); HLM_RENDITIONS off stops enqueue AND execution of translate jobs; MCP instructions/schema advertise English queries and `index_en` only when the matching flag is on; the source cap is applied before the fetch cut (so alternatives exist) and documented as best-effort only when no alternative exists in the window. **Policies:** (1) Previews and evidence are always the ORIGINAL text; an English rendition is returned only as a separate non-authoritative `hint_en` (the validator cannot see negation/tense/modality flips). (2) Renditions are a rebuildable retrieval projection, not user data: writing them in the OBSERVER role does not violate D-074 (Sol position adopted over astra's shadow-index proposal), but they only affect ranking when HLM_RENDITIONS is on. (3) HLM_RENDITIONS stays off in production until the hold-out quality thresholds (Sol 53) AND G-L3 p95 ≤500 ms at high rendition coverage pass; first mitigation to measure: lower rendition candidate budgets (PRE_FACTOR, V_MAX) and/or drop the rendition vector leg. (4) 0009 ships with a quiesced W0a deploy (writers stopped), not as zero-downtime. Consults: docs/consults/58-*.
D-089 | 2026-09-24 | ACCEPTED (orchestrator, after stale-first diagnosis + astra consult 59) | **Librarian v3 plan for stale-first; starts after J merges.** Diagnosis (pinned v2 run, deterministic, no LLM): none of the 15 A / 7 B stale-first pairs has an applied supersede; 8/15 pairs never reach the relate prompt (candidate caps SAME_TOP 8 / DOC_TOP 3; doc_chunk↔episode never paired); 7 reach it but yield no proposal, a direction-less contradicts link, or a supersede dropped by `supersedes_against_time` / `verifier_direction_disputed`; on A the current item is outside the top-3 in all 8 cases (rank 4–28), so reordering fixes 0 — only an applied close (or pull-up) moves the gate. Ceiling with a perfect judge on today's candidates: A 8→4 (close). v3, in order, each measured as an ablation on the pinned hold-out: (1) **instrumentation** — export per pair: candidate source/rank/score, primary raw output, guard before/after, verifier request/answer, terminal outcome ∈ {judged_none, time_rejected, direction_disputed, verifier_rejected, schema/budget/timeout, proposed}, plus prompt/schema/profile/code/corpus hashes; (2) **time/direction evidence** — recorded_at is ordering only and commit/mtime is provenance only: neither vetoes or decides direction; a quoted, claim-bound explicit date may veto a supersede whose direction it clearly contradicts (day precision ≠ hour order); with equal/unknown time, direction is accepted only from a quoted explicit replacement/revocation plus independent verifier agreement (verifier blind to the primary result, order-independent A/B mapping, no automatic direction flip); remove the chronology hint from the relate/verify prompts; (3) **owner-approved close** for a verified whole-scope supersede (no auto-close, incl. single-statement items), close time = trusted effective date if inside validity, else the owner-approved "now"; close+link atomic, version-checked, replayable, reversal by compensating event; (4) **targeted candidate expansion** — a separate bounded quota for same source section/decision id first, then doc_chunk↔episode, then the 24/8 cap ablation (cost: relate 2→4–5 calls/subject; measure $/latency). **No read-side pull-up** in ranking (neutrality cannot be guaranteed under wrong links); the 6a96ba1 read side stays. Gates unchanged: A stale-first ≤4/15, temporal L2 ≥.400, G-E-W2b ≥+3 with no category <−3 and no stale-claim increase, observer zero mutation, D-076 precision/false-invalidation thresholds. Honest risk: the ceiling equals the gate, so v3 may still FAIL; if so the next lever is retrieval (getting the current item into the top-K), not a gate change. Consult: docs/consults/59-*.
D-090 | 2026-09-24 | ACCEPTED (orchestrator; owner informed) | **Hold-out result of the English pivot (pinned d3ce0ac, prod temporal-rule import, budget 3000): the guarded QUERY REWRITE passes every Sol 53 threshold; the source cap and renditions do not earn their place yet.** Base reproduces D-057/D-087 exactly (A hit@5 .793, B evidence R@5 .451, pooled .643; B Turkish 17/36, English 28/36 in top-5). Rewrite only: Turkish 17→26 (+10/−1), English 0 lost, pooled .726 (+8.3), no category below 0 (up to +25), L2 A .772→.870 / B .556→.667, temporal L2 A .40→.60 / B .25→.31, negative AUC up on both, stale-first unchanged (A 8/15, B 6/16); 1 rewrite rejected by the guard per corpus; query p95 +25–30 ms (A 127, B 121 ms); spend $0.027. Cap: a no-op on the prod-rule import (A 131/288 items have `source`, no B source has >2 items); on a section-level `hlm import` of B (263 items) cap alone +1.4 pts (Turkish 12→14, FAIL) and cap+rewrite +12.5 vs rewrite +9.7 but stale-first 2→4 (decline FAIL); capped-away gold kept source hit and evidence R@5. Renditions: only 23 items needed one (A 9/288, B 14/91 — the corpora are mostly English already), and 0 were accepted: 18 rejected by the protected-token check (slash-joined Turkish words "x/y" treated as identifiers and translated by the model), 1 not English, 4 failed after retries on schema errors (58% of 129 translate calls were schema failures); G-L3 with renditions on PASS but thin (p95 471.8/427.4 ms vs 500). Run-to-run noise from fresh rewrites is ≤1.4 pts. **Decisions:** (1) R3 ships the query rewrite ENABLED (after the D-088 fixes and a re-measure on the final code with the stricter validator). (2) The source cap ships OFF; re-evaluate in W-O on section-level imports with a stale-first guard. (3) Renditions (slice 2, migration 0009) do NOT ship in R3: the translate path must first reach ≥90% acceptance on a Turkish-heavy sample with ≤5% schema failures (fix: pass the explicit protected-token list to the model, root-cause the schema failures, classify slash-joined natural words correctly while keeping the validator strict), then re-measure value and G-L3 margin; D-082 stays accepted and is sequenced, not dropped. Leaderboard: eval-pivot-l merged. Data: docs/private/realdata-{yt,hlmemo}/results-pivot-l/.
D-091 | 2026-09-24 | ACCEPTED (orchestrator; owner asked) | **Free trial endpoints (e.g. `nvidia/nemotron-3-ultra-550b-a55b:free` on OpenRouter) are never used on any path that sends memory content, and never as a librarian/risk/synthesis profile.** Verified 2026-09-24: OpenRouter `:free` limits are 20 req/min and 1,000 req/day for accounts with ≥10 credits purchased (ours: is_free_tier=false); the free endpoint is served by NVIDIA under the NVIDIA API Trial ToS, which says access is "for limited trial purposes only and without use of the API Service or Generated Content in production" (§1.2), NVIDIA collects "User Content and Generated Content to improve NVIDIA products and services, including AI models" and stores user content 30 days (§2.4/§3.3), and users must not submit "any confidential information … personal data" (§2.6a). The free endpoint also has no response_format/json_schema (live: 404 with require_parameters; forced tool-call worked, 12.8 s; plain JSON 2.9 s; 30-min latency p50 2.2 s / p99 49 s, ~19 tok/s). Allowed use: owner/dev experiments on public or synthetic data only (e.g. generating synthetic test data, second opinions on public code). The PAID `nvidia/nemotron-3-ultra-550b-a55b` ($0.60/$2.40 per M, structured outputs) is added as a fallback-bench candidate with data collection denied in its profile.
D-092 | 2026-09-24 | ACCEPTED (orchestrator; owner asked) | **Nemotron 3 Ultra (free) is not adopted as a development reviewer.** On the D-084/D-085 blind review bake-off (W2e, key K1–K6, max 12; astra-low 8, 5.6-sol 6, 6-sol 4) it scored 0–1/12 over 4 runs (codex agentic mode 0/1 at 18–22 min per run; a single 784k-token long-context prompt 0/0). It called a known DO-NOT-MERGE change "MERGE-WITH-FIXES" every time and produced up to 7 code-contradicted claims per run. D-085 stands; under the owner's rule (free quota, public or synthetic data only, occasional use) it stays available only for low-stakes tasks such as synthetic test-data generation. Side finding: in codex 0.155.1, `-s read-only` lets the model read the whole disk (incl. repo `.env` and `deploy/.local/`); BACKLOG item to run codex reviews from a clean export or with a restrictive permission profile. Evidence: docs/bakeoff/sol-5.6-vs-6/NEMOTRON-SCORING.md.
D-093 | 2026-09-24 | PROPOSED (orchestrator; changes owner-accepted D-066 → needs owner OK) | **Fallback models after the fallback bench v2** (bench/results/20260924-fallback-v2.md; `hlm bench` with D-066 settings, 3 reps, 600 calls/model; incumbents reproduce D-070: luna 95.4). Results (overall / $ per correct / p50–p95 s / schema-fail / G-LIVE-C worst rep): gpt-6-luna 95.4 / .00018 / 2.6–6.3 / 0% / PASS .05; gpt-6-luna-pro 95.4 / .00062 / 4.1–9.5 / 0% / **FAIL .15 (latency: 48/80 judged inside the 4 s cap)**; deepseek-v4-pro-0813 low 95.1 / .00362 / 9.0–50.7 / 1.3% / FAIL; qwen3.8-27b low 95.0 / .00202 / 6.2–38.8 / 0% / FAIL; glm-5.3-flash 92.4 / .00027 / 4.7–22.1 / 0.3% / PASS at the limit (.100, 4 reps); glm-5.3 91.0 / FAIL; qwen3.8-27b reasoning-off ("fast") 90.2 / .00097 / 2.3–18.2 / 1.0% / **PASS .050 (4 reps)**; deepseek-v4.1-flash (current fallback) 89.9 / .00038 / 0.8–5.8 / 0% / FAIL (D-071); kimi-k2.6 83.7 (9/27 injection compliance); nemotron-3-ultra paid 76.0. Pro tier, hardest family T9: luna-pro 92.6, deepseek-v4-pro low 90.8 (5.9× $/correct, p95 51 s, T12 p95 442 s), qwen3.8-27b low 85.4, glm-5.3 83.2. Every reasoning-on profile fails G-LIVE-C on LATENCY, not quality. **Proposal (per task class, via a small provider-agnostic config hook "fallback profile per task"):** (1) async librarian jobs → fallback glm-5.3-flash (+2.5 pts, 30% cheaper per correct than deepseek-v4.1-flash; slower, acceptable off the request path); (2) deadline-capped API tasks (synthesis, query_rewrite) → keep deepseek-v4.1-flash (only fallback fast enough for the deadlines); (3) risk_judge → qwen3.8-27b-fast replaces the retrieval-only fallback of D-071 (id-guarded judge only: it complied with 3/29 injections in T11, so never a general librarian); (4) Phase 5 pro tier (not built yet) → luna-pro primary with deepseek-v4-pro-0813 low as provisional fallback; luna-pro must never run risk_judge. Spend $10.29. Profiles merged unactivated (b98e3e5), each with data_collection=deny and require_parameters=true.
D-094 | 2026-09-24 | ACCEPTED (owner) | **D-093 accepted in full by the owner** ("Hepsini onayla"). This amends D-066 (single fallback) and D-071 (retrieval-only risk fallback). Implementation = workstream F: a provider-agnostic "fallback profile per task" config hook (default `HLM_FALLBACK_PROFILE` plus per-task overrides; model ids live only in profiles, D-017), with D-084 latency policy and per-model breakers preserved and D-071 retrieval-only behaviour kept whenever the configured risk fallback is not qualified. Production mapping: async librarian jobs → openrouter-glm53-flash; synthesis and query_rewrite → openrouter (deepseek-v4.1-flash); risk_judge → openrouter-qwen38-27b-fast (id-guarded judge only). The Phase 5 pro tier (luna-pro + deepseek-v4-pro low) is recorded, not wired, until Phase 5. F ships in R3 if its review and gates are done in time, otherwise in R4; the G-LIVE-C pass must be re-shown through the production wiring before activation.
D-095 | 2026-09-24 | ACCEPTED (orchestrator, after reviews 61/62) | **Question TTL is judged at the linearization point.** An accepted answer is applied only if the question is unexpired at the moment ALL locks the decision needs are held. The apply paths (worker `_apply_approved` and `memory.answer` direct apply) take locks in this order: item locks → policy recheck (D-083) → staleness/rebase → batch-row / rule / widen locks. They then perform a FINAL fresh-clock (clock_timestamp) TTL check immediately before event-id allocation or the answer-event insert, with no lock wait in between. If the question has expired, the worker marks it `expired` and applies nothing; memory.answer rolls back the whole transaction (including writes) with `E_VERSION_CONFLICT {expired}`. The sweeper cannot race these paths (question rows are FOR UPDATE; the sweeper uses SKIP LOCKED). Review 61 closed the D-077 stranding prerequisite (legacy `proposal.mutation` answers are picked up again; widen is never auto-applied); promotion itself stays gated by D-076/D-087 (observer only).
D-096 | 2026-09-24 | ACCEPTED | **J merged into main (6d87a27; int-r3-j-fix 9b93bea).** Contents: librarian judgement v2 + concurrency (N=3) + promotion-stranding fix + D-086 (widen owner-only; one terminal event + ≤4 defer; systemic hand-backs row-only), D-087 (read side = the neutral 6a96ba1 statement rule AND the clause rule; a65a8f5 rule 3 removed; rank_new ≤ max(baseline, 6a96ba1) property-tested over 3,000 cases), review-61 fixes (legacy proposal NULL strand, SCC cycles, unlimited out-of-span scan, pairwise replay mask) and D-095 (TTL at the linearization point). Reviews 56/57/60/61/62. Gates: full suite 1081 passed / 9 skipped on 9b93bea; gate-release on 6cf1492 G3 0.980, G4 p95 267 ms, G-L3 p95 367/384 ms (D-095 doesn't touch retrieval). The librarian stays OBSERVER (D-076/D-087); the D-077 stranding prerequisite is closed.
D-097 | 2026-09-24 | ACCEPTED (orchestrator, after dual review 64) | **Query rewrite (pivot-s1-r3 c91ee72) is FIX-NEEDED; it ships ON in R3 only after these fixes.** Both reviewers confirmed cache isolation, identical retrieval filters on the English branch, flag-off byte identity, cap-before-fetch and the D-087 rank bound (relative to the post-cap baseline). Policies: (1) **The rewrite guard is conservative: ambiguous means protected.** Every slash-joined token (B1's prose leniency does NOT apply to the rewrite guard), every quoted string incl. single-character and single/curly quotes, every backtick span, every code-ish token (contains / . _ - : = or digits), and command context (a known-CLI head plus its subcommand/arguments) must appear verbatim, or the rewrite is rejected. A missed rewrite is acceptable; a changed identifier is not. (2) **Credential phrases never leave:** a TR/EN credential keyword (parola, şifre/sifre, password/passwd/pwd, token, api key/anahtar, secret/gizli) followed by a value means no provider call at all. (3) The rewrite call uses the D-084 latency attempt policy, so the per-task fallback (D-094) can answer inside the deadline. (4) The deadline is absolute from enqueue (queue wait included). A failed schedule returns `unavailable`, never `pending`. (5) Rewrite spend is bounded per device/project with a stable lineage, and it cannot starve librarian jobs (a reserved share or a separate cap). (6) Partial-supersession demotion receives the union of original and English query terms. Accepted as-is: the query text itself (the caller's own words) may be sent even when it mentions device-scoped topics; stored device-scoped item text never is. Consults: docs/consults/64-*.
D-098 | 2026-09-24 | ACCEPTED | **F merged (wf-fallback-per-task d2d8a7f).** It adds `HLM_FALLBACK_PROFILE__<TASK>` overrides (env or `[hlm] fallback_profile__<task>`, resolved by profile_chain and applied per call by Provider.chain_for; generic by task name, no model id in code). An unknown profile fails fast at api and librarian startup when the librarian is enabled. Ops status shows the chain per task, and deploy/llm.env.example carries the D-094 mapping. Live G-LIVE-C through the production wiring, with the primary forced down, gives catch 1.000 and false-warn max .075 over 4 reps (PASS). After consult 63 the production profiles send `require_parameters=true`. gpt-6-luna and gpt-6-luna-pro drop `temperature`: verified live, no endpoint of theirs supports it, so with require_parameters the request returns 404. Recorded cassettes were re-keyed to the new request params rather than re-recorded, to keep the stored model decisions and goldens stable (805 keys, marked "rekeyed"); 38 live spot-check calls across every (model, task) pair returned 38/38 valid JSON. Gates: unit 594, full integration 517/0 failed, gate-release G3 0.980, G4 p95 274 ms, G-L3 p95 378/417 ms.
D-099 | 2026-09-24 | ACCEPTED (owner) | **Conditional production GO for R3 (owner, 2026-09-24): "if the VM rehearsal meets what was promised, go to prod when we reach the prod stage" — no further owner prompt needed for THIS release.** Scope: R3 only = main with J (D-096), F (D-098) and pivot-s1-r3 after its D-097 fixes; HLM_QUERY_REWRITE ON, HLM_RETRIEVAL_SOURCE_CAP OFF, HLM_RENDITIONS absent (slice 2 not shipped); librarian stays OBSERVER; llm.env per D-094. Facts: there is no alembic change and no compose/deploy.sh/Dockerfile change since R2 (6902f91), so R3 is a code-only upgrade (no schema change, no --accept-compose-change) and rollback is the W0a image rollback to R2. **"What was promised" = ALL of these must pass, else STOP and report to the owner:** (1) hold-out re-measure on final R3 main with rewrite ON meets the Sol 53 thresholds (Turkish top-5 ≥ base+4, 0 English lost, W-E ≥ +3 pts, no category < −3, no decline in evidence R@5 / L2 / temporal / negative AUC); (2) on the 2 vCPU VM rehearsal (upgrade from an R2-state copy): gate-release G3 ≥ 0.90, G4 within its bound, G-L3 p95 ≤ 500 ms under write load with the librarian ON (observer) and rewrite ON, all 8 remote gates PASS, live G-LIVE-C PASS through the production fallback wiring, G-LIVE-B/D as in R2, observer = 0 librarian mutations, the rollback to R2 rehearsed and working; (3) the final R3 code passed its reviews (D-085) with no open HIGH finding. After the prod deploy: prod remote gates plus an e2e re-run; any regression → roll back to R2 and report. `deploy.sh --accept-release` (deleting the pre-W0 backups) remains a SEPARATE owner OK.
D-100 | 2026-09-24 | ACCEPTED (orchestrator) | **Amends D-089 §2 (time evidence) after v3's G-LIVE-B FAIL.** v3 (wf-librarian-v3 5f5555b, all flags on) failed G-LIVE-B where v2 passed: worst of 3 reps, positive recall .944–.972 → .833, direction 1.0/.938/1.0 → .688/.625/.688, close recall .947 → .737. Safety stayed at 0 false supersede, 0 false close and 0 false cross-project. Cause: D-089 said "neither recorded_at nor commit/mtime decides direction", and v3 also treated an EVIDENCED valid_from as untrusted, so gold cases whose direction rests only on validity time (g04/g19/g35, ext e12) became undecidable. **Rule:** under D-072, valid_from comes only from explicit evidence (a frontmatter date, a dated decision row or heading, or a client-asserted valid_from on write); otherwise it defaults to the write/import time. A valid_from is therefore **evidenced** when it differs from the item's recorded_at by more than 5 minutes. When BOTH items carry evidenced valid_from values that differ by at least the coarser evidence precision (at least 1 day for date-only evidence), that order MAY decide supersede direction, still subject to the verifier. Default valid_from (= recorded_at), recorded_at itself and provenance dates (commit/mtime in `source`) never decide direction. A quoted, claim-bound date keeps its veto. Storing the evidence kind explicitly is a follow-up (a migration, not before R4; BACKLOG). Consult numbering: v3's routine review files `63v3-*` are renumbered to 65-*.
D-101 | 2026-09-25 | ACCEPTED (orchestrator) | **v3 with D-100 passes G-LIVE-B; the date-conflict rule is confirmed.** wf-librarian-v3 0757c4b (main merged): all 12 G-LIVE-B runs PASS on the unchanged frozen gold (4 configs × luna/deepseek/chain, worst of 3 reps). All v3 flags on: luna .972/1.00/1.00/.895, deepseek .972/.972/.938/.895, chain .972/1.00/1.00/.895 (pos recall / precision / direction / close recall), with false supersede and false close 0 everywhere; v2 reference .944–.972 / .972–1.00 / .938–1.00 / .895–.947. G-P1 v3 exact .989. Spend $0.64. **Rule (clarifies D-100):** when both items' evidenced valid_from values contradict the model's proposed direction, the supersession is dropped and a CONTRADICTION question goes to the owner, even if the text carries a change quote; the direction is never flipped automatically. Known limits: every evidenced valid_from is treated as date-only (≥1 day apart) until the evidence kind is stored (a migration, R4+). Next: a CRITICAL dual review (the close and reversal paths touch data integrity) and a hold-out ablation (G-E-TEMP / G-E-W2b) per flag, both before any merge. v3 is not part of R3.
D-102 | 2026-09-25 | ACCEPTED (orchestrator, after dual review 67) | **The rewrite gates move from deny-lists to allow-lists. The deny-list approach leaked in three consecutive review rounds.** Review 67 (both reviewers) closed items 3–6 but found open HIGHs:
- credentials still pass: `DB_PASSWORD=hunter2`, "gizli anahtar hunter2", "şifrem çiçek", "parola çilek", `HTTPS://user:pass@host`;
- command/quote changes are still accepted: `git -C "/tmp" status`→`log`, `./manage.py migrate`→`flush`, `bun install`→`update`, `"x"`→`x`, an added `git log`, `src/main`/`src/other` swapped.
Rules (the query rewrite is optional, so declining a rewrite is always safe):
(1) **Send gate:** NO provider call if the query contains ANY of the following:
- a credential-lexicon stem anywhere, matched case- and diacritic-insensitively with Turkish suffixes allowed (parola, şifre, gizli, anahtar, kimlik bilgi, password, passwd, pwd, secret, token, apikey/api key, credential, bearer, auth, private key);
- any `NAME=value` or `NAME: value` assignment;
- any URL (any `scheme://`, case-insensitive);
- any high-entropy token of 16 or more mixed alphanumeric characters.
(2) **Guard allow-list:** every token of the original that is NOT a recognised natural-language word of the query's language must appear verbatim in the rewrite, as the SAME multiset in the SAME order, with quote delimiters preserved. The rewrite may add NO new protected token. Recognition uses a bundled wordlist per supported source language (TR, DE), ASCII-folded, with Turkish suffix stripping. It is deterministic data, not a model call, so D-017 holds. Unknown words are therefore protected.
(3) Latency mode: on the first schema failure, switch to the next profile.
(4) The genericity test becomes AST/function-scoped.
Acceptance on the synthetic TR set (with and without diacritics) is re-measured. The hold-out re-measure (D-099 criterion 1) runs on the final code. Merges into pivot-s1-r3 are three-way merges of current main, never snapshots.
D-103 | 2026-09-25 | ACCEPTED (orchestrator) | **The librarian v3 hold-out ablation (pinned 0757c4b, prod-rule import, assistant + approve-all ×1, budget 3000) leaves stale-first unchanged; the third librarian-quality attempt fails.** C0 (all flags off), C1 (TEMPORAL), C2 (+CLOSE), C3 (+SECTION_TOP=4, DOC_EPISODE) and C4 (+24/8 caps) ALL score exactly the no-librarian baseline, which reproduces D-087 (A hit@5 .793, B evidence R@5 .451): A stale-first 8/15 (gate ≤4, FAIL), B 6/16, temporal L2 .400, G-E-W2b +0.0 (FAIL), stale-claim unchanged, 0 closes, 0 pairs fixed, 0 broken.
Why:
- In C2–C4 the model judged a supersede whole-scope exactly once, and that proposal was dropped. The stale items are multi-statement memories, so every supersede ends partial and the owner-approved close never fires.
- Under the prod temporal rule, few items carry an evidenced valid_from (A: the 157 git-day items; B: 2 of 91), so TEMPORAL mostly yields `time_no_direction_evidence`.
- Larger caps improve reach (not-candidate pairs 4 → 1), but the newly reached pairs end judged-none or time-rejected.
Proposal precision (blind judge, 40 C2 proposals): strict .175 (CI .09–.32), lenient .925; 36/40 are contradicts-only, most of them real updates proposed as bare contradictions (v2 was .48). Throughput: 5.1–5.2 jobs/min for C0–C2, 3.96 for C3, 1.82 for C4 (17.4 calls/job). Spend $2.56. Leaderboard merged; data in docs/private/realdata-{yt,hlmemo}/results-librarian-v3/.
Consequences: (1) v3 is NOT promoted and the observer role stays (D-076). (2) The pair-level approach (link two whole items) cannot fix stale-first on multi-statement memories. The next step is a strategy decision (memory granularity, retrieval, or how the gate measures a supersession clue), taken with a design consult and the owner, not another v4 of the same approach. (3) R3 is unaffected.
D-104 | 2026-09-25 | ACCEPTED (orchestrator; the architecture decision is reserved for the owner after the pilot) | **Stale-first strategy after D-103: a ceiling pilot first, then an owner decision between memory granularity (A) and span-bound revision (B).** Consult 68 compared three positions:
- orchestrator: A then B (written before reading the consults);
- gpt-5.6-sol: A, then C, then B;
- astra-low: B, then A.
All three agree that no pair-level v4 is built, G-E-TEMP stays ≤4/15 unchanged, production stays observer, and a ceiling pilot of ≤1–2 days runs before any architecture change. Any clue-aware answer metric (C) may only be ADDED, and only as an owner-approved second gate ("served stale error ≤4/15", false-current-clue ≤.02), never replacing the raw gate.
**Pilot (disposable copies, prod-rule import, rewrite state fixed, budget 3000):**
- Arm A0: atomic claim blocks from a FROZEN deterministic splitter built without seeing the hold-out. The parent is kept verbatim as provenance; children carry derived_from, span offsets and the splitter version.
- Arm A-oracle: A0 plus a close of only the labelled stale child. This is an architectural ceiling, not a quality claim.
- Arm A-v3: A0 plus the real v3 C2 librarian, approve-all.
- Arm B-oracle: a span-bound revision of the stale item, replacing only the outdated span with the current value; unchanged spans are kept verbatim and the old version stays in history.
Measures: A/B stale-first, G-E-W2b per category, G3 (≤1 pt drop), G4/G-L3 (p95 ≤500 ms), and a blind split audit (no fabrication or negation loss, coverage of the parent text).
**Decision rules:**
- If no oracle arm reaches ≤4/15, both are falsified; go to targeted retrieval (D) or accept observer (E).
- If an oracle arm passes but its real arm fixes fewer than 2 cases, the bottleneck is judgement/direction and there is no full rollout.
- Otherwise the owner chooses the architecture.
Consults: docs/consults/68-*.
D-105 | 2026-09-25 | ACCEPTED (orchestrator) | **Librarian v3 is code-complete and review-closed (wf-librarian-v3 e9b5508), but held OFF main until R3 is released.** All 9 findings of critical review 66 are fixed, each with a regression test that fails on 0757c4b: D-101 double veto, close linearization for every segment type, the future cut aligned with the link start, per-question reversal with a dependency refusal, source/code_refs kept on CLOSE/REOPEN, cross-project export masking with an audited opt-in, `--out` created 0600/O_EXCL/O_NOFOLLOW, budget hints only on incomplete pairs, and the section pool cut after cosine ordering. Routine check 69: OK with no new defect. G-LIVE-B with all flags passes 3 reps × luna/deepseek/chain ($0.20). Reason for holding: D-099 scopes R3 to J+F+pivot-s1, and v3's always-on instrumentation changes the write path and the event payloads. v3 merges into main after the R3 prod deploy, with every flag default off. It remains the librarian code for the D-104 pilot's "A-v3" arm. Consult: docs/consults/69-*.
D-106 | 2026-09-25 | ACCEPTED (orchestrator, after dual review 70 — the 5th rewrite round) | **The query rewrite becomes MASK-AND-TRANSLATE: protected tokens never reach the model, so they cannot be changed and cannot leak.** Review 70 (both reviewers FIX-NEEDED) again found accepted rewrites that change commands, operators, quotes and technical words (`git add .`→`git add`, `&&`→`||`, `false`→`true`, `main`→`master`, `"prod db"`→`"prod new db"`), plus send-gate misses (`ÖZEL=hunter2`, `"pin": "1234"`, `Mein Passwort …`, `p a r o l a hunter2`, `passw0rd hunter2`, bare `hunter2`). Validating free LLM output after the fact is an open-ended game.
**New design:**
1. Tokenize the query. A token is TRANSLATABLE only if it is a word of the query's source language (bundled TR/DE lists, ASCII-folded, TR suffix stripping) AND NOT an English word, a CLI head, a code-ish token, punctuation/operator, number, or quote/backtick content. Every other token is replaced by a placeholder ⟦Pn⟧, and contiguous protected runs, quote spans and command spans are masked whole.
2. Only the masked text goes to the provider.
3. The model must return English containing every placeholder exactly once and in order. Otherwise the rewrite is rejected.
4. The server re-inserts the protected tokens verbatim.
By construction, protected tokens and any non-dictionary string (including bare secrets like `hunter2`) never leave the server and cannot be altered. The send gate stays in front of it as defence in depth:
- credential lexicon incl. DE stems, with leetspeak and letter-spacing normalisation;
- Unicode and quoted assignment keys;
- PIN keyword, Luhn-valid card numbers, IBAN;
- URLs;
- high-entropy tokens.
**Release wiring (D-099):** `deploy/llm.env.example` and install_llm_env set `HLM_QUERY_REWRITE=true`, and a post-deploy status check asserts the flag is live.
**Verification:**
- Property tests over generated queries: (a) the provider payload contains only source-language dictionary words, placeholders and the fixed prompt; (b) the output equals the model text with every protected token restored verbatim.
- All earlier reviewer counterexamples as regression tests.
- Acceptance and the hold-out re-measured on the final code.
- Release-gating dual review scoped to these two properties and the send gate.
Residual (documented): a dictionary word that doubles as an identifier (`worker`, `query`) may be translated. It is harmless, because the original query is always searched as well and the English branch only adds candidates.
D-107 | 2026-09-25 | ACCEPTED (orchestrator) | **Interim R3 re-measure on 97344eb (D-102 allow-list guard) FAILS D-099 criterion 1, and it does not ship.** Paced prewarm, prod-rule import, D-094 fallback chain; 2 runs.
| Criterion | Result | vs D-090 |
|---|---|---|
| Turkish top-5 | 21/36 (exactly at the bar) | 26/36 |
| English new misses | 0 | — |
| Pooled W-E | +3.4 / +5.2 | +8.3 |
| Worst category | A fact/config −4.6 in both runs (FAIL) | — |
| Stale-first | +1 in each run | — |
Every hit lost vs D-090 comes from guard rejections ("protected token": 13/8 on A, 6/5 on B; "unchanged": 2–3 per corpus); the credential send gate cost none. Production-relevant finding: a burst of ~50 non-English queries times out in the rewrite queue (2 in flight, deadline counted from enqueue), those local timeouts OPEN the provider breaker, and rewrites stay disabled for ~60 s. Fixes are folded into the D-106 round:
- queue/admission timeouts never trip the breaker;
- early `unavailable` when the queue wait would exceed the deadline;
- configurable concurrency, default 4;
- a tunable `HLM_QUERY_REWRITE_BRANCH_WEIGHT` (default 1.0) so the English branch's RRF weight can be tested at 0.5–0.8 against the q002-type rank loss.
The final gate is the re-measure on the D-106 code. Leaderboard eval-r3-rewrite merged (interim labels).
D-108 | 2026-09-25 | ACCEPTED (orchestrator) | **R3 release order for llm.env (found in the rehearsal prep).** R2 code cannot load the D-094 llm.env: `profile_chain` fails with "profile 'openrouter-glm53-flash' not found", because the new profiles ship only in the R3 image. The R2 librarian would not start. Order for the VM rehearsal AND production:
1. Deploy the R3 image with the R2 llm.env still installed.
2. Verify.
3. Switch to the D-094 llm.env (`HLM_QUERY_REWRITE=true`, per-task fallbacks) and restart.
4. Verify again, including a status check that rewrite is ON.
Before any rollback to R2, reinstall the R2 llm.env FIRST, then roll back the image. The VM `hlm-2604` is at R2 = 6902f91 with realistic data (the 51 HLMemo docs → 247 items in `r3-base`; `r3-load`; the G-LIVE-C risk world). The rehearsal checklist is docs/status/R3-REHEARSAL.md (f74148f). main was pushed to origin (f74148f) after a gitleaks scan of the 81 new commits (clean); docs/private is gitignored.
D-109 | 2026-09-25 | PROPOSED (orchestrator → owner decision per D-104) | **D-104 pilot result: B (span-bound revision) has the best ceiling at near-zero collateral. A (atomic children) is rejected on collateral. Rule 2 fires: the bottleneck is judgement, not representation.** All arms ran on disposable copies with a prod-rule import, rewrite OFF and budget 3000. Base reproduces D-087/D-103 question by question.
| Arm | A stale-first | B stale-first | Temporal L2 | G-E-W2b | Worst category | A hit@5 | B evidence R@5 |
|---|---|---|---|---|---|---|---|
| Base | 8/15 | 6/16 | .400 | ±0 | – | .793 | .451 |
| A0 (split only) | 4/15 | 5/16 | .467 | −7.9 | B decision_rationale −40.6 | .750 | .326 |
| A-oracle | 1/15 | 1/16 | .467 | −7.6 | −34.4 | .728 | .361 |
| A-v3 (real, partial: OpenRouter credits ran out after 1,103/1,564 A jobs, none of B) | 4/15 | – | – | – | – | – | – |
| **B-oracle** | **0/15** | **0/16** | **.867** | **+0.3** | −9.4 (partly a scoring artefact) | .793 | .458 |
A0's apparent 4/15 comes from the stale key dropping out of the drill window, not from the current value surfacing.
- **A-v3:** 0 closes. All 36 whole-supersede proposals were dropped: 17 for missing time evidence, since children inherit an un-evidenced valid_from; 7 verifier rejections; 11 downgraded to partial. In 8 stale pairs the two sides never met in the same candidate list (siblings crowd it).
- **Atomicized G3 world:** 2,400 items → 125,713 children. Parent-credited R@5 .980, raw .930, G4-shaped p95 1,158 ms (FAIL vs 500).
- **Split audit:** 1–2 of 40 lose meaning (.025–.05); coverage .9997.
- **B-oracle:** 108 span revisions, byte check 108/108, no index growth, latency ≈ base.
- Pilot spend: $2.28.
**Proposal:** adopt B as the stale-first mechanism and do NOT pursue A (−7.9 W2b, 21× items, G4 fail). Next, build and measure **B-real**: the librarian proposes a span revision {stale span quoted verbatim from the old item, replacement quoted VERBATIM from the newer item, evidence}. Deterministic guards check both quotes byte-exact. The owner approves (assistant role); the observer never mutates. It is measured on the same pilot harness against the B-oracle ceiling (0/15). This needs the owner's OK, per D-104.
D-110 | 2026-09-25 | ACCEPTED (owner) | **D-109 accepted: pursue B-real (span-bound revision) and drop A (atomic children). Production and development get SEPARATE OpenRouter keys.**
**B-real** builds on the librarian v3 code, using the pilot branch pilot-d104-av3 (v3 + pilot hooks + the `revise` primitive). A v3 partial supersede becomes a REVISE_SPAN proposal with these fields:
- `old_span`: quoted byte-exact from the old item's current version, and unique in it;
- `replacement`: quoted byte-exact from the newer item;
- evidence quotes, and the direction per D-100/D-101 with the verifier's agreement.
Deterministic guards re-check both quotes byte-exact before a proposal is emitted and again at apply time.
Apply path: only after the owner's answer in the assistant role (the observer never mutates, D-074). Apply creates a new version of the old item that differs only in that span, plus a supersedes link from the new version to the old one; it is bi-temporal and replayable. The locks follow J/D-095. Reversal is a compensating event.
Measured on the D-104 harness against the B-oracle ceiling (A 0/15). Pass needs A stale-first ≤ 4/15, G-E-W2b ≥ +3 with no category < −3, and precision tracked. The live measurement waits for OpenRouter credits.
**Keys:** the owner creates a production-only key. It is installed on the VPS llm.env during the R3 deploy (D-108 order). The existing key stays for development and agents. BACKLOG: reconcile the spend, since billed cost was about $66 against about $18 reported.
D-111 | 2026-09-25 | ACCEPTED (orchestrator, after release-gating review 72) | **Mask-and-translate holds as a design, but its boundaries leak. The implementation is hardened by canonicalization and strict boundaries, and the R3 release tooling gets env-aware rollback.** Review 72 (astra FIX-NEEDED, sol DO-NOT-MERGE) reproduced these holes:
- payload leaks through apostrophe tails (`hata'myprivatevalue`), multi-line and unclosed quotes, quote-glued tokens (`XX"foo"hata`), and zero-width/homoglyph/spacing+leet credential obfuscation (`pa​rola çilek`, `p @ s s w 0 r d`);
- restore accepts glued placeholder text (`⟦P1⟧production`) and repeats;
- the breaker exemption triggers on any positive `queue_wait_s`;
- check_librarian PASSes an R3 cutover with llm.env missing or rewrite off;
- **rollback.sh starts the R2 image with the R3 llm.env**, whose R3-only profiles R2 cannot load. This affects ANY R3 deploy, because F added profiles.
Fixes:
1. Input canonicalization: NFC; any Cf/zero-width/bidi control character means no rewrite; mixed-script tokens are masked; the credential check runs on a fixed-point normalisation (collapse single-character runs, leetspeak, confusable folding).
2. An apostrophe token is translatable only with a dictionary base plus a suffix from a closed Turkish suffix list; otherwise the whole token is masked.
3. A stateful quote scanner masks multi-line spans, masks unclosed quotes to EOF, and masks glued tokens whole.
4. Restore requires the same boundary class on each side of every placeholder, and rejects glued or repeated protected text.
5. Breaker exemption only when the queue wait causally cut the attempt budget below the minimum.
6. The R3 cutover check requires llm.env to be present and rewrite=true.
7. llm.env becomes part of the release state: deploy.sh snapshots it, and rollback.sh restores the previous env atomically before starting the previous image.
All reviewer inputs become regression tests, and the property generators are extended per class. The unit count must be reported exactly: collected, passed, skipped.
D-112 | 2026-09-25 | ACCEPTED (orchestrator) | **Spend reconciliation: the product ledger is exact per call; the ~$48 gap came from tooling and shared-key users.** wf-spend-reconcile @ 1025d37. Every answered call records OpenRouter's `usage.cost` exactly, reasoning tokens are already inside completion_tokens, and timeouts and cancellations are charged the worst case. Under-count paths found:
- **`hlm bench`, eval/live run.py / run_w2b.py and the G-LIVE-C/D tests** kept their rows in memory and saved them only at the end, so killed, aborted or re-run runs left no record. This is the most likely bulk: $0.03–3.5 per lost run.
- **Realdata evals** summed spend before run_eval, then dropped the DB, so API-side rewrite/prewarm/synthesis calls were lost: cents per run.
- **The legacy bench** costed only the final attempt.
- **Edge cases:** a $0 worst case with the guard off, and lost rows (a raising validator, a cancel between answer and settle).
Other users of the same key (ad-hoc probes, embedding benches, the VM rehearsal's R2 librarian backlog) account for the rest; confirming the split needs the OpenRouter activity export (a management key).
Fixes:
- each ledger row records its cost source, with a billed-unknown flag;
- the worst case is charged even with the guard off;
- the lost-row paths are closed;
- each profile names its cost field (`usage_cost_field`, D-017);
- EVERY LLM-calling tool appends each attempt immediately to a JSONL spend log (`HLM_SPEND_LOG`).
Test DSN guard: DB tests require HLM_TEST_DSN (collection stops with exit 4 before touching any DB), unit tests run without it, `make test` refuses without it, `make test-unit` is added.
Tests: 26 spend-accounting tests and 6 guard tests. Gates: unit 626, integration 517/13 skipped.
**Held OFF main until R3 ships** (D-099 scope). Still to do: a routine check, then a merge; the query_rewrite provider needs the same one-line ledger wiring after pivot-s1-r3 merges. Operational follow-up: separate keys per environment (D-110).
D-113 | 2026-09-25 | ACCEPTED (orchestrator; owner may override) | **B-real is built; REVISE_SPAN excludes historical-record kinds by default.** wf-b-real @ 0d7a1a8, flag `HLM_LIBRARIAN_REVISE_SPAN` (default off; requires V3_TEMPORAL), prompt `revise_span/v1`, no migration: the proposal is a contradiction question carrying one `version_revise` action. Guards run at proposal AND apply time: old_body_nfc, old_span_unique, span_word_boundary, span_not_whole, replacement_found, replacement_differs, length_ratio, span/replacement in the judged statement, replacement_visibility. Also required: D-100/D-101 direction, blind verifier agreement, partial scope, same project, privacy gate, D-083 policy. The implementer's own dual review (consult 71) found 3 HIGH + 1 MEDIUM, all fixed and checked OK. Gates: unit 811, integration 574, G3 0.980, G-L3 p95 380/376 ms (quiet rerun). G-LIVE-B with the flag on passes 3 reps on luna/glm53-flash/chain; REVISE_SPAN proposed 4/4 gold partial supersessions with precision 1.00 (small n). Live spend $0.23. **Policy:** episodes and decision/ADR rows are historical records, so REVISE_SPAN never rewrites them. Revisions apply only to living-knowledge kinds, via the config `HLM_LIBRARIAN_REVISE_KINDS`, enforced at proposal and apply time. Imported items: an unchanged re-import keeps the revision (source.sha256 unchanged); a changed source file overrides it (the file is authoritative). Next: the D-110 hold-out measurement (B-real arm on the D-104 harness, ≤ $3).
D-114 | 2026-09-25 | ACCEPTED (orchestrator, after release-gating review 73) | **The rewrite tokenizer DECLINES on ambiguity, and the release-gating severity rubric is fixed.** Review 73 confirmed as OK: canonicalization, the breaker, the cutover check and env-aware rollback (sol: claims 1, 4, 5, 6 OK). It left ONE HIGH class: in Turkish the apostrophe and the single quote are the same characters (' ’), so `‘İstanbul’un üretim bağlantısı’`, `XX'…'`, `f'…'` and escaped `\"` make the quote scanner expose quoted content that an accepted rewrite can then change. It also left MEDIUM restore gaps: clitic/inflected repeats (`worker's`, `workers`), a possessive glued to a placeholder (`⟦P1⟧’s`), and İ/I folding.
**Principle:** the tokenizer must be CERTAIN. For single-quote-family characters:
- an apostrophe is valid only when it is glued to a word, followed by a suffix from the closed TR list, and then a word boundary;
- a quote pair is valid only when the opener sits at a word start and the closer is followed by a non-letter;
- ANY single-quote-family character that is neither is ambiguous, so the query is NOT rewritten: zero calls, outcome `rejected: ambiguous_quote`;
- escaped (`\`) or prefix-glued (`f'`) quotes are ambiguous.
Restore compares on `fold()`, the same folding as masking. English clitic and inflection forms of a hidden protected token (`'s`, `s`, `es`, `ed`, `ing`) count as repeats. A placeholder followed by an apostrophe or clitic is glue and is rejected.
A declined rewrite costs nothing (the original query is always searched).
**Severity rubric for the rewrite in release-gating reviews:** HIGH means a non-dictionary token or a secret reaches the provider, or an ACCEPTED rewrite changes, duplicates or re-roles protected text. A decline or rejection on an ambiguous input is not a finding. Changes to dictionary words that also serve as identifiers are the documented residual (D-106).
D-115 | 2026-09-25 | ACCEPTED (orchestrator; STOP-and-report per D-099) | **The R3 final candidate 63bc041 FAILS D-099 criterion 1, so the query rewrite does not ship ON as built.** Paced prewarm, 2 runs at weight 1.0, plus runs at weights 0.7 and 0.5:
| Criterion | Result (bar) | D-090 | interim 97344eb |
|---|---|---|---|
| Turkish top-5 | 18–19 (≥21) | 26 | 21 |
| Pooled | +1.8 / +2.5 (≥+3) | +8.3 | +3.4 / +5.2 |
| Worst category | A fact/config −4.6 (bar −3) | – | – |
| Applied rewrites | 27/20 | 48/38 | 28/29 |
Lower branch weights are worse, so weight 1.0 stays. The safety hardening (D-097 → D-111) preserved privacy, and the send gate cost 0 hits, but every lost hit is a REJECTION: placeholder-restore failures (the model does not return ⟦Pn⟧ intact; 11 on A, 8 on B), protected_repeat and unmasked_token, plus 1–4 timeouts. The burst fix works (0 breaker opens). D-114, now in progress, adds more declines. **Next design, proposed: SEGMENT TRANSLATION.** Send only the maximal runs of translatable dictionary words, as a JSON array; the model returns an array of the same length; the server reassembles with the protected tokens verbatim in place. There are no placeholders in the model output at all. Invariant A (payload = dictionary words only) is unchanged; invariant B becomes a deterministic assembly, plus a per-segment repeat check that rejects only the offending segment. The English is not grammatical across protected tokens, which does not matter for retrieval. **Release options for the owner:** (a) ship R3 now WITHOUT the rewrite (J + F + env-aware rollback/cutover tooling, rewrite flag OFF; the cutover check validates the flag against the release manifest), and the rewrite follows in R3.1 after the segment redesign passes; (b) hold R3 until the rewrite passes. Orchestrator recommendation: (a).
D-116 | 2026-09-25 | ACCEPTED (owner) | **R3 ships WITHOUT the query rewrite, and the rewrite is SHELVED. The B-real hold-out fails too.**
**Owner decisions (answering D-115):**
1. R3 = J (librarian fixes, D-096) + F (per-task fallbacks, D-098) + the env-aware release tooling from D-111: llm.env becomes part of the release state; rollback restores the previous env before starting the previous image; the cutover check validates llm.env against the release manifest, with rewrite expected OFF. The rewrite and cap code does NOT ship. Branch pivot-s1-r3 is archived, not merged.
2. The rewrite is shelved. Resources go to the librarian (B-real) and Phase 3.
**D-099 amended for this R3:**
- Criterion 1 is replaced by "no retrieval regression vs R2 on the hold-out". The 63bc041 flags-off base already reproduced D-090 exactly (pooled .643), and J's read side is proven neutral (D-087). The final SHA gets an LLM-free retrieval re-check ($0).
- Criteria 2 and 3 stand, with the rewrite OFF on the VM; criterion 3 now covers the ported tooling.
**B-real hold-out (D-110 bar):** run on ed8deda (with the D-113 kinds default).
| Arm | A stale-first | B stale-first | W2b |
|---|---|---|---|
| B-real | 8/15 | 6/16 | ±0.0 |
| B-oracle | 0/15 | 0/16 | +0.3 |
Only 2 revisions were applied, neither on a labelled stale pair, and both were judged wrong by a blind judge (0/2). Upstream, v3 finalised only 9 (A) / 5 (B) partial supersedes. Outcomes of the A stale pairs: 6 judged none, 4 never candidates, 2 proposed (1 rejected by `replacement_in_judged_statement`), 1 time-rejected, 2 with no stale item. D-113 puts 3/14 A and 7/19 B stale items out of scope (historical kinds or decision records). Spend $0.45. **This is the 4th librarian attempt that does not move stale-first. The mechanism (B-oracle 0/15) works, but the librarian does not find or judge the pairs.** Next: an offline diagnosis of the "judged none" and "never candidate" pairs from the v3 export, before any new build.
D-117 | 2026-09-25 | ACCEPTED (orchestrator; direction decision to the owner) | **B-real diagnosis: the post-hoc pairwise librarian cannot fix stale-first on long imported documents at the D-100/D-101 safety level.**
**Dominant cause:** relate reads only the start of each item (3,000 characters of the subject, 1,500 of each candidate), while the stale or current statement often sits beyond that (items of 2.8k–38k characters). Both labelled statements were visible in only 5/16 A pairs and 6/21 B pairs. A-pair classes:
- 6 statement not visible;
- 3 never candidates (same-project cap of 8, counterpart at rank 11–50; one is top-5 by vector similarity alone);
- 1 doc_chunk↔episode never paired;
- 1 correct partial supersede wrongly rejected by the 2-word quote rule of `replacement_in_judged_statement`;
- 1 correct partial supersede rejected by the D-100 time rule (item-level dates not bound to the statement);
- 1 bare contradiction on another statement;
- 2 with no stale item.
**Hypothesis tests** (luna, $0.02):
- a similarity-centred statement window: 0/6;
- a per-statement prompt: 0/6;
- even ORACLE windows centred on the labelled statements: conflicts found in 9/12 calls, but only 2/6 pairs became correct partial supersedes and 1/6 survived the guards (no direction evidence, or judged compatible).
Estimated gain from statement-level relate + fixing the quote rule + a guaranteed candidate slot for vector-only hits: about 2–4 A pairs. That still misses ≤4/15 unless the direction rules are relaxed, which trades against safety. Directions for the owner are listed in the next decision.
D-118 | 2026-09-25 | ACCEPTED (owner) | **Stale-first moves to WRITE-TIME supersession by the writing agent. The post-hoc librarian judgement is no longer the primary mechanism.**
When an agent writes a memory that updates or corrects something it saw (typically in a memory.query result it just read), `memory.write` may carry `updates`: [{`item` (logical id), `expected_version`, `old_span` (verbatim quote from that version), `mode` ∈ {revise, supersede}}].
- **revise:** replace `old_span` with a `replacement` that must be a verbatim quote of the NEW memory being written. This is the D-110 invariant, now grounded in the writer's own text.
- **supersede:** a whole-item close plus a link.
**Server rules:**
- Deterministic validation inside the SAME write transaction: capability and project scope, D-083 policy, the version matches `expected_version` (else `E_VERSION_CONFLICT`, and the new memory is written without the update, with a hint), `old_span` occurs exactly once with word boundaries and is not the whole item, and the replacement is found in the new body.
- The D-113 kinds rule applies: historical kinds (episodes, ADR/decision rows) get only a supersedes link, never a text revision.
- The apply path is B-real's (a new version, question/event tagged, replayable, reversible by a compensating event). The locks and linearization follow J/D-095.
- A write comes from the owner's authenticated device, so this is an OWNER action. No librarian autonomy is involved, and the observer still only labels.
- Tool guidance and schema must fit G-SURF (≤ 3000 tokens).
The imported legacy documents that the hold-out measures stay a Phase 3 (consolidation) problem.
**Measurement:**
- deterministic integration tests for the server path;
- a small synthetic benchmark of client-LLM behaviour: given the query results and a new fact, does the client fill `updates` correctly (precision/recall)?
- the stale-first metric for this regime, on a synthetic write-sequence set.
**Builds on:** wf-b-real (ed8deda), which stacks on wf-librarian-v3. The stack merges after R3 (R4).
D-119 | 2026-09-25 | ACCEPTED (orchestrator, after release-gating review 75) | **The release tooling gets crash-safety before R3. Several of these defects date from R1/R2 and were never triggered.** Review 75 of r3-tooling @ 3b2ce64: both reviewers DO-NOT-MERGE, with 6 HIGH findings (several reproduced in the harness):
1. The "previous env" snapshot can capture the on-disk R3 env while the containers still run R2, so a rollback would start R2 with R3-only profiles.
2. A same-ref deploy rerun rewrites the rollback tuple to R3→R3 and deletes the R2 env snapshot.
3. The env install runs outside the deploy lock, so api and librarian can end on different env releases.
4. **(pre-existing)** A killed destructive DB restore re-snapshots the half-restored DB on retry.
5. **(pre-existing)** `--accept-release` passes while a rollback is in progress and deletes the backups.
6. **(pre-existing)** A deploy killed between stack start and the state publish cannot converge.
3 MEDIUM findings: the manifest checks presence rather than exact values or api==librarian equality; an unknown label gets the interim pass; secret-bearing snapshot files can be orphaned after a crash.
All are fixed on r3-tooling, each with fault-injection tests (kill or interrupt at the exact step, then assert convergence), before R3 proceeds. The R3 flow is unchanged otherwise: merge → LLM-free retrieval re-check → push → VM rehearsal (including a rollback drill) → prod per D-099/D-116.
D-120 | 2026-09-25 | ACCEPTED (owner) | **Production OpenRouter key created (D-110).** Monthly limit $50, reset monthly; verified via /api/v1/key (limit_remaining $50, usage $0). Stored ONLY in deploy/.local/153.92.1.166/openrouter-prod.key (gitignored state dir, mode 0600). It is installed into the VPS llm.env during the R3 deploy (D-108/D-119 order), replacing the shared dev key in production; the dev key stays local for agents and the VM. Caveat: both keys draw from the SAME account credit balance. The per-key limit caps production's own spend and separates accounting, but dev spend can still drain the shared balance, so a monthly limit on the dev key is recommended as an owner action. The key value appeared once in the chat transcript: acceptable given the $50 cap; rotate it if that transcript is ever shared.
D-121 | 2026-09-25 | ACCEPTED (owner) | **The prod key is placed on the VPS; the owner swaps it into llm.env personally. Production LLM spend target: ≤ $10/month.** Location: `/etc/hlmemo/openrouter-prod.key` (0600, hlmdeploy:hlmdeploy, 73 bytes, sha256 prefix 32fe76f56a2b, identical to the local copy). llm.env itself was NOT changed by the orchestrator; the owner replaces `OPENROUTER_API_KEY` in /etc/hlmemo/llm.env and restarts. The shared credit balance does not matter to the owner. Budget finding: prod llm.env has `HLM_LLM_BUDGET_MONTH_USD=60` (plus DAY 10 and HOUR 3), which is above the owner's $10/month expectation. Recommended: MONTH=10, DAY=2, HOUR=1, so the HLMemo spend guard enforces the target itself; the owner applies this in the same edit. The R3 llm.env template/installer must carry these values, so a future install does not revert them.
D-122 | 2026-09-25 | PROPOSED (orchestrator → owner decision) | **Review 77: the deploy tooling's crash-safety is deeper than one more fix round; R3 release options.** Both reviewers say DO-NOT-MERGE on r3-tooling @ f9f5037 (claims 2, 5 and 8 are OK). New HIGH findings, several PRE-EXISTING since R1/R2 and never triggered:
- a resumed deploy publishes a half-switched stack;
- the backup timer and restore race a rollback or recovery (the operation lock is not held across dump → restore → validate);
- daily rotation can delete the rollback safety dump, and a retry then cleans the journal without restoring;
- install and deploy/rollback journals can deadlock each other;
- the persistent image selection is not reverted on recovery;
- the provenance fingerprint ignores the budget/guard fields and does not require both services;
- R3-REHEARSAL.md is stale (it expects rewrite ON, caps of 60, and a manual R2-env reinstall that the new provenance check refuses).
MEDIUM: extra fallback overrides or an unreadable env file fail open; secret orphan windows; a preserved wrong key passes (the probe does not authenticate).
**Options:** (a) keep hardening the shell tooling until the reviews come back clean, which could take several more rounds; (b) ship R3 behind an infrastructure safety net and fix the tooling in a dedicated workstream (R3.1). Proposed (b):
1. Before the prod deploy, take a Hostinger VPS snapshot of the HLMemo VPS only (VM 2002259). The owner handles backups, so this needs the owner's OK. The ultimate rollback becomes a whole-machine snapshot restore; writes after the snapshot are lost, which is acceptable at personal-use volume.
2. The deploy is attended, with the backup timer paused during the deploy window.
3. Update the rehearsal checklist to the current scope (rewrite OFF, caps 10/2/1, automatic env restore), and drill both the script rollback AND the snapshot restore on the VM.
4. Merge r3-tooling as it is, because it is strictly better than R2's tooling for the env-compatibility risk that R3 introduces, and record the review-77 findings as an accepted, time-boxed residual risk for R3 only.
5. Tooling hardening (all of the review-77 HIGH/MEDIUM findings) becomes workstream T, and it gates R4.
This amends D-099 criterion 3 for the tooling only, and therefore needs the owner's decision.
D-123 | 2026-09-25 | ACCEPTED (owner) | **D-122 option (b) accepted: R3 ships behind a Hostinger VPS snapshot safety net; the tooling hardening (workstream T) gates R4.** r3-tooling (f9f5037) is merged into main. **R3 = main = J + F + env-aware release tooling**; no rewrite, no v3/B-real/write-updates/spend-reconcile (all held for R4). Release procedure:
1. The LLM-free retrieval re-check on the final SHA shows no regression vs R2 (the D-116 criterion 1).
2. Push after a gitleaks scan.
3. VM rehearsal with the UPDATED checklist: rewrite OFF, caps 10/2/1, automatic env restore, G-L3 on a quiet host, G-LIVE-B/C/D within the $3 budget, a script-rollback drill AND a VM snapshot-restore drill.
4. Prod: take a Hostinger snapshot of VM 2002259 ONLY (the other VPSs are never touched), pause the backup timer, deploy attended (D-108/D-119 order), run the remote gates and the cutover check, run the e2e re-check, resume the timer.
5. Any failure: script rollback first; if that fails, restore the snapshot.
The review-77 findings are an accepted residual for R3 only and are listed for workstream T.
D-124 | 2026-09-25 | ACCEPTED | **R3 (805f4cd) shows no retrieval regression: the D-116 criterion 1 PASS.** Pinned git-archive, prod-rule import, budget 3000, drill-top 5, with no librarian and no rewrite ($0). Every metric equals the D-090/D-087 base exactly:
- A: hit@5 .793, MRR .660, L2 .772, temporal L2 .40, stale-first 8/15, negative AUC .792.
- B: hit@5 .625, evidence R@5 .451, L2 .556, stale-first 6/16.
- Pooled .643; Turkish 17/36; English 28/36; every category Δ 0.0.
The top-5 ids and titles are identical per question for 100/100 A and 80/80 B questions. Query p95 was 142/170 ms on a loaded host with one shared api (latency is gated separately in the VM rehearsal). Data: docs/private/realdata-{yt,hlmemo}/results-r3-release/.
D-125 | 2026-09-25 | ACCEPTED (orchestrator; retrospective asked by the owner) | **Retrospective and course correction.** Why the project ran long:
1. The core premise error (multi-statement items where the research report specified atomic L1 facts) was found only after three librarian builds (D-103/D-117). A ceiling experiment after v1 would have saved about 3 days.
2. The review process could not converge: dual adversarial reviews gated on "no open HIGH", with no threat model, severity rubric or round cap until D-114. The query rewrite took 8 rounds, and its hardening erased its value (+8.3 → +1.8, then shelved).
3. Enterprise-grade guarantees on a single-user system (124 decisions; every feature must satisfy replay, locks, observer, policy, events, TTL and budget).
4. Too many parallel workstreams (5–6): merge churn, host contention, DB footguns, credit exhaustion.
5. The wrong regime was measured: long imported docs instead of agents writing short memories; D-118 came after four failures.
The core server works and safety never broke. The waste was direction, not effort.
**Now:**
- (a) Only 2 active workstreams: the R3 release (rehearsal → prod per D-123), and D-118 finishing its current fix round plus ONE verification, then an owner risk decision.
- (b) STOPPED: post-hoc librarian quality work (v3/B-real tuning); the librarian stays an observer and question generator. Rewrite and renditions stay shelved.
- (c) After R3, in order:
  1. Dogfood HLMemo for this project's own memory.
  2. R4 = a MINIMAL port of the D-118 write-time updates onto main, not the whole v3/B-real/pilot stack.
  3. Tooling hardening T, before R4: threat-modelled, at most 2 rounds.
  4. The real HLMemo self-migration (the first Phase 5 step), supervised by the owner.
  5. Re-scope Phase 3/4 with a simplification review of which guarantees a personal system needs.
**Working rules, effective now:**
- ceiling-first: at most a 1-day oracle/prototype before any LLM-quality build;
- dual review only for one-way doors (data, security, release), with the threat model and severity rubric written first, at most 2 rounds, then an explicit owner accept/reject on the residual; a HIGH requires a reproducing test;
- at most 2–3 parallel workstreams, finish before starting;
- timeboxes with owner checkpoints.
The CLAUDE.md working agreement is to be updated when the owner confirms.
D-126 | 2026-09-25 | ACCEPTED (orchestrator) | **D-118 write-time supersession is review-closed within the D-125 cap.** Branch wf-write-updates @ 8919355.
- Critical review 76 (2 HIGH + 3 MEDIUM) led to 5 fixes. Each has a regression test that fails on 8b54800 and passes now, live and replay.
- The single capped verification (consult 78, astra-low) returned OK: all 5 findings FIXED, no in-scope regression.
- Gates so far: unit 858, targeted integration 63. The FULL integration suite on the final code is pending; it waits for the R3 rehearsal to free the host.
- No open HIGH, so no owner risk call is needed.
- It stays on its branch until R4. Per D-125, R4 is a MINIMAL port of this feature onto main rather than the whole v3/B-real/pilot stack. The port plan comes after R3 ships.
D-127 | 2026-09-25 | ACCEPTED (owner waiver) | **The R3 VM rehearsal passed every D-099 criterion-2 item except the VM G-L3 latency bound, which the owner WAIVED for R3 because R2 fails it identically on the same data.** Rehearsal on 805f4cd (docs/status/R3-REHEARSAL.md, af97279):
- G3 0.980; G4 p95 251 ms; host G-L3 358/417 ms.
- Remote gates 11/11, and 10/10 after every drill step.
- Cutover interim and `--release r3` PASS (caps 1/2/10, rewrite and cap absent); downtime 9.7 s.
- G-LIVE-C with the primary down PASS (catch ≥ .975, false-warn ≤ .075).
- G-LIVE-B PASS (luna, glm53-flash, chain; false supersede 0); G-LIVE-D PASS.
- Observer: 0 links, versions or closes.
- Drill (a) script rollback PASS: the R2 env was restored automatically, 11.3 s downtime, then roll-forward.
- Drill (b) snapshot restore PASS: `/ready` in 13 s, exact R2 state.
- None of the review-77 residuals were hit. Spend about $2.20.
**VM G-L3:** neutral p95 576/585 ms, identifier 954 ms (bound 500). An A/B at the same moment on the same data gave R2 725/588 vs R3 755/660 ms, with the same slowest queries. Root cause, PRE-EXISTING: the trigram search matches across every project's text before the project filter, and the VM had accumulated 200+ synthetic identifier-heavy bodies from rehearsals. This is a real scalability issue for Phase 5 (many projects). Fix it with a project-filtered trigram search (workstream T/R4) and re-gate G-L3.
**Prod preconditions:**
- The owner edits /etc/hlmemo/llm.env FIRST: the prod key and caps MONTH ≤ 10 (10/2/1). Otherwise the R3 env install fails its own manifest check.
- Docker Hub, ghcr and Hugging Face must be reachable from the VPS; every deploy re-downloads the model.
- The Hostinger snapshot of VM 2002259 only.
- The backup timer is paused during the attended deploy.
D-128 | 2026-09-26 | ACCEPTED | **Prod pre-deploy state for R3.** The owner edited /etc/hlmemo/llm.env on the VPS (D-121), and it was verified without printing secrets:
- The OPENROUTER_API_KEY sha256 prefix 32fe76f56a2b equals /etc/hlmemo/openrouter-prod.key, so production now uses its own key ($50/month key limit).
- The caps are HOUR 1 / DAY 2 / MONTH 10 with DISABLED=false; the running api reports MONTH=10.
- Only api and librarian were recreated; `/ready` returned 200 after about 60 s, and all services are healthy.
- The old-key llm.env.bak-* on the VPS still contains the dev key. The owner may delete it after R3.
Network: registry-1.docker.io and ghcr.io answer (401, the auth challenge), and huggingface.co answers 200. No HLMemo backup timer exists on the VPS (only dpkg's), so there is nothing to pause.
The Hostinger snapshot of VM 2002259 was started (action 116674684). It was the first snapshot on this VPS; no prior snapshot existed, so nothing was overwritten.
D-129 | 2026-09-26 | ACCEPTED | **Release R3 is LIVE in production (805f4cd).** R3 = J (librarian judgement v2, concurrency, stranding fix, D-086/087/095) + F (per-task fallbacks, D-094) + env-aware release tooling (D-111/D-119/D-121). The query rewrite is not included (shelved, D-116). The deploy was attended, behind Hostinger snapshot 377499 of VM 2002259 only (24 h expiry), in the D-108/D-119 order; no rollback was needed. Record: docs/status/R3-PROD-DEPLOY.md, with logs in docs/bakeoff/production-r3/.
- **Deploy:** the interim cutover check PASSed on the unlabelled env; public downtime 36.1 s.
- **Env install:** install_llm_env under the deploy lock preserved the operator's prod key (sha prefix 32fe76f56a2b) and the caps 1/2/10. `check_librarian --release r3` PASS, with the exact manifest.
- **Gates:** remote gates 11/11, with WAN p50 132 / p95 145 ms, risk-check judged=true, and a 21 s restore in the backup-restore drill.
- **Light e2e:** 8 tools; a write landed; the query found it; risk_check returned judged=true.
- **Observer:** 0 links, supersessions or closes; items 407 → 411 (the gate and e2e markers only).
- **Deploy LLM spend:** $0.0014.
- **Independent orchestrator check:** all services run 805f4cd, current-ref 805f4cd, previous-ref 6902f91, env marker r3, the prod key sha matches, MONTH cap 10, rewrite and cap absent, the librarian is an observer.
- **Open:** `--accept-release` (deletes the pre-W0 backups) needs a separate owner OK. The old-key llm.env.bak copies on the VPS can be deleted once R3 is accepted. The full e2e re-run is folded into the D-125 dogfooding.
D-130 | 2026-09-26 | ACCEPTED (owner) | **Product definition, and a self-certifying "Production Ready" gate: the RESEARCH LIBRARIAN.**
**Owner's definition.** The librarian must not stay a mere observer; it must do the retrieval work.
- For a request plus its project context, it uses a **Memory Map** (what is where), issues several internal queries, and answers LLM-to-LLM with (a) a refined answer, (b) primary sources, and (c) related sources. Each source is a path/handle the caller can drill into or pull raw: "the answer is X; for details see Y, Z, W, …".
- When the system does this on REAL data, it is Production Ready and **certifies its own release**. No owner approval is needed for releases from now on.
- The owner's lesson, recorded: the infrastructure was built more robust than needed. The fine-grained engineering effort belongs to the LLM performance, i.e. the product's core job.
**Role.** "Research librarian". It is read-only: it never mutates memory, so it does not need the observer → assistant → autonomous write ladder. It runs with the CALLER's capabilities through the existing scoped retrieval, so scope and privacy hold by construction.
**PR gate, v0, to be finalised after the ceiling prototype.** Measured on the real migrated HLMemo memory, using the SEALED hold-out questions for the final gate and B-dev plus real usage for development:
| Criterion | Threshold |
|---|---|
| Answer correct and complete (blind judge) | ≥ 0.80 |
| Correct abstention on unanswerable questions | ≥ 0.90 |
| Faithfulness (every claim supported by the cited sources) | ≥ 0.95 |
| Gold evidence among primary + related sources | ≥ 0.85 |
| Cross-project or device-scope leak | 0 |
| Latency | p95 ≤ 20 s |
| Mean cost per question | ≤ $0.01 |
Context: today's raw retrieval finds B evidence in the top-5 for only .451 of questions. The multi-query plus map loop must raise source recall substantially, and that is the crux.
**Plan (D-125 rules: ceiling first, at most 2–3 workstreams, timeboxes):**
- **W-A (timebox about 3 h):** migrate THIS project's memory into prod project `hlmemo`. Dry-run plan first, then apply. Exclude docs/private, deploy/.local, secrets, cassettes and bulk result data.
- **W-B (timebox 1 day):** a ceiling prototype of the research loop outside the server (map + multi-query + answer with sources, luna), on a dev copy of the same import, measured on B-dev against the gate metrics. It compares no map vs a deterministic source map, and single-shot synthesis (W2e) vs an agentic loop.
- Then build the chosen design into the server as a tool, with a routine review; a dual review only for the scope/privacy of the read path.
- Parked: D-118 (write-time updates) after its running suite; tooling T; the trigram fix (it is re-gated when the research loop needs its latency).
D-131 | 2026-09-26 | ACCEPTED (orchestrator, within the owner's D-130 instruction "start by migrating this project's memory") | **HLMemo self-migration into prod project `hlmemo`: dry run approved, and the test project is isolated.**
- **Dry run:** 551 new items, 0 changed or closed (the 2 existing items have no source, so the import cannot touch them), about 355k tokens.
- **Per source:** decisions 198 (130 decision rows), status 46, research 34, consults 192, bakeoff results 12, USAGE/README/RUNBOOK/HARDWARE 28, CLAUDE.md 1, auto-memory 40 (34 lessons). No serena memories.
- **Exclusions verified:** 208 files, none under docs/private, deploy/.local, .env*, *.key, tests/, src/, eval/results, raw outputs or cassettes. gitleaks on the exact file set: clean. The auto-memory holds pointers only (key-file locations and VPS IPs), no credential values.
- **Expected librarian spend:** about $0.7–0.9 (551 observer jobs); the hour cap may pause it.
**Policy:** `hlmemo-e2e` (the TEST project, 381 near-duplicate items) is set to librarian_cross_project=exclude, following the D-083 test isolation. This is reversible, and it stops the librarian from treating the e2e duplicates as cross-project candidates (noise and spend).
D-132 | 2026-09-26 | ACCEPTED | **HLMemo's own memory is migrated into prod project `hlmemo`: this is the first real-data migration.** Under the owner's explicit /permissions grant and the D-130 instruction, the main session ran two steps:
1. The hlmemo-e2e isolation: policy `{"librarian_cross_project":"exclude"}`, previously null.
2. The approved import (scratchpad/wa/apply.sh, 22:11:27–22:13:04Z): markdown 511 new, context 1 new, automemory 40 new = **552 new; 0 changed, 0 closed, 0 rejected**. The 208-file set was gitleaks-clean and contained no docs/private, deploy/.local or secrets. It is one item more than the dry run, because STATUS and DECISIONS gained sections after the dry run.
Right afterwards: 464 embeds queued and 517 observer librarian jobs queued; spend today $0.03 against caps of 1/2/10.
The subagent's auto-mode permission check had refused these prod writes twice. The owner's grant applied to the main session only, so it was not routed around. This memory is the REAL data on which the D-130 Production-Ready gate will be measured.
D-133 | 2026-09-26 | ACCEPTED (owner: "continue until it works as intended"; the owner is asleep, the orchestrator runs autonomously) | **Overnight autonomous plan toward the D-130 Production-Ready gate: the research librarian plus the Memory Map with L2 summaries.** The target is the full memory system of the research report (L2 topic summaries = the "summarising" layer), working as the owner described: a refined LLM-to-LLM answer plus primary and related source handles.
**Steps, in order:**
1. Pick the loop design from the W-B ceiling prototype.
2. Memory Map per project: a deterministic source/section tree plus short LLM summaries per file and topic cluster (report §D, L2; RAPTOR-lite, bounded, cached, refreshed on writes). Summarising the whole HLMemo memory once is cheap (~355k input tokens).
3. A server tool (read-only, caller's capabilities, per-task fallback hook, spend guard) returning {answer, primary[], related[], abstain}, with deterministic citation checks.
4. Measure the gate on an exact dev replica of the migrated prod memory, same importer and sources. Questions: the SEALED B hold-out plus a fresh question set about the recent decisions, written blind by an independent agent.
5. If the gate passes: a light VM rehearsal, then a fresh Hostinger snapshot of VM 2002259 only, then a self-certified prod release (D-130). If it fails: record exactly what fails and why.
**Budget:** tonight's dev LLM spend is ≤ $6 in total, so that ≥ $9 of the shared balance always stays available to prod. Prod runs on its own key with caps 1/2/10.
**Rules (D-125):**
- at most 2–3 workstreams;
- a dual review only for the read-path scope/privacy;
- every decision recorded here;
- no prod writes beyond a gated release.
D-134 | 2026-09-26 | ACCEPTED | **D-118 write-time supersession is fully gated on its branch.** wf-write-updates @ 8919355: the full integration suite ran 612 passed / 14 skipped / 0 failed (fresh DB, 1 h). With D-126 (review-closed) it is complete and parked for R4. Per D-125 it is ported minimally onto main after the research-librarian work (D-133), or together with it if that port is small.
D-135 | 2026-09-26 | ACCEPTED (orchestrator) | **The PR-gate question set is ready.** 60 questions were written BLIND by an independent agent from the migrated knowledge files only (no src/eval/prototype access). Location: docs/private/realdata-hlmemo/pr-gate/questions.jsonl (gitignored). Categories: recent 25, temporal 10 (the gold is the CURRENT state, with superseded sources noted), procedure 10, multi-hop 5, unanswerable 10. Languages: en 30, tr 25, tr-ascii 5. Each question carries gold_answer, gold_sources anchors and must_mention facts.
The final gate uses this set PLUS the sealed B hold-out.
Replica rule: the dev replica mirrors the CURRENT tree (all migrated sources, including D-132/D-133 and MIGRATION-HLMEMO.md). Prod memory is refreshed by an incremental `hlm import` (--keep-missing; dogfooding keeps it current) before the final prod certification sample.
D-136 | 2026-09-26 | ACCEPTED (orchestrator, overnight D-133) | **W-B ceiling: the map-guided research loop is close to the gate, so it gets built. Gate v1 pins the judge and the rubric.** B-dev: 72 questions, sealed B never opened; replica `hlm_research_b` reproduces R3 exactly; spend $1.46.
| Variant | Correct (flash judge) | Abstain | Faithful | Source recall | p95 | $/q |
|---|---|---|---|---|---|---|
| V0 single query | – | – | – | .451 | – | – |
| V1 W2e synthesis | .250 | 1.00 | .93 (judge) | .521 | 2.3 s | – |
| V2 loop, no map | .611 | – | – | .632 | – | – |
| **V3 loop + 3k map** | **.778** | 1.00 | .78 quote / .95 judge | .708 | 9.3 s | .0019 |
| V5 (+refine, answer prompt v2) | .72–.76 | .875–1.00 | .93–.95 | .70 (.771 with a 6k map) | ~11–12 s | .002–.003 |
- **The map is the lever:** it adds +.17 correct and takes incorrect answers from .069 to 0, mainly through better queries.
- **Dominant failure:** INCOMPLETE answers (a secondary gold detail is dropped) even though the evidence was retrieved (9–10/72). Next: retrieval misses (4–6/72) cap source recall at ≤ .82.
- **Judge sensitivity:** deepseek-v4-pro scores the same answers far lower (V3 .583), and the two judges agree on .746.
**Build (R4 core), from main:** a read-only tool `memory.ask`.
- **Flow:** plan (map + question) → 3–5 queries + ≤6 map sections → fuse → drill ≤12 (chunk handles) → answer. It adds a COMPLETENESS pass (checking every retrieved gold-candidate fact against the draft) and one refinement round only when it abstains or is unsure. At most 4–5 LLM calls.
- **Answer contract:** {answer in the caller's language, confidence, primary ≤3 [{handle, path, quote}], related ≤5 [{handle, path}], abstained}. A deterministic quote/literal check with normalised numerals.
- **Memory Map per project:** about 6k tokens. It holds the path tree, item handles with chunk counts, headings and decision rows as `vN.M`, and git subjects, spread across large documents. It also carries an **L2 summary** for each file or topic cluster: LLM-written, cached, refreshed asynchronously when its items change (report §D L2, the "summarising" layer).
- **Runtime:** caller capabilities, privacy precheck, spend guard, per-task fallback `research`.
**Gate v1 (pinned before the sealed run):**
- judge = deepseek-v4-pro, an independent family and the strict one;
- correct = every must_mention fact present and no contradicting claim;
- source recall = cited sources contain the gold facts: the exact gold anchor OR a judge-verified restatement;
- the sets are the 60-question PR set plus the sealed B;
- the thresholds of D-130 stand.
Workstreams:
- an implementer, with no hold-out access;
- an evaluator on B-dev who reports aggregate failure classes back (no question text).
D-137 | 2026-09-26 | ACCEPTED (orchestrator; recorded BEFORE any final-gate run) | **Gate v1 calibration on B-dev, and one rubric-consistency fix.** Scorer `score.py --gate v1` (judge deepseek-v4-pro pinned; rubric prompts versioned; branch wf-research-proto df03bf8). Current-tree replica `hlm_research_cur` = prod's 552 source keys plus the 6 new docs (558 items / 1,450 chunks).
Re-scoring the prototype's existing B-dev answers under v1 gave V3:
| Criterion | Score | Result |
|---|---|---|
| Correct | .389 | FAIL |
| Abstention | 1.00 | PASS |
| Faithful | .456 | FAIL (quote check alone .805) |
| Source recall (with restatement) | .898 | PASS |
| p95 | 9.3 s | PASS |
| $/q | .002 | PASS |
V5 scored .455 correct and .592 faithful on the 62 judged questions.
Causes:
- (1) answers miss about a third of the key facts;
- (2) one quote per claim covers only PART of the claim, so the judge refuses entailment. Both are product defects, and memory.ask now requires claims[] with 1–3 fully entailing quotes each, plus a completeness pass (sent to the implementer).
- (3) A RUBRIC INCONSISTENCY: for corpus B the scorer used ALL gold facts as must-mention, whereas the blind PR set uses 1–3 key atomic facts.
**Fix:** for every set, must_mention = 1–3 key atomic facts per question. For sealed B they are derived ONCE from the gold answer by a fixed prompt, cached and frozen before any system answer is scored; the PR set keeps its own. The judge, the thresholds and all other criteria stay unchanged. The change is recorded before the sealed or PR sets are touched, and it is not tuned on results.
Tonight's spend: the evaluator $0.60 (budget reached); the total is about $2.1 of $6.
D-138 | 2026-09-26 | ACCEPTED | **Fair dev baseline under gate v1 (with the D-137 rubric), and the iteration plan.** Frozen 1–3 key-fact files: B-dev sha256 4211fb8d…f7f9 and sealed B f76fe319…b2a. They were derived verbatim, by index, from the gold facts with prompt derive.md v2 (499e619, committed before sealed was touched). Sealed B's gold was read only for this derivation; no system has run on it. Disclosure: the first dev derivation dropped a key fact, so the prompt was fixed using dev gold only; the discarded file is kept.
B-dev prototype baseline:
| Variant | Correct | Abstain | Faithful | Source recall | p95 |
|---|---|---|---|---|---|
| V3 | .472 | 1.00 | .456 | .898 | 9.3 s |
| V5 | .486 | 1.00 | .573 (quote check .925) | .905 | 11.9 s |
Both miss about a third of the key facts, and V5 contradicts gold on .167 of questions.
**Iteration plan on B-dev only, about $0.4 per run:**
- (1) memory.ask with claims[] and fully entailing 1–3 quotes, plus a completeness pass;
- (2) if still short: a stronger model (luna-pro) for the answer/completeness steps only, planning stays on luna. Expected ~$0.007/q, inside the $0.01 gate;
- (3) retrieval for the remaining misses.
The final gate runs only once the dev numbers clear the thresholds with margin. Total spend tonight so far is about $2.3 of $6.
D-139 | 2026-09-26 | ACCEPTED (orchestrator) | **Dev lever test: the claims contract improves faithfulness but not correctness; misses are MAIN facts that the quote check drops.** Fixed 34-question B-dev subset (subset-v6.json, recorded before any V6 answer was scored).
| Variant | Correct | Faithful (judge / quote check) | Recall | Abstain | p95 | $/q |
|---|---|---|---|---|---|---|
| V5 | .462 | .630 / .959 | .853 | – | 12.1 s | .0021 |
| V6 (claims + completeness) | .423 | .709 / 1.00 | .814 | – | 14.7 s | .0038 |
| V6-pro (luna-pro for answer/completeness) | .538 | .717 | .891 | .875 (1 false answer) | **24.8 s, FAIL** | .0066 |
Misses are mostly the MAIN key fact (V6 12/15, V6-pro 10/12), not secondary details. About half had a claim REMOVED by the verbatim-quote check. Spend $0.70.
**Next, both offline at $0:**
- (A) classify why the quotes failed (whitespace/markdown/table vs a true paraphrase) and how many would survive a normalised match;
- (B) a judge audit: 30 answers independently adjudicated against the frozen key facts, to measure the judge's false negatives/positives before trusting it for the final gate.
memory.ask gets: normalised quote matching (the raw quote kept for display), a re-quote attempt instead of a silent drop, and the main answer as the first claim.
D-140 | 2026-09-26 | ACCEPTED (orchestrator) | **Offline diagnosis ($0): the judge is valid, and the quote/value checks drop true claims.** Files: gate-v1/diag-drops-and-judge-audit.json.
**Judge audit:** 30 answers and 109 claims, independently adjudicated.
- Correct: agreement .967, judge FN 1/16, FP 0/14, so the pinned judge (deepseek-v4-pro) is kept.
- Faithful: agreement .899, FN 10/92 (9 of those 10 are subject-less quote fragments), FP 1/17.
**Dropped claims:** 33 dropped in V6/V6-pro.
- Only 4 failed the quote match itself: markdown bold next to punctuation (3) and a quote that skipped table cells (1).
- 29 had verbatim quotes but failed the claim-value check:
  | Cause | Claims |
  |---|---|
  | TR/EN suffixes glued to numbers or names | 10 |
  | A plain "a/b" phrase read as an identifier | 5 |
  | The value sits elsewhere in the excerpt | 4 |
  | The value is reformatted | 5 |
  | A dash variant | 3 |
  | Truly unsupported | 2 |
- 25/33 would survive with suffix-tolerant, excerpt-level value checks.
- In V6, artefact drops removed a key fact in 6 of 15 misses.
**Product fixes sent to memory.ask:**
- markup stripping without inserted spaces, NFC, dash/quote unification;
- a suffix-tolerant value check, where "a/b" prose is not an identifier and values are checked against the cited excerpt, with reformatting accepted;
- self-contained quotes that name their subject, with the heading or row key as a second quote;
- the main fact first, and a re-quote attempt before any drop.
The scorer's own quote/value check is NOT changed. It stays as pinned, and any change to it would need its own recorded decision.
D-141 | 2026-09-26 | ACCEPTED (orchestrator; recorded BEFORE any final-gate run) | **V7 closes the claim-drop artefacts, and the scorer's deterministic check gets the SAME normalisation (a measurement-validity fix).** V7 = V6 + the D-140 fixes, on luna, on the fixed 34-question subset:
| Variant | Correct | Faithful | Recall | Abstain | p95 | $/q | Claims dropped |
|---|---|---|---|---|---|---|---|
| V7 | .538 | .647 | .872 | .875 | 15.4 s | .0035 | 0 of 85 |
| V6 | – | – | – | – | – | – | 15 of 94 |
V7 matches V6-pro's correctness at luna cost and inside the latency limit.
The pinned scorer still rejected 8 TRUE V7 claims for the very artefacts diagnosed in D-140: suffix 4, reformatted value 2, dash 1, markup 1. A faithfulness metric must measure entailment, not formatting, so the scorer's deterministic quote/value check now uses exactly the product's normalisation. The judge prompt, the judge model, the frozen key facts and all thresholds are unchanged. The decision is backed by the D-140 audit (29 of 33 drops were artefacts on true claims) and is fixed before the PR set or sealed B is touched.
Remaining gap: 10 of 26 answerable questions still miss a key fact (8 of them the MAIN fact), with 2 retrieval misses and 0 contradictions. A $0 diagnosis now classifies whether the main fact was present in the evidence the answerer saw.
D-142 | 2026-09-26 | ACCEPTED (orchestrator) | **The main-fact misses come from COMPRESSION, not retrieval.** With the D-141 scorer (the product's normalisation), V7 on the 34-question subset scores: correct .538, faithful .729 (quote check 1.00), recall .872, abstain .875, p95 15.4 s, $.0035/q. V6-pro: .538 / .717 / .891.
Diagnosis of V7's 10 missing-key-fact questions:
| Class | Count |
|---|---|
| (a) The fact was in the evidence the answerer saw but was not stated | 9 |
| (b) Truncated | 0 |
| (c) The gold chunk was never drilled | 1 |
| (d) Not retrieved | 0 |
In 8 of the 9 (a) cases, the answer states the gist but drops the specific the key fact names: an identifier part, backend names, a log path, a plan name, a full URL, a second value, or a rationale. No question was misread. The answerer summarises too aggressively for an LLM reader.
Fix, V8 (prototype) and memory.ask:
- a specificity instruction (exact ids, names, paths, URLs and all values; brevity is not a goal);
- atomic claims with full-sentence or full-row quotes that include the subject;
- a completeness pass that upgrades general wording to the most specific form in the evidence.
D-143 | 2026-09-26 | ACCEPTED | **V8 with the output cap: correct .615, faithful .780, source recall .853, abstain .875, p95 19.6 s, $.0036/q** (34-question dev subset, fixed D-141 scorer, pinned judge). No answer hit the 6k cap; the longest output was 1,404 tokens.
Progress under the strict gate-v1 judge:
| Variant | Correct | Faithful |
|---|---|---|
| V1 W2e synthesis | .25 | – |
| V3 | .47 | .46 |
| V7 | .54 | .73 |
| V8 | **.62** | **.78** |
Gap to the gate: correct +.19, faithful +.17. Abstention is .875 against a .90 bar (1 false answer on 8 unanswerables), and p95 is at the 20 s limit.
Remaining misses (10 of 26): missing key fact 7 (6 of them the main fact), retrieval 2, contradiction 1.
Safety lesson: without per-call caps a specificity prompt can run away to 65k output tokens, so memory.ask gets per-call max_tokens, a per-question $ and token budget and a wall-clock deadline (sent to the implementer).
The prototype spend guard now reserves the worst case for in-flight calls; there was an overshoot of $0.018 with 4 parallel judges.
Tonight's spend is about $3.9 of $6. The final gate is NOT run: the dev numbers have not cleared the thresholds.
D-144 | 2026-09-26 | ACCEPTED (orchestrator) | **$0 diagnosis of V8-cap gives the next three levers.**
1. **Missed facts:** 10 missed facts across 7 questions.
   - Present in the evidence (7): still compressed 5, all of them main facts whose identifier or value was not carried into the claim; a different related fact chosen 1.
   - Not in the evidence (3): all 3 were not drilled (the document was retrieved, but its chunk was never read).
2. **Rejected claims:** 18 of 82 claims were rejected. The causes were added inference 13 (8 of them attributions naming a source, actor, gate or workstream the quote does not name, and 5 an added relation or consequence), over-claim 2, merge 1, and judge error 2.
**Levers, applied as prototype V9 and memory.ask addendum 6:**
- (a) Deterministic COPY-THROUGH: the question-relevant identifiers and values in a claim's quote must appear in the claim, with one targeted rewrite otherwise. This fixes 5 of 7.
- (b) NO ATTRIBUTION: provenance lives in the handle, and a check requires every named subject in a claim to occur in its quotes. This fixes 8 of 18.
- (c) A doc-level best-chunk drill for top-ranked documents. This fixes 3.
Expected if they hold: correct ≈ .80, faithful ≈ .88.
D-145 | 2026-09-26 | ACCEPTED | **V9 isolates the levers.** V9 = V8-cap + copy-through + no-attribution + the doc-level drill: correct .500, faithful .817, recall .744, abstain .875, p95 23.4 s, $.0038/q.
- Copy-through and no-attribution WORK: faithful +.037, and both targeted compression misses became correct.
- The doc-level drill HURTS: under the 12-chunk cap it displaced ranked chunks 7–12, so gold-in-evidence fell from .712 to .538 (6 questions worse, 0 better), correct fell by 5 answers, and p95 rose.
Decision: V10 = V8-cap + copy-through + no-attribution; the doc-level drill is allowed only in FREE drill slots. The same correction went to memory.ask. Spend $0.30; tonight's total is about $4.2 of $6.
D-146 | 2026-09-26 | ACCEPTED | **V10 is the best prototype on quality, but it misses latency; prototype iteration stops (budget).** V10 = V8-cap + copy-through + no-attribution, with the doc drill only for free slots (it never ran). On the 34-question subset under the strict judge: correct .615, faithful .849, recall .872, abstain 1.00, p95 28.5 s (FAIL; the separate repair call runs in sequence), $.0038/q. Correct is unchanged from V8: the same 6 main-fact misses remain. memory.ask gets the repair folded into the completeness call, parallel queries/drills and ≤ 4 sequential steps. Tonight's spend is about $4.5 of $6; the rest is reserved for evaluating memory.ask on dev and, only if dev clears, the final gate. Honest status at 05:00: the research librarian is much better than the old synthesis (correct .25 → .62, faithful → .85 under a strict judge), but the D-130 gate (.80/.95) is NOT reached yet.
D-147 | 2026-09-26 | ACCEPTED | **memory.ask is built on the server (wf-memory-ask @ 9bceb59, 11 commits, not merged).**
- **Memory Map:** a rebuildable projection of the CALLER's view, about 6k tokens: a path tree of `vN(k)` item handles and `vN.M` section handles, spread by bit-reversal. L2 summaries come from the async `map_summary` task (digest-cached, debounced, backed off, spend-guarded, fallback key `map_summary`).
- **Loop:** plan → parallel queries + drill (the doc-level drill only in free slots) → answer → completeness plus repair (copy-through and no-attribution folded in) → refine only on abstain. At most 4 sequential steps and 9 attempts.
- **Limits:** per-JOB max_tokens; a per-question budget of $0.01 or 100k tokens with a partial answer; a 25 s deadline (D-084 policy).
- **Safety:** a privacy precheck on every send; no DB transaction held across any LLM call; a locked final re-check.
- **Claims:** each carries 1–3 quotes, checked deterministically with the D-140 normalisation.
- **Migration:** 0009_memory_map (create-only, not in replay). G-SURF 2978/3000.
- **Tests:** scope isolation (map, prompts, answer, summaries) and no-mutation / no-open-transaction tests are present.
- **Live smoke:** 5/5, p50 7.6 s / p95 11.7 s under load; build spend about $0.055.
- **Gates:** unit 617, integration shards 0 failed, G3 0.980, G4 p95 263 ms, G-L3 p95 344/362 ms.
- **Open:** load_view scales linearly; a one-time prod map_summary spend of about $0.2–0.5; no MCP outputSchema; the param is named `token_budget`; an unused `verdicts` field; the release manifest is not updated.
- **Disclosure:** the implementer once grepped docs/private by accident (one prototype result line was printed, and not used).
**Next, in parallel:**
- a dev evaluation of the server build on the 34-question subset (≤ $0.40, summaries included);
- dual review 79 against a written threat model T1–T6 (scope, privacy, mutation, spend, migration, output honesty), capped at 2 rounds.
D-148 | 2026-09-26 | ACCEPTED (orchestrator) | **Review 79, round 1 of 2 on memory.ask: FIX-NEEDED, against the threat model.** Every finding was reproduced by the reviewers.
| Threat | Severity | Finding |
|---|---|---|
| T1 | HIGH | The D-083 exclude isolation is missing from load_view, the privacy rechecks and the map_summary grouping, so an [A,T] item with T excluded leaks into A's map, prompts and summary. |
| T2 | HIGH | The redactor misses secrets after JSON escaping, so text must be redacted before serialisation. |
| T4 | HIGH | Per-question budget checks happen only before the logical call; retries and fallback exceed the $0.01 cap ($0.016 was observed). |
| T4 | MEDIUM | map_summary never retries after a failure. |
| T5 | HIGH | The new features default to ON, so an R3 env on this image silently enables memory.ask and summary spend. They must default OFF, and an R4 manifest must pin the flags, fallbacks and fingerprint. |
| T6 | HIGH | The quote matcher's word-skipping fallback accepts a quote with its "not" removed (a negation flip). It must be a contiguous normalised match, with changes to negation or modality words rejected. |
T3 (mutation) and the migration DDL had no findings. Round 2 is the last: a verification of these fixes only. After that, any residual goes to the owner, per D-125.
D-149 | 2026-09-26 | ACCEPTED | **The server memory.ask (9bceb59) on dev matches the prototype, but the D-130 gate is NOT reached, so there is no final-gate run and no release tonight.** Measured on the same 34-question B-dev subset with the strict judge: correct .577, faithful .874, recall .814, abstain 1.00, p95 21.3 s, $.0044/q, 3.3 LLM calls per question on average. V10 on the same subset: .615 / .849 / .872 / 1.00 / 28.5 s. The map summaries for this corpus cost $0.013 (89/89).
Misses: missing key fact 8 (6 of them the main fact), retrieval 2, contradiction 1.
Gap to the gate: correct −.22, faithful −.08, recall −.04, p95 +1.3 s.
Overnight trajectory under the strict judge:
| Stage | Correct |
|---|---|
| W2e synthesis | .25 |
| Map loop V3 | .47 |
| V8 | .62 |
| memory.ask | .58 (within ±.04 run noise) |
Faithful rose from .46 to .87.
Per D-130 and D-125 the release does not self-certify, and the sealed B and PR sets stay untouched. The review-79 round-1 fixes (T1/T2/T4/T5/T6) are in progress; round 2 is verification only.
Tonight's spend is about $4.8 of $6.
Next levers, for the owner's morning decision:
- (a) a stronger answer step only (luna-pro), budget-checked against p95;
- (b) structured evidence → facts extraction before composing, to fight compression;
- (c) a re-audit of judge faithfulness false negatives on the memory.ask outputs (the D-140 audit had an 11% FN rate);
- (d) retrieval recall .81 → .85.
D-150 | 2026-09-26 | ACCEPTED | **The last overnight probes: model strength is not the lever, and the judge is accurate.**
**(1) V10-pro** (luna-pro for the answer and completeness steps), on 27 of the 34 questions before the cap:
| Variant | Correct | Faithful | Recall | p95 | $/q |
|---|---|---|---|---|---|
| V10-pro | .615 | .859 | .853 | 38.2 s | .0079 |
| V10 | .615 | .844 | .872 | 28.5 s | .0037 |
The misses are the same, so a stronger answerer does not fix compression.
**(2) A $0 re-audit of memory.ask @ 9bceb59:**
- Correct: the judge is exact (0/15 FN, 0/11 FP), so the true correct is .577.
- Faithful: 4 of the 14 judge rejections are actually entailed and 0 of 20 accepted claims are false, so the true faithful is ≈ .91 against the judge's .874. It is still short of .95. The remaining unsupported claims name a person or source (4), list items beyond the quotes (3), or add a time/context frame (2).
**True overnight state of memory.ask:**
| Criterion | Value | Gate |
|---|---|---|
| Correct | .58 | .80 |
| Faithful (true) | ≈ .91 | .95 |
| Recall | .81 | .85 |
| Abstain | 1.00 | .90 |
| p95 | 21 s | 20 s |
| $/q | .004 | .01 |
**Next design lever, the most promising:** SLOT-FILLING against compression.
1. Decompose the question into the exact information slots it asks for (a name, value, flag, path, list, reason).
2. Extract each slot VERBATIM from the evidence, with its quote.
3. Compose from the filled slots only. This targets the dominant failure directly.
Also: remove the residual attribution and list-overreach claims with the same deterministic subject/list check.
Overnight spend is about $5.2 of the $6 cap.
D-151 | 2026-09-26 | ACCEPTED | **V11 slot-filling is fast but too narrow; overnight experimentation ends (spend about $5.4 of $6).**
| Variant | Correct | Faithful | Recall | Abstain | p95 | $/q |
|---|---|---|---|---|---|---|
| V11 | .385 | .838 | .853 | 1.00 | **17.4 s** (the first variant under 20 s) | .0029 |
| V10 | .615 | .849 | .872 | – | 28.5 s | – |
| memory.ask | .577 | .874 | .814 | – | 21.3 s | – |
Why: the plan makes one slot for two-fact questions (9 cases). About 1.5 slots get filled and 1.4 claims written, against 3.5 in V10. Secondary key facts are stated .48 of the time, against .88.
**Lesson:** slot decomposition under-generates. The combination worth trying next is slots per FACT plus a completeness pass over the filled slots; the latency headroom that V11 bought allows it.
The overnight experiments are closed. Next steps are the owner's morning decision (see OVERNIGHT-2026-09-26.md). The review-79 fixes on memory.ask continue; they need no LLM spend.
D-152 | 2026-09-26 | ACCEPTED (D-125: max 2 review rounds; a HIGH is closed by a reproducing test, not a third round) | **Review 80 (round 2, astra-low + gpt-5.6-sol xhigh, from a clean export of bc333bd): T1, T2, T4-high, T4-med and T5 FIXED (both reviewers agree); T6 was still OPEN and is now fixed at wf-memory-ask 8398656.**
**T6 (both reproduced it).** `polarity_ok` asked only whether the claim had ANY negation, so an unrelated "not" excused a dropped one:
- the quote "The release gate is not enabled by default. It runs weekly, not daily." accepted the claim "… is enabled by default and runs weekly, not daily." (astra) and "… enabled by default and not disabled." (sol).
**Fix: polarity per proposition.**
- Each negation or modal scope in the quotes whose words the claim states must be matched by a claim scope about the same words, and vice versa.
- A scope is the governed side of the polarity word: the right side for EN/DE; the left side for TR değil/yok/olabilir; the other side only when that side is empty. It never crosses a sentence end.
- A first symmetric-window attempt pulled the shared subject ("release gate") into every scope, which blocked a true claim about the other proposition. This was caught by a new test before commit.
**Tests.** Both reviewers' scenarios are regression tests (unit and `validate_answer`), plus true-claim, inserted-negation and sentence-boundary cases. Results: unit 645 passed; memory.ask integration 39 passed on a fresh DB.
**The RUNBOOK:697 residual is fixed in the same commit:** the convergence step names the template's marker, not r3.
**Residuals for the owner (accept/reject, D-125):**
- (1) `map_summary.py:321`: a DB write failure after provider success skips `_failed`, so on an idle DB the retry waits for the next event.
- (2) `provider.py:723-785`: a timeout or unknown-usage attempt charges worst-case USD but not tokens against the per-question token cap.
- (3) Turkish suffix negation ("etkin değildir" is caught, but a verb suffix like "-me/-ma" is not) remains unsupported.
None of these is a leak or an overspend: (1) is a delayed retry; for (2), the USD cap still binds; (3) is a known language limit.
The review-79/80 hardening (contiguous quotes plus polarity) can cost correctness, so memory.ask @ 8398656 is re-measured on the same 34-question dev subset. The result is D-153.
D-153 | 2026-09-26 | ACCEPTED | **The review-79/80 hardening cost correctness through false polarity rejects. The polarity check was tuned on real claims (wf-memory-ask 7f900ba), and memory.ask is back at its pre-hardening level within noise. The 25-answerable dev subset cannot resolve levers smaller than about ±.15.**
**Measured with the strict judge on the same 33 B-dev questions** (B-D006 excluded: the judge's entail call spends its whole 6k-token cap on reasoning and returns no JSON; 4 attempts cost about $0.10):
| Tree | Correct | False abstain | Faithful | Recall | Abstain | p95 |
|---|---|---|---|---|---|---|
| 9bceb59 (pre-review) | 15/25 (.60) | 1 | – | – | – | – |
| 8398656 (review-80 fix) | 10/25 (.40) | **5** | .899 | .80 | 1.00 | 16.9 s |
| 7f900ba (tuned) | 12/25 (.48) | 1 | .892 | .88 | .875 | 21.1 s |
**Cause at 8398656:** the per-proposition polarity check rejected 12 of 111 TRUE claims that 9bceb59 had accepted (the overnight audit found 0 of them false). A dropped main claim then became a guard abstain.
**Tuning, driven by those real claims and without weakening either reviewer's reproduction:**
- clause-bounded windows;
- core versus widened scopes;
- a contrast is kept when the claim names both sides;
- epistemic hedges only, and an added hedge does not fail;
- "X-free" and TR negative-verb suffixes are recognised, with an SOV window;
- soft negations (unable/instead/rather/rejected/yerine) can excuse a dropped "not" but never count as an inserted one.
**Replay:** memory.ask claims 0/211 rejected. The prototype variants V6–V11 (only V10 used for tuning) have 18/531 rejected, all paraphrased negation ("preventing", "refuse", "çıkarılır"). Accepted as the residual false-reject rate: the judge still measures faithfulness end-to-end.
**At 7f900ba versus 9bceb59:** 5 lost and 2 gained. Nothing is systematic: no budget stops, the same steps, similar claim counts. The losses are answer variance (one question saw a model self-abstain).
**Method lesson:**
- At n = 25 answerable questions, the binomial SE at p ≈ .55 is ±.10, so the "±.04 noise" in D-149 was optimistic. Levers must be judged on large effects, or on more questions or replicates.
- The gate needs .80 against about .55, so a big lever is required.
**Gate not reached, so there is no release.** Next: a key-fact miss taxonomy on the 7f900ba outputs, then a ceiling-first prototype of the lever it points to. Morning spend is about $0.65; the shared balance is about $10.6, keeping ≥ $9 for prod.
D-154 | 2026-09-26 | ACCEPTED | **Kept claims that the answer summary dropped are appended (wf-memory-ask 4e88bc0).**
The miss taxonomy on 7f900ba:
- 13 misses: compression 6, retrieval 4 (2 of them really lost at quote level), abstain 1, contradiction 1, facet 1.
- In 4 misses a kept, verified claim held the key fact but the free-text summary left it out.
The fix: `validate_answer` appends every kept claim the answer does not cover, i.e. does not state all its literals plus ≥ 60% of its content words. There is no extra LLM call.
Offline re-judge: +1 (12 → 13 of 25).
It is small because the claims themselves are compressed: "10-20 MB", "STATUS.md" and "REST 64 KiB" never enter a claim. Appending the primary sources' verbatim quotes also changes nothing, because the facts sit in other, uncited documents or lines.
D-155 | 2026-09-26 | ACCEPTED | **Ceiling-first on the metric itself: the D-130 gate is reachable, and the bottleneck is the answer CONTRACT, not the model or retrieval.**
Oracles on the same 25 answerable dev questions, same strict judge:
| Variant | Setup | Correct |
|---|---|---|
| O-span | The candidate is the gold source text itself | .84: the judge ceiling on this set |
| O-ans | The product's answer model (openai/gpt-6-luna, effort low) with a plain "complete and specific" prompt over the gold spans ±3 lines | .84, c+partial .88, 0 contradictions, $0.00015/q |
| **ORACLE-sel** | The same plain prompt over the sources memory.ask ITSELF cited (primary + related) | **.76** |
| memory.ask 7f900ba | Claims + verbatim-quote contract | .48–.52 |
**Conclusion:** about .24 of the gap is the claims + verbatim-quote answer contract, which makes the model compress. Only about .08 is retrieval/selection. The judge accepts complete answers: D021 and D054, suspected judge false negatives, pass for O-ans. The D-150 luna-pro and D-151 slot-filling probes changed the model and the plan, but never this contract.
**Next: V13 "write, then cite".**
1. The model writes a complete answer with a [handle] after each sentence.
2. The server picks each sentence's supporting lines deterministically (greedy cover, at most 3 lines of the cited excerpts).
3. The literal, coverage and polarity checks run as now, and failing sentences are dropped.
This keeps the faithfulness guarantees without the quote contract. It is prototyped offline over the ORACLE-sel contexts first (ceiling-first); spend is cents.
D-156 | 2026-09-26 | ACCEPTED | **Free prose with source citations beats the claims + verbatim-quote contract on BOTH correctness and faithfulness, so memory.ask moves to "V14: write freely, cite handles, verify deterministically".**
Same 25 answerable dev questions, same sources (the handles memory.ask cited), same model (gpt-6-luna), strict judge:
| Answer contract | Correct | Faithful (judge) | Det. check |
|---|---|---|---|
| memory.ask 7f900ba: atomic claims + verbatim quotes | .52 | .89 | 1.00 |
| Free prose ("complete and specific"), each sentence judged against its sources | **.76** | **.922** | .98 |
| V13b: cite exact LINES, then drop sentences whose literals or polarity fail on those lines | .20 | .756 | 1.00 |
| V13: server picks quote lines by word overlap | 14/25 abstain | not judged | – |
Findings:
- The quote contract does not buy faithfulness; it only costs completeness.
- Line-level citation by a cheap model is imprecise: 24% of the kept sentences are not supported by their cited lines.
- Word-overlap quote selection cannot work for TR answers over EN sources.
**V14 design:**
1. The answer step writes complete prose with `[handle]` after each sentence. The handles are excerpt handles; there are no quotes and no lines.
2. The server runs deterministic checks on each sentence against the TEXT of its cited excerpt(s): literals, and polarity.
3. It fixes the literal extraction faults found here: a TR suffix after a closing backtick, slash-joined code spans, and "…" inside code.
4. Failing sentences are dropped. A sentence with no valid citation may be checked against all shown excerpts.
**Measurement:** a sentence's faithfulness is judged against the full text of the cited source unit, which the caller can drill into (D-130: "every source is a handle the caller can drill"). Before, it was judged against a displayed quote. This is recorded as a gate-measurement choice and flagged to the owner with the gate-definition question.
Spend for the oracle and prototype series: about $0.5.
D-157 | 2026-09-26 | ACCEPTED | **V14 "write, then cite" (wf-memory-ask 4c5b457, HLM_RESEARCH_ANSWER_MODE=cite) is memory.ask's best profile so far: 3 of the 6 gate criteria pass. The gate is NOT reached, so there is no release. A strategic pause (D-125) follows: the dev budget is nearly spent.**
Same 33 dev questions (25 answerable), strict judge; V14's faithfulness is judged against the cited source text (D-156 scope):
| Criterion | V14 first (53f5469) | **V14 D-157 (4c5b457)** | claims 7f900ba | Gate |
|---|---|---|---|---|
| Correct | .40 | **.60** | .48 | .80 |
| Faithful | .778 | **.911** | .89 (quote scope) | .95 |
| Abstain | 1.00 | .875 (7/8) | .875 | .90 |
| Source recall | .68 | **.90 ✓** | .88 | .85 |
| p95 | 11.0 s | **12.7 s ✓** | 21 s | 20 s |
| $/q | .004 | **.003 ✓** | .004 | .01 |
| False abstain | .28 | .04 | .04 | – |
**The D-157 fixes over 53f5469:**
- (1) `cite_check` ran polarity against EVERY excerpt line sharing words with the sentence. A "not" about other words dropped true sentences in 8 questions, 6 of them guard abstains. Now polarity is checked against each top cited source's best line only.
- (2) The write prompt asks for short sentences, one fact each.
**Remaining gap to ORACLE-sel (.76, same model and prompt style, over the ≤ 5 sources memory.ask itself cited):**
- 5 questions V14 misses and ORACLE-sel answers: no validation drops; the answers are less complete over 12 excerpts (about 38k chars) than over the focused ≤ 5 (about 9k).
- 5 more questions both miss: the judge ceiling or retrieval.
**Next lever: select, then write.** A small call picks the ≤ 5 excerpts that answer the question, then the answer is written over those only: +1 call, p95 about 16 s.
**Not yet done:**
- V14 is not the default (claims mode stays the default; the R4 manifest will pin the mode).
- The claims-mode baseline has not been re-scored under the source scope.
Owner decisions pending:
- the gate definition (answer text vs answer + cited sources; faithful judged against the displayed quote or the cited source);
- a budget top-up (shared balance about $10.0, ≥ $9 kept for prod).
Today's dev spend is about $1.4.
D-158 | 2026-09-26 | ACCEPTED (the owner delegated the gate definition and the residuals to the consumer: "you will read these answers; the optimal config/setup for your needs is release ready"; they topped up $10) | **Consumer gate v2, PRE-REGISTERED before any reference or new memory.ask result is seen. NotebookLM chat over the same sources is the reference system; the owner used it as a project memory and rates its source Q&A as very good.**
**Who judges "good":** the consumer is a Claude coding agent. What it needs from `memory.ask`, in order:
- (1) never act on a wrong fact;
- (2) a clear "not in memory" when it is not;
- (3) the facts needed to act, directly in the answer, or at most one precise drill away;
- (4) exact source handles;
- (5) speed, cost and compactness (LLM-to-LLM efficiency).
**Criteria.** All must pass on the FINAL sets: the untouched PR set (60) on the current-tree replica and sealed B (50), as D-130 intended. Dev iteration uses the 33-question B-dev subset only.
| Criterion | Definition | Pass |
|---|---|---|
| C1 Utility (primary) | A blind Claude rater (a fresh subagent, the consumer model family) sees the question, the gold answer and each system's response exactly as an agent would receive it, with system names hidden and order shuffled. It rates each question 0/1/2. Answerable: 2 = actionable and correct without another lookup; 1 = correct and points to a source holding the rest (one drill); 0 = wrong, contradicts gold, or misses the main point. Negative: 2 = clearly "not in memory"; 0 = asserts an answer. | mean U ≥ mean U(NotebookLM) − 0.05, and U = 0 share ≤ 0.10 |
| C2 Key-fact correct | Strict judge v1 | ≥ .70 and ≥ correct(NotebookLM) − .05 |
| C3 No wrong facts | Judge contradiction rate; faithful against the cited SOURCE text (D-156 scope) | contradiction ≤ .04; faithful ≥ .92 |
| C4 Abstain | Negatives | ≥ .90 |
| C5 Source recall | – | ≥ .85 |
| C6 Efficiency | p95 latency; cost; median response size | p95 ≤ 20 s; ≤ $0.01/q; median response ≤ 1,200 tokens |
**Rationale for the changes from D-130:**
- C2: correct .80 was set before the ceiling was known. With the GOLD sources the answer model scores .84, which equals the judge ceiling on dev (D-155), so .80 demands near-perfect retrieval. The reference system anchors adequacy, and C1 measures actual consumer utility.
- C3: .92 judged ≈ ≥ .94 true, since D-150 found about 30% of the judge's faithful rejections actually entailed. Contradiction is capped separately because a wrong fact is the costliest failure.
**Reference:**
- The NotebookLM notebook holds exactly the corpus B sources imported into the dev DB (89 docs at 82200ae + automem + the git episodes), bundled to ≤ 50 sources and secret-scanned.
- It gets the same questions, with a fresh conversation each, scored by the same judge and the same rater.
- NotebookLM has no source handles, so C5 does not apply to it.
- Hold-out questions go to NotebookLM only at the final gate: that is a measurement, not tuning.
**Residuals of review 80, decided by the consumer:**
- (1) The map_summary DB-write failure delays the retry to the next event: ACCEPTED. Summaries are a cache; a restart or any event re-arms it.
- (2) A timeout charges USD but not tokens: ACCEPTED. The USD cap is the binding one.
- (3) TR suffix negation: RESOLVED by D-153 (TR negative-verb suffixes are read as negation).
D-158a | 2026-09-26 | ACCEPTED (consult 81, gpt-6-astra low, 1 round per D-125; recorded BEFORE any NotebookLM or new memory.ask result was seen) | **Consumer gate v2.1: D-158 tightened after an independent check. The implementer defined the gate, so the goalposts must not move.**
**Adopted:**
- (1) **C2 correct stays ≥ .80**, plus ≥ correct(NotebookLM) − .05. The .84 oracle is 21 of 25, not a proven ceiling. The .70 floor is withdrawn.
- (2) **C3 faithful stays ≥ .95** in the source scope. A discount needs an independent blind calibration of judge accepts and rejects in that scope, done before the final run.
- (3) **C1 floors:** mean U ≥ 1.6 on answerable questions, and U = 0 share ≤ .10, in addition to the reference comparison.
  - A U = 1 ("one drill finishes it") counts only if that drill is actually executed on the named handle and the missing gold facts are in the returned text; otherwise it becomes 0.
  - The rater never sees system identities.
  - Rubric consumer_v1 is frozen (wf-research-proto fba8304).
- (4) **C3 also at answer level:** the share of answers containing any wrong actionable claim (a contradiction, or a superseded value stated as current) must be ≤ .04. Temporal questions are reported separately.
- (5) **C7 zero scope leakage** (from D-130) is an explicit release condition. The review-79 T1 isolation tests must be green, and a cross-project probe must pass on the release candidate.
- (6) **The final protocol is frozen before the run:** code sha, config, prompts, rubric, corpora, question lists, and one run with no best-of-N.
  - PR set and sealed B must pass SEPARATELY.
  - Every final question stays in the denominator. A judge runaway gets one retry with a higher output cap; if it still fails, it goes to independent adjudication, never exclusion.
  - The NotebookLM reference gets a same-content corpus per set (corpus B for sealed B; the current tree for the PR set).
  - After a failed final, changes require a new hold-out.
- (7) **C6 adds** an error/timeout rate ≤ .02 and reports p99, response tokens (median and p95), and answer-plus-drill time.
**Partly adopted:** paired bootstrap CIs are REPORTED for every reference comparison, but the point estimate gates. At n = 50–60 the CI is about ±.10, so "the CI must exclude −.05" would be unpassable by construction.
**Not adopted:** removing the check of uncited sentences against all excerpts. Such a sentence is shown attributed to the excerpt(s) that hold its literals and words, the judge checks entailment against exactly those, and the count is reported (`uncited`).
D-159 | 2026-09-26 | ACCEPTED | **Select-then-write (HLM_RESEARCH_SELECT, wf-memory-ask 642dc7a + 2761c6c) hurts correctness, so the flag stays OFF.**
Same 33 dev questions, consumer gate v2.1 criteria:
| Variant | Correct | Faithful (source) | Recall | Abstain | False abstain | p95 |
|---|---|---|---|---|---|---|
| V14 + select | .40 | **.957** | .78 | 1.00 | .12 | 14.2 s |
| V14 D-157 | .60 | .911 | .90 | .875 | .04 | 12.7 s |
**Why it fails:**
- The select job keeps too little: 3 excerpts in 14 questions, 1–2 in 8, none in 7. In the 7 questions lost against D-157, the answers are complete over what was selected but miss facts in excerpts that were left out.
- The model's own relevance guess is weaker than the ORACLE-sel selection, which came from an answer pass (claims-mode primary + related).
- The 7 `budget_stop`s are 6 negative questions plus B-D010: select [] → write → refine → select [] → no budget for a second write. The abstentions come out right, just at higher cost.
**Kept from the experiment:** the select-job output cap is 800 tokens, because reasoning counts against the cap.
**Next:** an offline test of the product's V14 write prompt + `validate_cited` over the ORACLE-sel contexts. It separates the context-selection effect from the answer-format effect in the .60 → .76 gap.
D-160 | 2026-09-26 | ACCEPTED | **The NotebookLM reference is measured, and the per-sentence citation instruction by itself costs about .3 correct. Next: V15 "write freely, then attribute separately".**
**Reference (D-158):** a private NotebookLM notebook (id in docs/private) holds all 91 dev-DB items (bodies) as 36 sources, secret-scanned with 0 hits. It was asked the same 33 questions, each through a fresh `nlm query` CLI process: the MCP tool reuses the notebook's cached conversation, which would make every question a follow-up.
| System | Correct | c+partial | Abstain | Contradiction | p50 / p95 | Median answer |
|---|---|---|---|---|---|---|
| **NotebookLM** | **.88** | .92 | **.125** (fabricates on 7/8 negatives) | .04 | 22.9 / 37.0 s | 1,305 chars |
| memory.ask V14 D-157 | .60 | .64 | .875 | .04 | 5.7 / 12.7 s | ~400 chars |
Under the pre-registered C2, memory.ask must reach **≥ .83** (NotebookLM .88 − .05) as well as ≥ .80. NotebookLM itself would fail C4 (abstain) and C6 (p95).
**Citation finding (same model, same ORACLE-sel sources):**
| Variant | Correct |
|---|---|
| Plain "complete and specific" prose, no citations (ORACLE-sel) | .76 |
| Same prompt + "cite [handle] after every sentence", validated by the product (V13-prod) | .44 |
| The product's V14 write prompt, JSON sentences + cites (V14-on-sel) | .52 |
Validation dropped only 4/61 and 2/59 sentences, so the loss is in GENERATION. Asking the cheap model to write and cite at once makes it compress.
**Next: V15.** The write step gets the plain prompt and no citation duty (longer answers allowed). A separate small attribution call maps each sentence to excerpt ids, then `validate_cited` runs as in V14. Ceiling-first: V15-on-sel attributes the existing ORACLE-sel prose offline.
D-161 | 2026-09-26 | ACCEPTED | **On free prose, every validation layer we tried loses true facts without raising judged faithfulness, and a longer writing prompt adds nothing. The remaining levers are the MODEL (configuration, D-017) and a realistic faithfulness calibration.**
Offline: same 25 answerable dev questions, same ORACLE-sel sources (the handles memory.ask cited), same luna model, strict judge, faithfulness in source scope.
| Variant | Correct | Faithful | What it adds |
|---|---|---|---|
| Free prose, unvalidated (ORACLE-sel) | **.76** | **.922** | – |
| Free prose, "exhaustive, 5–12 short sentences" | .72 | – | +24% length |
| + separate attribution + product `validate_cited` (V15) | .52 | .918 | 17/102 sentences dropped: literal 11, polarity 5 |
| same, re-attributing literal failures (V15r) | – | – | 3 of 11 were attribution errors; the other 8 are checker false positives |
| + LLM verifier (luna, supported true/false) + hard-literal check (V15b) | .44 | .894 | 14 supported=false and 3 hard-literal drops |
**The checker false positives are extraction limits, not hallucinations:**
- a slash joining code and a word;
- bold markup plus a TR suffix;
- "…" inside a code span splitting a sentence;
- quoted prose phrases and § references taken as literals;
- TR future negation (-mayacak) and semantic negation ("replaced", "preventing", "neither…nor").
A cheap verifier disagrees with the judge in both directions.
**Conclusion:** the deterministic and cheap-LLM checks cannot tell a paraphrase from a fabrication on free prose. Each one costs correctness, and none reached the judged faithfulness of the unvalidated prose.
**Two measurements are next, both offline:**
- (a) a model sweep with the plain prompt over the ORACLE-sel sources: gemini-3.8-flash, claude-haiku-4.5 and kimi-k2.6. DeepSeek is excluded because it is the judge's family.
- (b) NotebookLM's own faithfulness against its citations, to learn whether ≥ .95 under this judge is realistic for a strong system before any calibration argument.
D-162 | 2026-09-26 | ACCEPTED | **The model sweep and NotebookLM's faithfulness fix the architecture: memory.ask moves to a "prose" answer mode (V16). The remaining lever is retrieval.**
**Model sweep** (plain prompt, same ORACLE-sel sources, 25 answerable):
- luna 19/25 (.76);
- gemini-3.8-flash 17/25 (.68; $.0039/q, one answer cut by its reasoning);
- claude-haiku-4.5 and kimi-k2.6 stopped by the cap after one probe each (kimi spent 1,975 reasoning tokens on 62 chars).
The model is not the lever.
**NotebookLM against its own citations:** 164 references mapped verbatim onto HLMemo items, 204 statements, strict judge in quote scope.
- faithful **.613** overall;
- **.71 on answerable questions** (≈ .76 excluding 2 judge runaways, B-D034 and B-D066);
- det .897;
- it fabricates on 7/8 negatives.
It reaches .88 correct by writing long answers that go beyond what its sources state. For a consumer that acts on the answer, that is the costliest failure. Free prose on luna over memory.ask's own sources is **.922** faithful at .76 correct.
**Where the gap to NotebookLM's correctness sits:** 7 questions NotebookLM gets and luna-over-ORACLE-sel misses. Most of them (D001, D002, D050, D053) have the key fact in a source memory.ask did not cite. That is retrieval.
**V16 "prose" mode (HLM_RESEARCH_ANSWER_MODE=prose, prompt research/v3):**
1. The write job gets the plain "complete and specific" prompt plus the abstain and current-vs-earlier rules. It returns {status, answer, sources (≤ 6), related, confidence}, with no per-sentence citation duty.
2. The server splits the answer into sentences and drops a sentence ONLY if one of its HARD literals appears in no shown excerpt: a digit, or a backticked identifier, with the extraction fixes of D-161. This is the fabricated-value guard.
3. Each sentence is attributed deterministically to its best source line, for display and measurement. A polarity mismatch against that line FLAGS the claim (`flags: ["polarity"]`) instead of dropping it.
4. There is no check/repair call, and refine-on-abstain stays as today.
**Faithfulness ≥ .95 (C3) stays.** The calibration audit that consult 81 requires (an independent blind review of judge rejections in source scope) will decide the realistic reading. The threshold is not changed here.
D-163 | 2026-09-26 | ACCEPTED | **Retrieval diagnostic: retrieval explains at most 3–4 of V14's 10 dev misses. Writing is the bigger lever (V16), and item-level fusion is the retrieval fix to take next.**
The question and the plan queries of the V14 D-157 run were replayed through memory.query with the LLM off, taking the rank of the first hit whose chunk ±1 holds the gold span:
- 15/25 correct: all had their gold cited, at best rank ≤ 6.
- **9 misses had the gold surfaced (rank ≤ 12); 6 of those had it in primary/related.** The loss there is in writing: D009, D017, D018, D021, D034, D053.
- 0 misses had the gold ranked 13–40.
- 1 miss never surfaced it: D050. Its golds are markdown table rows in chunks that start mid-table, with the header and caption in earlier chunks, asked in Turkish.
**Mechanisms found:**
- memory.query returns one chunk per item. A long doc ranks high but shows a sibling chunk, so the gold chunk cannot appear: D002, D010, D062.
- memory.ask fuses by CHUNK handle, which splits a doc's votes across queries: ROADMAP is #1 in 4 of 5 queries but fused #7.
- `_doc_best` finds the right in-document chunk (#1 for D010 and D062) but only fills free drill slots, and the ranked list always fills all 12.
**Fixes, in order:**
- (1) V16 prose mode, for the 6 writing misses.
- (2) Fuse per item, one vote per item per query, and let `_doc_best`'s chunk for the top-4 items take drill slots. What-if: gold-in-excerpts 20 → 21 of 25 (D010), no losses.
- (3) Later, with a re-import: table-aware chunking that repeats the header row and nearest heading or caption in a chunk that starts inside a table (D050).
D-164 | 2026-09-26 | ACCEPTED | **V16 prose mode (wf-memory-ask 2b46633) end-to-end: the best correct/abstain/latency so far, but faithfulness is .828 and is under audit.**
Same 33 dev questions; token_budget 6000 so claims are not trimmed; faithfulness in source scope:
| Criterion | V16 prose | V14 D-157 | Gate v2.1 |
|---|---|---|---|
| Correct | **.64** | .60 | ≥ .80 and ≥ .83 |
| Abstain | **1.00 ✓** | .875 | ≥ .90 |
| Faithful | .828 | .911 | ≥ .95 |
| Contradiction (answer level) | .08 | .04 | ≤ .04 |
| Source recall | .90 ✓ | .90 | ≥ .85 |
| p95 | **10.7 s ✓** | 12.7 s | ≤ 20 s |
| $/q | .003 ✓ | .003 | ≤ .01 |
Of 99 statements, 3 were dropped by the hard-literal guard and 6 carry polarity flags.
Re-scoring the same answers with every sentence attributed to the first 3 of primary+related gives the same faithfulness (.818), so the product's per-sentence attribution is not the cause.
Over 12 excerpts the prose is less faithful than over ORACLE-sel's ≤ 5 (.922), OR sentences rest on a 4th+ source that the judge's 3-source cap cannot see, OR the judge errs.
A per-statement audit (JUDGE_FN / CLIPPED / ELSEWHERE / OVERREACH / UNSUPPORTED / CONTRADICTED) decides which. It is also the start of the calibration that consult 81 requires; the independent confirmation comes later.
D-165 | 2026-09-26 | ACCEPTED (consult 82 = the independent calibration that D-158a requires) | **V16's faithfulness loss is attribution plus measurement scope, not fabrication. This is confirmed blind by codex. It sets the faithfulness measurement ("item" scope) and the final-run adjudication protocol.**
**Internal audit** (Claude, per statement, all 17 judge rejections among 99 V16 statements): JUDGE_FN 6, CLIPPED 4 (in the cited item, outside the drilled chunk), ELSEWHERE 7 (another SHOWN source states it: an attribution error), OVERREACH / UNSUPPORTED / CONTRADICTED 0.
**Independent blind check** (gpt-6-astra low; 25 statements = the 17 rejections + 8 random accepts, shuffled, no labels):
| Internal class | Codex label |
|---|---|
| JUDGE_FN | S 6/6 |
| CLIPPED | W 4/4 |
| ELSEWHERE | E 6/7, P 1/7 |
| Accepted sample (8) | S 6, W 1, E 1 |
**U = 0, C = 0.** The agreement is 16/17 on the rejections.
**Measurement (wf-research-proto 348ecb2):** `--faithful-scope item` gives the judge each cited source's TITLE plus a ≤ 6,000-char window of its item body centred on the cited chunk: what a caller sees when it drills. V16 re-scored: .828 → **.869**.
Attribution errors (E) are NOT absorbed by the scope: they are product defects, because the caller would open the wrong source. WS-A attacks them with attribution over ALL shown excerpts plus multilingual embedding similarity, and item-level fusion.
**Final-run faithfulness protocol (C3), frozen now:**
1. Faithfulness is judged in item scope.
2. EVERY judge rejection goes to independent blind adjudication (codex, labels S/W/E/P/U/C). S and W count as faithful; E, P, U and C count as unfaithful.
3. A blind random sample of 20% of the judge accepts is adjudicated the same way, and the observed false-accept rate is applied to the remaining accepts.
4. faithful = (adjudicated faithful rejections + accepts × (1 − sampled false-accept rate)) / statements ≥ .95.
5. The contradiction rate (C, plus the judge's answer-level contradictions) stays ≤ .04.
The polarity flags in prose mode were 6/6 false positives and are removed (WS-A).
D-166 | 2026-09-26 | ACCEPTED (owner) | **Product principle: HLMemo never performs external research. `memory.ask` answers only from memory.**
The owner, after the NotebookLM comparison: NotebookLM's "research" feature is one HLMemo must NOT have. The LLM working on the project (or the owner) does the research, refines it, and then writes the result into memory. That is the right and optimal flow.
Consequences:
- `memory.ask` stays read-only over the caller's memory, as D-130 already specifies.
- No web or source-discovery tool is added.
- New knowledge enters only through `memory.write` / import by the working agent.
The owner also notes the advantages HLMemo has over a NotebookLM-style tool beyond the measured comparison, all of which are in the design:
- cross-project reads when needed (D-083 policy);
- a project-independent experience layer (L4, Phase 4);
- no source limit.
D-167 | 2026-09-26 | ACCEPTED | **Item-level fusion stays: gold-in-cited .76 → .90, correct .64 → .68. The "wide + embedding" attribution is reverted to a measurable choice.**
End-to-end on wf-memory-ask 0db86e2 (prose mode, 33 dev questions, item scope):
| Criterion | V16 2b46633 | 0db86e2 (wide attribution + item fusion) |
|---|---|---|
| Correct | .64 | .68 (gained D010, D021, D062; lost D009, D054) |
| Source anchor among cited | .76 | **.90** |
| Faithful (item) | .869 | .765 |
| Abstain | 1.00 | .875 |
| p95 | 10.7 s | 9.6 s |
**Item fusion (0db86e2):** one vote per item per query, and the top-4 items' best in-document chunks take drill slots. It does what D-163 predicted.
**Wide attribution (cbc4297):**
- every shown excerpt is a candidate, plus multilingual embedding similarity;
- it attached sentences to semantically similar lines that do not support them;
- embedding also covered only part of each request in time (35 s embed time summed over 34 questions).
**Next:**
1. `HLM_RESEARCH_ATTRIBUTION` ∈ {sources (V16), wide, llm (one extra "attribute" call)}.
2. `meta.excerpts_shown` so strategies can be replayed OFFLINE on the SAME answers; only the judge costs.
3. A pure `attribute(...)` function for that replay.
The winner is chosen by faithfulness (item scope, same answers), then adjudicated per D-165.
D-168 | 2026-09-26 | ACCEPTED (pre-registered BEFORE any adjudication result; consult 81: "if gold/judge defects are verified, fix the measurement and rescore both systems") | **Symmetric correctness adjudication for C2, and the llm attribution result.**
**Why:** on free prose the key-fact judge shows errors that the claims-mode audit (D-150, 0 FN) never saw:
- B-D009 states both key facts but writes the log path in full (`/opt/hlmemo/.deploy-runs/<run-id>/log` against the gold's `.deploy-runs/<run-id>/log`), and is marked a CONTRADICTION.
- B-D017 states both reasons, but the gold also lists the backend names.
- B-D053 wording ("Caddy limit" against "Caddyfile max_size").
**Protocol (dev now, final run later, identical for memory.ask AND the NotebookLM reference):**
1. Every answerable question the judge marks incorrect goes to blind codex adjudication, plus a random 20% of those it marks correct.
2. The adjudicator sees the question, the frozen key facts and the answer, with no system identity. It labels ALL (every element of every key fact stated: paraphrase and equivalent formatting accepted, parentheticals count as elements), MISSING (list them) or CONTRADICTS (states something incompatible with a key fact; extra CORRECT detail such as a fuller path is not a contradiction).
3. Adjudicated correct = ALL.
4. C2 uses adjudicated correctness for both systems, and the raw judge numbers are reported alongside.
The same applies to answered NEGATIVE questions (C4). A premise correction grounded in memory ("HLMemo does not use Elasticsearch; lexical search is Postgres tsvector", B-D079) is adjudicated as ABSTAIN-EQUIVALENT or FABRICATED.
**llm attribution (wf-memory-ask a0d91d0, HLM_RESEARCH_ATTRIBUTION=llm):**
- correct .68, abstain 1.00, faithful (item) .878, contradiction .04, recall .94, p95 11.5 s, $.004/q;
- 97 sentences LLM-cited, 7 fallbacks.
Current best profile. Its C3 is decided by the D-165 adjudication.
D-169 | 2026-09-26 | ACCEPTED | **First complete dev picture under the frozen final protocol (a0d91d0, prose + llm attribution): 5 of 7 consumer-gate criteria pass. C2 and C3 fail, both on writer behaviour.**
| Criterion | memory.ask | NotebookLM | Gate | Result |
|---|---|---|---|---|
| C1 consumer utility (blind fresh-Claude rater, rubric consumer_v1 + drill rule) | **1.97** (U2 .97, U0 0) | 1.85 (U2 .85) | ≥ 1.6, ≥ NLM − .05, U0 ≤ .10 | PASS |
| C2 correct (judge + blind codex adjudication, D-168) | **.72** (judge 17/25; D009 overturned → 18/25; 0/4 false accepts) | .88 (judge 22; D054 overturned, D050 a false accept) | ≥ .80 and ≥ .83 | FAIL (−3 questions) |
| C3 faithful (item scope + blind adjudication, D-165) | **.867** | .71 (judge, quote scope) | ≥ .95; contradiction ≤ .04 | FAIL |
| C4 abstain (adjudicated) | **1.00** (B-D079 = premise correction) | .375 (5 of 8 fabricated) | ≥ .90 | PASS |
| C5 source recall | .94 | – | ≥ .85 | PASS |
| C6 p95 / cost | 11.5 s / $.004 | 37 s / – | ≤ 20 s / ≤ $.01 | PASS |
| C7 isolation | review-79 T1 tests green | – | + probe at the final run | PASS (probe pending) |
**C3 breakdown** (11 judge rejections): S 4 (judge errors), E 2 (attribution), **P 4 (overreach: added causal glue or rationale, e.g. "this created compatibility and maintenance burden")**, **C 1 ("STATUS.md says X", but X is only in D-050)**. Sampled false-accept rate: 1/16 (P).
**C2 misses:**
- omitted specifics: D018 "10–20 MB", D053 the REST 64 KiB limit, D061 the command and flag names, D022 the actor (Codex) and the "entrenches mistakes" warning;
- retrieval: D002, D050;
- a judgement call: D017.
**The rater** (fresh Claude) found memory.ask actionable in 32/33 cases and gave NotebookLM 1s for invented reasons on negatives. It rated B-D079 1, because it never literally says "no Elasticsearch".
**Next, prompt research/v3.1 for the prose JOB only, with two short rules:**
- **(a) No overreach:** state only what the excerpts state; no cause, effect, purpose or conclusion they do not state; never attribute a statement to a document that does not contain it.
- **(b) Specifics:** give every value, name, command, flag, path, limit and actor the excerpts state for what the question asks, including sibling values in the same statement (other routes, earlier values); when rejecting a false premise, say explicitly that the memory has no such thing.
Then one end-to-end run and the same adjudication.
D-170 | 2026-09-26 | ACCEPTED | **Prompt v3.1 raises faithfulness (judged .878 → .907) without moving correctness beyond noise. The next lever is a completeness ("expand") pass.**
Same 33 dev questions; prose + llm attribution; judge, item scope.
| Metric | a0d91d0 (v3) | **61f6712 (v3.1)** |
|---|---|---|
| Correct | .68 | .64 |
| Faithful | .878 | **.907** |
| Contradiction | .04 | .04 |
| Abstain | 1.00 | .875 (a premise case) |
| Recall | .94 | .88 |
| p95 | 11.5 s | 12.5 s |
| $/q | .004 | .004 |
Correct changes: gained D018 ("10–20 MB" now stated) and D061; lost D010, D034, D038. Correctness keeps moving in the .64–.72 band, the ±.09 noise at n = 25.
**Two harness faults found and fixed today:**
- (1) The dev api's default spend caps (1/1/60) tripped after about 8 runs a day on one DB. memory.ask then correctly returned E_UNAVAILABLE, and the harness scored those as wrong answers and still paid the judge. The first 61f6712 run is quarantined under `results-research-prototype/invalid/`. Dev runs now use `--caps 2,5,60` and abort before judging when more than 2 questions error.
- (2) A judge runaway (reasoning uses the whole output cap, no JSON) crashed the scoring. It now gets one retry at twice the cap, then independent adjudication (`judge_adjudicate` in the summary): no crash, no silent pass (wf-research-proto cbbd1b9).
**Next:** HLM_RESEARCH_EXPAND, a second call that adds up to 6 sentences stating further question-relevant specifics from the same excerpts, under the same hard-literal guard. It targets the MISSING facts that keep C2 at about .70, where NotebookLM writes answers about 3× longer.
D-171 | 2026-09-26 | ACCEPTED | **The expand pass (wf-memory-ask fedced4) is not a clear win, so HLM_RESEARCH_EXPAND stays OFF. C2 has plateaued at about .70 with the current writer model; a strategic pause follows.**
Same 33 dev questions; judge; item scope.
| Metric | v3.1 61f6712 | + expand fedced4 |
|---|---|---|
| Correct | .64 | .72 (+D009, D017, D038, D050; −D001, D018) |
| Faithful | .907 | .912 |
| Abstain | .875 | .75 (expand added sentences to negative-question answers) |
| Recall | .88 | .84 |
| p95 | 12.5 s | 16.0 s |
| $/q | .004 | .005 |
Expand added only 17 sentences over 34 questions and dropped 2 of them by the literal guard; the median answer is 466 chars. The correct gain is inside the ±.09 noise, while abstain, recall and latency are worse.
**A scorer robustness fix** (wf-research-proto b094458): a malformed judge verdict (`{"contained": true}` for a 2-fact question) is now left to adjudication instead of crashing the run.
**State of C2:** across 5 end-to-end variants on the writer luna (prose modes), judged correctness sits in .64–.72; adjudicated in the D-169 run it is .72. The gate needs ≥ .83. Every other criterion is at or near its threshold on dev.
**Remaining lever:** a stronger writer model for the prose job only (per-job profile). It trades against C6 (≤ $0.01/q) and the prod budget of $10/month, so it is presented to the owner with the numbers.
D-172 | 2026-09-26 | ACCEPTED (owner: "if this quality is enough for you to work successfully and smoothly, accept it; if it would slow you down, tire you or cause problems on big or intense projects, go with your recommendation") | **Consumer verdict: the ~.70 C2 quality is NOT yet enough for large or intense projects, so the writer model is upgraded, time- and cost-boxed.**
**Why, as the consumer:**
- (1) On this project's small memory the answers are usable: C1 is 1.97, and the misses are secondary details one drill away. But completeness degrades as memory grows. More sources mean more distractors, and the cheap writer compresses more (D-155: .76 over ≤ 5 focused sources against about .65 over 12). That means more drills and re-asks.
- (2) C3 is also short: .91 judged against .95. The misses are occasional overreach ("therefore…", an unstated rationale). On intense work every such sentence has to be re-verified, which is the "tiring" failure.
- Better instruction-following addresses both.
**Plan (≤ 1 day, ≤ $3 dev):**
1. An offline writer sweep over the EXACT end-to-end contexts (`meta.excerpts_shown` of the v3.1 run), holding the prose prompt, excerpts and questions fixed:
   - luna at effort low (baseline replicate) and medium;
   - luna-pro;
   - mistral-large-2512;
   - glm-5.
   Sonnet 5, gpt-6-sol and Haiku 4.5 are excluded because they cost about $0.017–0.034 per question against C6's $0.01. gemini-3.8-flash is excluded as borderline cost and weak in D-162.
2. Implement a per-job writer profile (configuration, D-017) for the best candidate, then run end-to-end plus the D-165 and D-168 adjudication.
3. If C2 or C3 still falls short, the residual goes back to the owner with the numbers.
Prod cost note: memory.ask is about $0.004/q today. A writer at ≤ $0.007/q keeps 20–50 questions a day at about $3–10/month, inside the prod cap of 10.
D-173 | 2026-09-26 | ACCEPTED (owner: "since we are trying other models and efforts, we will exceed the planned $10/month; so let's find the model that can do this job successfully at the best price/performance"; hint: OpenAI's gpt-5.6 models are pricier but more capable than the gpt-6 ones) | **The monthly $10 prod budget is no longer a hard cap. The writer model is chosen on price/performance, and C6's per-question cost limit is re-set with the owner after the sweep.**
The sweep adds 4 runs:
- gpt-5.6-luna ($0.2/$1.2 per M) at effort low and medium, about $0.004/q for the writer;
- gpt-5.6-terra ($2/$12);
- gpt-5.6-sol ($2/$10), about $0.03/q each.
These join luna low/medium, luna-pro, mistral-large-2512 and glm-5. The dev spend cap for the sweep is $3.
**Selection rule, written before the results:** among the writers that reach C2 ≥ .80 on the sweep AND keep C3/C4 at their thresholds in the end-to-end run, choose the cheapest. If none reaches .80, choose the best correct per dollar and present the residual to the owner.
The C6 latency limit (p95 ≤ 20 s) stays. The C6 cost limit moves from $0.01/q to an owner-confirmed value, proposed together with the chosen writer's measured cost.
D-174 | 2026-09-26 | ACCEPTED | **Writer sweep over the EXACT end-to-end contexts: GLM-5 writes at NotebookLM's correctness level (22/25) with 0 contradictions, at about $0.01/q for the writer; latency is its one problem.**
The sweep held everything else fixed:
- the prose prompt research/v3.1;
- the same 34 questions, with each question's 12 excerpts from the 61f6712 run (request sha256 matched 34/34);
- the same judge (correctness + abstain).
| Writer | Correct | Abstain | Contradictions | Writer $/q | Latency median / p95 |
|---|---|---|---|---|---|
| gpt-6-luna low (current) | .60 | 7/8 | 2 | .0015 | 2.5 / 3.9 s |
| gpt-6-luna medium | .72 | 7/8 | 0 | .0016 | 3.3 / 5.6 s |
| gpt-6-luna-pro | .72 | 7/8 | 0 | .0021 | 7.0 / 11.9 s |
| gpt-5.6-luna low | .72 | **8/8** | 0 | .0030 | 2.9 / 5.6 s |
| gpt-5.6-luna medium | .72 | 7/8 | 1 | .0031 | 3.4 / 6.4 s |
| mistral-large-2512 | .76 | **1/8** | **4** | .0069 | 6–30 s (rate-limited) |
| **glm-5** | **.88** | 7/8 | **0** | .0099 | 10.3 / **31.9 s** |
**Reading:**
- The cheap writers plateau at .72.
- mistral is ruled out on abstain and contradictions.
- glm-5 matches NotebookLM (.88), but its writer p95 alone exceeds the 20 s end-to-end gate.
- gpt-5.6-terra and gpt-5.6-sol (about $0.03/q) were NOT run: the auto-mode permission check blocked the projected spend (about $2.1), so it needs the owner's explicit approval. They are unnecessary if glm-5's latency can be fixed.
**Next:**
- (1) glm-5 latency probes: OpenRouter provider sort by latency or throughput, and reduced reasoning.
- (2) Per-job writer profile `HLM_RESEARCH_WRITER_PROFILE` plus an `openrouter-glm5` profile (configuration, D-017).
- (3) An end-to-end run with the D-165 and D-168 adjudication and the C1 rater.
The estimated full cost with the glm-5 writer is about $0.013/q, roughly $8–20/month at 20–50 questions a day.
D-175 | 2026-09-26 | ACCEPTED | **Writer configuration chosen: glm-5 with reasoning OFF, Baidu excluded, latency-sorted providers, plus a writer timeout that falls back to the task profile.**
Latency and quality probes over the same 34 end-to-end contexts:
| Variant | Correct | Abstain | Contradictions | Latency median / p95 | $/q (writer) |
|---|---|---|---|---|---|
| glm-5 default (reasoning on) | 22/25 | 7/8 | 0 | 10.3 / 31.9 s | .0099 |
| glm-5, provider sort=latency (replicate) | **22/25** | 6/8 | 1 | 11.4 / 33.0 s | .011 |
| **glm-5, reasoning `{"enabled": false}`, sort=latency, ignore Baidu** | **21/25** | 6/8 (premise cases → adjudication) | 1 | **6.0** / 29.2 s | **.0075** |
**Findings:**
- The .88 is reproducible (22/25 twice).
- Turning reasoning off costs at most one question (within noise), halves the median latency and cuts cost by 25%.
- `{"effort":"low"}` is ignored by this model.
- Baidu is the slow upstream (21 s median).
- The remaining tail (3/34 at 29–42 s) is upstream variance. It is cut by `HLM_RESEARCH_WRITER_TIMEOUT_S` (default 12 s): on timeout the task profile (gpt-6-luna, 2–4 s) writes the answer. The expected cost is about 9% of questions at luna quality, ≈ −.014 correct.
**Profile** `profiles/openrouter-glm5.toml` carries the final values. The estimated full cost per question is about $0.010 (writer $0.0075 plus plan and attribution on luna), roughly $6–15/month at 20–50 questions a day.
**Next:** the end-to-end run with the writer profile and timeout, then the D-165 and D-168 adjudication and the C1 rater. terra and sol stay unrun: they are no longer needed unless glm-5 fails end-to-end.
D-176 | 2026-09-26 | ACCEPTED | **glm-5's latency on OpenRouter drifts too much for a 20 s p95. The quality holds, but the writer timeout fires on up to half the questions.**
**End-to-end runs of the final configuration** (prose + llm attribution + writer openrouter-glm5 with reasoning off, no Baidu, latency sort; writer timeout 12 s; dev per-question cap raised to $0.03 because the glm-5 worst-case reservation, about $0.014, does not fit under $0.01, the R4 manifest's current limit):
| Run | glm-5 wrote | Timeouts | Breaker opens | Correct | Faithful | Contradiction | p50 / p95 | $/q |
|---|---|---|---|---|---|---|---|---|
| fba91c2 | 18/34 | 4 | 12 skipped (the timeout cuts tripped the breaker: a bug, fixed in 46ea7e6) | .64 | .941 | .08 | 11.1 / 21.4 s | .0103 |
| 46ea7e6 | 17/34 | **17** | 0 | .60 | .83 | .16 | 18.3 / 36.4 s | .0145 |
Where glm-5 wrote, it was better (fba91c2: 7/9 against luna 9/16), but in 46ea7e6 the upstream latency drifted and half the writer calls hit the 12 s cut. The earlier probe (lowr2) saw 3/34 over 12 s. The latency depends on which upstream provider serves the call at that hour.
**Options:**
- (1) Pin the fastest upstream (StreamLake) with no fallback: probe running.
- (2) OpenAI-served gpt-5.6-terra or sol: latency should be stable, at about $0.03/q, and the test spend (about $2) needs the owner's approval because the permission check blocked it.
- (3) Relax C6's p95 (20 s): an owner decision.
D-177 | 2026-09-26 | ACCEPTED | **The GLM family is not viable as the prod writer: capacity and latency are too volatile. The candidates left are big-vendor models with stable serving, and their test spend needs the owner's approval.**
Probes on the same 34 end-to-end contexts:
- **glm-5, pinned to StreamLake** (the fastest upstream in lowr2) with no fallback: 6/6 calls returned HTTP 529 "system overloaded".
- **glm-5.3** ($0.379/$1.192, 40 endpoints): reasoning is mandatory (`{"enabled": false}` → 400). Median 58.5 s, p95 77 s, a mean of 4,077 reasoning tokens, 8/11 calls hit the 3,000-token cap with no JSON. Stopped after 7 questions.
- **glm-5.3-flash** ($0.04/$0.5, 33 endpoints): reasoning is mandatory. Median 9.1 s, p95 59.8 s, 7/39 calls cap-cut, 2 questions errored.
Together with D-176 (glm-5's timeouts moved from 4 to 17 of 34 between two runs an hour apart), a memory tool the consumer calls many times a session cannot depend on these upstream pools.
**Remaining writer candidates**, each served by its own vendor and major clouds:
| Model | Endpoints | $/M in / out | Est. $/q (full) |
|---|---|---|---|
| anthropic/claude-haiku-4.5 | 8: Anthropic, Bedrock, Google, Azure | $1 / $5 | ≈ .02 |
| openai/gpt-5.6-terra | 7: OpenAI, Azure, Bedrock | $2 / $12 | ≈ .04 |
| openai/gpt-5.6-sol | – | $2 / $10 | ≈ .037 |
The cheap stable option measured so far is gpt-5.6-luna low: 18/25 correct, 8/8 abstain, p95 5.6 s, ≈ $.006/q full.
D-178 | 2026-09-26 | ACCEPTED (owner: "handle the task in a clever way… I will trust your decisions when you find a model"; spraying paid trials wastes credit; Haiku 4.5 was already worse and more expensive than gpt-6-luna in earlier benches) | **Writer decision method changes: evidence first, then one decisive probe. The hypothesis is that glm-5's latency and capacity problem is self-inflicted by our provider constraints.**
**Free evidence** (OpenRouter `/models/z-ai/glm-5/endpoints`):
| Provider | Uptime | response_format | Price $/M in / out |
|---|---|---|---|
| first-party **Z.AI** | 100% (1 day) | no | 1.0 / 3.2 |
| **Novita** | 100% (1 day) | no | 1.0 / 3.2 |
| Venice | 99.2% | yes | – |
| StreamLake | 92% (30 min) | yes | – |
| GMICloud | 85% | yes | – |
| Baidu | 99.7% (slow) | yes | – |
The writer profile forced `response_format` + `require_parameters`, which EXCLUDED Z.AI and Novita and left only the volatile third-party pool (D-176, D-177). The prose job does not need provider-enforced JSON: the prompt asks for one JSON object, the product validates the schema and retries once.
**One decisive probe:** glm-5, provider order [Z.AI, Novita] with no fallbacks, no response_format, reasoning off, the same 34 contexts, cap $0.60.
- **Expected if the hypothesis holds:** median ≤ about 6 s, p95 ≤ about 12 s, correctness ≈ 21–22/25, JSON-invalid ≤ 1–2 on the first try.
- **If it holds:** the writer is glm-5 via Z.AI/Novita at about $0.017/q full (about $10–25/month at 20–50 questions a day).
- **If not:** the stable fallback is gpt-5.6-luna (18/25, 8/8 abstain, p95 5.6 s, about $0.006/q), with the C2 residual presented to the owner.
Dropped without spend: Haiku 4.5 (the earlier bench evidence is against it) and gpt-5.6-terra/sol (no evidence of better writing; about 3× the cost).
D-179 | 2026-09-26 | ACCEPTED | **The D-178 hypothesis holds, and the writer is DECIDED: glm-5 served by first-party Z.AI (Novita backup), reasoning off, a text output protocol, and a 12 s timeout that falls back to gpt-6-luna.**
**Decisive probe** (the same 34 end-to-end contexts, no response_format, provider order [Z.AI, Novita] with no fallbacks):
- every call served by Z.AI, 0 overloads or 4xx/5xx;
- reasoning-off accepted (0 reasoning tokens);
- per question: median 7.4 s, p95 17.0 s, max 49 s (one cap-hit plus a retry);
- $0.464 for 34 questions ($.0137/q writer at $1.0/$3.2).
**Correctness (strict judge):**
| Writer | Correct | Abstain | Contradictions | Note |
|---|---|---|---|---|
| glm-5 via Z.AI | **22/25** | 6/8 | 2 | third independent 22/25-level result, after glm5 22/25 and lowr2 21/25 |
| gpt-5.6-luna | 18/25 | 8/8 | 0 | |
| gpt-6-luna (current default) | 15/25 | – | – | |
**The one defect:** without provider-enforced JSON, 7/34 first tries were invalid JSON (long prose breaks the escaping), and retries feed the tail.
**Fix:** a labelled text protocol for the prose (and expand) job whenever the writer profile declares `json_mode = false`. It is a profile capability flag, never a model name (D-017), and the server parses it deterministically.
**Cost:** about $0.017/q full (writer about $0.014 plus plan/attribute on luna), about $10–25/month at 20–50 questions a day. That is within the owner's relaxed budget (D-173).
**C6 cost limit proposed to the owner:** ≤ $0.03/q (the per-question cap must cover the writer's worst-case reservation plus the fallback), together with the R4 manifest change from $0.01.
D-180 | 2026-09-26 | ACCEPTED | **The final writer configuration passes C2 on dev before adjudication (correct .84). The output format for non-JSON writers is a labelled text template parsed by the server.**
End-to-end on wf-memory-ask 817aff9: prose + llm attribution + writer openrouter-glm5 (Z.AI/Novita, reasoning off, json_mode = false → `prose_text`), writer timeout 12 s → gpt-6-luna; dev per-question cap $0.03.
| Metric | Value |
|---|---|
| Correct (judge) | **.84**: glm-5 wrote 21 answerable questions, **20/21** correct; the luna fallback wrote 4, 1/4 correct |
| Abstain | .875 (B-D077 is a false-premise question → adjudication) |
| Contradiction | .04 |
| Faithful (item, judge) | .853, over 225 statements (the answers are longer, ≈ 1,300 chars) → adjudication |
| Recall | .96 |
| p95 | 21.1 s |
| $/q | .016 |
Ledger: glm-5 29 ok (avg 7.5 s), 5 writer-timeout cuts, 0 format failures. The 3 budget stops were negative questions on the refine path (a second prose call's worst case did not fit under $0.03): a correct outcome, but an answerable refine-path question would be hit too. **The per-question cap for the final run is $0.05.**
**Output-format decision (owner asked):**
1. Prompt-forced JSON without response_format: 7/34 invalid first tries, because long prose breaks the escaping; retries feed the tail.
2. **A labelled text template (STATUS / CONFIDENCE / SOURCES / RELATED / ANSWER), parsed deterministically by the server: CHOSEN.** There is nothing to escape, a missing header is detected and retried, and it is provider-agnostic because it depends on no provider feature. 0 format failures in 29 answers.
3. Raw text passed to the consumer: rejected. The server needs status (C4), sources (attribution, verification, drillable handles) and a stable response contract.
4. Tools or structured outputs: not offered by the Z.AI/Novita endpoints, and they would re-introduce provider dependence.
The principle stays the same as D-156: the model writes free text, the server adds the structure and the checks.
**Next:** D-165/D-168 adjudication and the C1 rater on this run; then freeze the final protocol (writer config, caps, prompts, rubric, sets) and run the final gate on the PR set and sealed B.
D-181 | 2026-09-26 | ACCEPTED | **Full dev picture of the glm-5 writer configuration (817aff9) under the frozen protocol. The stronger writer buys correctness but adds wrong facts, and the remaining failures are a MEMORY-LAYER problem (current vs superseded), not a model problem.**
| Criterion | luna config a0d91d0 (D-169) | **glm-5 config 817aff9** | Gate |
|---|---|---|---|
| C1 utility (blind fresh-Claude rater) | 1.97 vs NLM 1.85 | 1.88 vs NLM 1.91 | ≥ 1.6, ≥ NLM − .05 |
| C2 correct (adjudicated) | .72 | **.80** (D050 was a judge false accept) | ≥ .80 and ≥ .83 |
| C3 faithful (adjudicated) | .867 (C: 1 in 27 sampled) | .859 (**C: 7 in 72 sampled**, P: 8) | ≥ .95 |
| C4 abstain (adjudicated) | 8/8 | **7/8** (B-D073 fabricated an "in-process" cache) | ≥ .90 |
| C5 recall | .94 | .96 | ≥ .85 |
| C6 p95 / $ | 11.5 s / .004 | 21.1 s / .016 | ≤ 20 s / owner cap |
C1 compares against different raters (two fresh instances) and is not comparable across the two columns.
**The glm-5 contradictions** are mostly SUPERSEDED designs presented as current:
- the D-055 preview change;
- D-026 replacing the decorator design;
- the SIGKILL/power-loss exclusion;
- which tools updated.
The overreach adds unstated inferences.
Longer, more detailed answers (≈ 1,300 chars against ≈ 450) surface more stale facts. For a consumer, a wrong fact is worse than a missing one, so glm-5 is NOT a clear improvement despite C2 .72 → .80.
**Root cause:** the dev and final corpora are imported documents in which "what is current" exists only as text ("D-055 supersedes…"). HLMemo's temporal layer is not in the answer path:
- bi-temporal validity;
- write-time supersession (D-118, wf-write-updates, unmerged);
- librarian contradiction/supersession marking (wf-librarian-v3, unmerged).
Nothing tells the writer which excerpt is superseded. Every writer model must guess, and a verbose one guesses more.
**Proposal to the owner (a strategic fork):**
- (a) The next lever is the memory layer. Surface current/superseded status per excerpt (bi-temporal validity plus supersession links from D-118 and the librarian) and show it to the writer, then re-measure with both writers. This is the "Human-Like" core: knowing what is current.
- (b) Or release with the conservative luna writer, which has fewer wrong facts, and accept the C2/C3 residuals explicitly.
D-182 | 2026-09-26 | ACCEPTED (owner chose "bring in the temporal layer") | **Next phase: surface current / superseded status per excerpt in memory.ask. This is HLMemo's "Human-Like" core, and it comes before any release.**
**Plan:**
- (1) A free inventory of the temporal machinery on main and on the branches: the bi-temporal fields, D-118 write-time supersession (wf-write-updates), librarian contradiction/relate (wf-librarian-v3, wf-b-real), and what memory.ask's excerpts carry today. This includes how it can apply to IMPORTED documents, where supersession lives inside the text.
- (2) A ceiling test before building: annotate the excerpts of the D-181 runs with current or superseded status, re-run both writers, and adjudicate contradictions.
- (3) Build, measure with the full D-165/D-168 protocol plus the C1 rater, freeze, then run the final gate.
The writer choice (luna vs glm-5) is re-decided after the temporal layer, because glm-5's extra wrong facts are mostly stale-as-current.
D-183 | 2026-09-26 | ACCEPTED | **Temporal inventory: the machinery exists but carries no data in the answer path. There are no supersedes links in dev or prod, and memory.ask's excerpts carry no status.**
Grounded on main a76d8fe plus the branches:
- **Data model:** `memory_versions` is bi-temporal (valid_from/valid_to, recorded_at/superseded_at, supersedes_version_id; GiST exclusion per logical_id), and `links` has rel supersedes/contradicts with bi-temporal columns and props.
- **Read path:** reads filter by (valid_at, known_at). D-057 hides a superseded hit when a live `supersedes` link joins two hits, and a `scope=part` link demotes. Hits carry valid_from only.
- **Librarian:** write_review (placement → relate → relate_verify) proposes contradicts/supersedes, but production runs as OBSERVER, so nothing is written (the R3 rehearsal wrote 0 links). **D-057 is therefore a no-op in dev and prod.**
- **D-118 (wf-write-updates):** writer-declared `updates` (revise or supersede, with a scope=part|whole link and a quote). It is reviewed (round 2 OK, D-126), 612 integration tests pass, and the client bench is item precision .978 / recall 1.0. It is unmerged: stacked on wf-b-real → wf-librarian-v3, and a minimal port is planned for R4 (D-125).
- **wf-librarian-v3 / wf-b-real:** evidence.py (quote-bound dates, CHANGE_MARKERS), span revise. Hold-outs failed (D-103, D-116); relate sees only the head of long documents (D-117), so librarian relate over imported chunks is NOT a viable status source.
- **memory.ask:** excerpt = {id, title, date = the ITEM's valid_from, text}, and no links are read. DECISIONS.md chunks all share one date, and the only currency signal is a prompt sentence.
- **The imported-docs gap:** the stale facts (the D-055 preview change, D-026 replacing the decorator design) sit in PHASE0-SPEC and the consults. No DECISIONS row names them by D-id (only 3 of 61 rows do), so a D-id parser has low recall.
- **The research report prescribes** invalidate-not-delete on contradiction, write-time contradiction detection, and extracting ingested docs into L1 facts with validity; its contradiction target is < .02.
**Candidate design:** a server-computed per-excerpt `status` (current / superseded_by X [part: quote] / earlier_than X), sourced from:
1. live supersedes links (D-118 writer updates);
2. a D-id change-marker index;
3. the chunk's own in-text date;
4. a query-time "currency triage" LLM for conflicts among the shown excerpts, quote-validated and annotation-only.
**Ceiling test first:** step (i) is free (was the superseder among the shown excerpts?); step (ii) uses oracle labels and re-runs both writers. Go if stale-as-current contradictions at least halve with C2 no lower.
D-184 | 2026-09-26 | ACCEPTED | **Temporal ceiling check (free): the stale-as-current errors are fixable by EXPLICIT supersession links, a superseder pull-in and per-chunk context labels. The build starts.**
The chunks were rebuilt offline with the product chunker (all 134 cited windows matched), and the excerpts the writer saw were reconstructed. The 8 C statements (817aff9 + a0d91d0) break down as:
- **5 genuine stale-as-current.** In 3 of them the old fact comes from the SAME pre-merge draft `docs/consults/03-claude-phase0-spec.md`, which PHASE0-SPEC declares explicitly: "MERGED 2026-09-22 from docs/consults/03-claude-phase0-spec.md". Nothing records that as a link, and PHASE0-SPEC's header chunk was never shown.
  - The superseding text reached the writer in 2 of 5 cases; the superseded source did in 5 of 5.
  - 1 is explicit (D-026 "rather than MCPServer decorators", whose row never reached the writer), and 4 are implicit.
- **3 are not stale:** an over-extension, a D-number cut at a chunk boundary, and a disputable label.
- **Date cues are useless:** file commit days are the same day or even inverted.
**Design:**
- (A) **Explicit-supersession links**, deterministic and LLM-free. A pass over items finds explicit declarations: "MERGED … from <path>", "supersedes / replaces <path | D-id>", "Update (D-xxx)", "instead of", "no longer". It writes `supersedes` links through the product's link path, with props {by: "explicit", scope: whole|part, quote}, so they are evented, replayable and reversible. It runs at import and as a backfill command.
- (B) **memory.ask:**
  - each excerpt gets a status (current / superseded_by X [quote]) from live supersedes links;
  - the superseder is pulled into the excerpt set;
  - the writer prompt treats superseded excerpts as history;
  - each chunk carries a read-time context label (the enclosing D-row id or heading and its in-text date), computed from the stored item body.
- (C) The D-118 writer-update port (the R4 plan) is for live memory.write use. It is not needed for the imported-doc evaluation, so it comes after.
- (D) The query-time triage LLM only if (A)+(B) leave stale cases.
A backfill on PROD data is a data change: review before the prod run (D-125). Dev measurement runs on the hlm_research_b replica only.
D-185 | 2026-09-26 | ACCEPTED | **The temporal layer is built (wf-memory-ask da41a94 = explicit supersession links + excerpt status, superseder pull-in and context labels). End-to-end, luna's faithfulness improves; glm-5's raw scores swing, and adjudication decides the writer.**
**Build:**
- explicit links: `hlm links explicit`, evented and reversible. On the dev copy `hlm_research_b_tl` it wrote 4 links (PHASE0-SPEC chunks → the two merged draft consults). A precision check over 1,731 repo markdown files found only these. DECISIONS-internal rows are self-links in this corpus.
- excerpt `status` and `context`, and pull-in of up to 2 superseders, on wf-memory-ask 82dcd97.
- merged, with 772 unit / 63 integration tests green.
**End-to-end on hlm_research_b_tl** (33 dev questions, per-question cap $0.05):
| Metric | glm-5 (Z.AI, text) | luna | luna before (a0d91d0) |
|---|---|---|---|
| Correct (judge) | .68 | .72 | .68 |
| Abstain (judge) | .375 | .875 | 1.00 |
| Faithful (judge, item) | .626 (246 statements) | **.925** (93) | .878 |
| Contradiction | .04 | .08 | .04 |
| p95 | 22.8 s | 12.5 s | 11.5 s |
| $/q | .016 | .003 | .004 |
glm-5's "answered negatives" are premise corrections grounded in memory (no Redis name, only an LRU cache; Docker Compose, not Kubernetes; no Grafana, the latency gate is in remote_gates.sh). The strict abstain judge counts those as answers, so they go to D-168 adjudication.
glm-5's raw scores swing between runs (817aff9: .84 / .853 / .875 against this run's .68 / .626 / .375) and its answers are very long. Only 1–2 questions per run show a superseded excerpt: the 4 links touch few questions.
**Next:** blind codex adjudication of BOTH runs (correctness plus faithfulness, one mixed packet each); the writer is then decided on adjudicated numbers.
D-186 | 2026-09-26 | ACCEPTED | **Writer DECIDED on adjudicated numbers: gpt-6-luna with the temporal layer. glm-5's extra correctness costs wrong facts. luna + temporal passes 6/7 consumer-gate criteria on dev, and C2 is one question short of the reference-relative bar.**
**Blind codex adjudication of both temporal runs** (consults 87/88; faithfulness extrapolated per stratum per D-165):
| Metric | glm-5 + temporal | **luna + temporal** | Gate |
|---|---|---|---|
| C2 correct | 21/25 = .84 (4 judge "wrongs" were runaways or FNs) | 20/25 = .80 (2 FNs overturned) | ≥ .80 and ≥ .83 |
| C3 faithful | ≈ .80 | **≈ .978** | ≥ .95 |
| Contradiction statements | 9 (superseded signatures, a "refactor done" that is only in BACKLOG, a SIGKILL cause, a stale STATUS line) | **0** | – |
| C4 abstain | 7/8 (1 fabrication, B-D073) | **8/8** | ≥ .90 |
| p95 | 22.8 s | **12.5 s** | ≤ 20 s |
| $/q | .016 | **.003** | owner cap |
| C5 recall | – | .86 | ≥ .85 |
Faithfulness detail:
- glm-5: 3/4 det-fail, 2/9 entail-false and 32/47 sampled runaway statements are faithful; 3/31 accept sample false.
- luna: 1/2 det-fail, 4/5 entail-false, 0/18 accept sample false.
**Scorer fix (wf-research-proto 6f9ab6d):** entail is judged in batches of 6 statements. A long answer's single payload made the judge run away (10/33 questions on the glm-5 run), so that run's first raw numbers (.626 / .375) were artifacts. Re-scored: .862 faithful, .84 correct, 0 runaways.
**Reading:** the temporal layer plus the concise writer gives the faithfulness the consumer needs. The verbose writer surfaces more superseded or overreaching content than the links cover.
**Last lever before the final gate (evidence-based, same price):** luna at reasoning effort MEDIUM scored 18/25 against 15/25 at low on the identical writer-sweep contexts (D-174). One end-to-end run with effort medium plus adjudication follows; then the configuration is frozen for the final gate.
D-187 | 2026-09-26 | ACCEPTED | **Dev iteration ends: luna at effort medium brings no measurable gain (judge .72, 2 gained and 2 lost; p95 16.9 s against 12.5 s), so the writer stays at effort low. The final-gate protocol is FROZEN in docs/decisions/FINAL-GATE-PROTOCOL.md before the run.**
On 25 dev questions, C2 sits one question below the NotebookLM-relative bar (.80 against .83), inside the ±.08 noise. Only the 110-question hold-out can resolve it.
The protocol pins:
- code wf-memory-ask fd83f95 and its configuration (prose, llm attribution, the temporal layer, luna low, a $0.01 cap);
- the memory DBs: sealed B on `hlm_research_b_tl`, PR set on `hlm_research_cur_tl` with explicit links;
- the set hashes;
- the scorer 6f9ab6d and its rubric hashes;
- adjudication and rater procedures;
- the NotebookLM reference notebooks;
- the per-set pass criteria and the outcome rules.
D-188 | 2026-09-26 | ACCEPTED (the owner: continue without the codex adjudicator for now; codex quota is exhausted until 2026-09-30) | **FINAL GATE (frozen protocol D-187, fd83f95): NOT PASSED. memory.ask is NOT release ready. The corpus-B dev set did not represent the real regime.**
**Judge results** (adjudication pending codex; the direction does not depend on it):
| Criterion | Sealed B (50: 45 answerable / 5 negative) | **PR set (60: 50 / 10), this project's current memory, 558 items** |
|---|---|---|
| Correct | .622 | **.30** |
| Contradiction | .067 | **.22** |
| Abstain | 1.00 | .70 |
| Faithful (item) | .895 | .868 |
| Recall | .948 | .853 |
| p95 | 15.0 s | 14.1 s |
| $/q | .004 | .003 |
Errors: 0. The temporal layer fired (sealed B: 5 superseded excerpts shown, 2 pull-ins).
**PR-set failure types (50 answerable):** 15 correct, 13 main fact only, 11 main fact missing, 9 contradictions, 2 stale-as-current.
**PR by category:**
| Category | Correct |
|---|---|
| multihop | **0/5** |
| procedure | **2/10** |
| temporal | **2/10** |
| recent | 11/25 |
**Sealed B by category:** architecture 5/10, rationale 6/10, gotcha 7/8, identifier 5/7, temporal 5/10.
**Lessons:**
- (1) All day of dev iteration ran on corpus B, an older and more static snapshot. The real regime is the current memory, with many superseded decisions and multi-hop or procedural questions, and dev did not represent it, so the tuning overfit dev.
- (2) The capability gaps are multi-hop synthesis across items, procedures, and "what is current now" over an evolving memory. The explicit links cover almost nothing in the current tree: 252 links from ONE "MERGED" declaration.
- (3) Per the protocol, the PR set and sealed B are now SPENT for this candidate. No tuning on them; the next candidate needs a new hold-out.
**Next (proposed):** build a REPRESENTATIVE dev set from the current memory (the same generator as the PR set, a disjoint commit range) with multi-hop, procedure, temporal and recent questions, then attack those capabilities on it. The NotebookLM reference on these sets is still running, for context.
D-189 | 2026-09-26 | ACCEPTED | **The real-memory regime is hard for BOTH systems. NotebookLM on the PR set: correct .42, contradiction .28. A representative dev set (pr-dev) now exists, and memory.ask's baseline on it mirrors the hold-out.**
**NotebookLM PR reference:**
- a private NotebookLM notebook (id in docs/private): all 558 items of hlm_research_cur, 44 content sources, secret-scanned with 0 hits;
- 60/60 questions, a fresh `nlm query` per question, judge only (codex adjudication is blocked until 09-30).
| PR set (60) | memory.ask fd83f95 | NotebookLM |
|---|---|---|
| Correct | .30 | .42 |
| Contradiction | **.22** | .28 |
| Abstain | **.70** | .40 |
| multihop | 0/5 | 0/5 |
| procedure | 2/10 | 3/10 |
| temporal | 2/10 | 4/10 |
| recent | 11/25 | 14/25 |
| p95 | **14 s** | 43 s |
Sealed B for NotebookLM is 36/50 done; the daily quota stopped it, and it resumes with `<scratch>/nlm-final/run_set.py`.
**pr-dev:** 60 questions written blind by an independent writer from the migrated sources (snapshot c981973, D-001..D-133): multihop 12, procedure 12, temporal 12, recent 14, unanswerable 10; 49/50 gold answers span ≥ 2 sources. It lives at `docs/private/realdata-hlmemo/pr-dev/`, with sources converted to the hold-out's `{path, anchor}` schema.
**memory.ask fd83f95 on pr-dev:**
- correct .34, contradiction .26, abstain .90, faithful .84, recall .82, p95 13 s;
- multihop 2/12, procedure 3/12, recent 7/14, temporal 5/12.
It mirrors the PR hold-out (.30 / .22), so pr-dev is the iteration set from now on. A root-cause analysis of its failures is running.
D-190 | 2026-09-26 | ACCEPTED | **pr-dev root cause (33 failures of fd83f95): a literal-guard bug destroys procedure answers; retrieval misses cross-referenced decisions; about 9 failures are judge or gold artefacts. The fixes start.**
The per-question table is in `<scratch>/prdev-rca/classification.tsv`.
| Cause | Count | Mechanism / cases |
|---|---|---|
| JUDGE | 9 | facts true in the sources but absent from the gold read as contradictions (PD-020, 022, 026, 048); the gold snapshot is older than the memory (PD-003). Without these, correct ≈ .52 |
| RETRIEVAL | 7 | 2 are chunk-level: the right item was shown but the wrong chunk (PD-007), or the 3,200-char clip cut the section (PD-038) |
| COMPRESSION | 7 | – |
| PROCEDURE | 6 | – |
| STALE | 3 | – |
| MULTIHOP | 1 | – |
**Mechanisms:**
- **PROCEDURE is a product bug.** The prose hard-literal guard treats a whole fenced code block as ONE literal, so a single placeholder or filled-in token drops the block and leaves a dangling "…run:". None of the 60 answers kept a fenced block.
- **Contradictions are mostly the writer answering from an older or nearby source because the current one was never retrieved** (7), plus judge artefacts (4) and literal-drop truncations (2).
- **At fact level**, the must_mention facts were in the shown excerpts in 23/33 failures. The failing questions' gold was only partly shown: .55 against .75 for passing questions.
- **The temporal layer was inert:** the current memory has only the one "MERGED" declaration and no decision-to-decision links (superseded_shown 0 on all 60).
**Fixes (wf-memory-ask), in order:**
- (1) The code-block literal guard: line-level checks, placeholders exempt, small and derived numbers exempt, no dangling lead-ins.
- (2) Cross-reference retrieval: pull in D-ids and paths mentioned in the shown excerpts but not shown; best chunk across long items; clip windows centred on the matched section.
- (3) Then writer coverage, and the decision-to-decision supersession sources.
Dev comparisons are PAIRED on pr-dev (same questions), and every correct/incorrect flip is spot-checked, because judge noise is as large as a single lever and codex adjudication is unavailable until 09-30.
D-191 | 2026-09-27 | ACCEPTED | **The pr-dev fixes (wf-memory-ask 43113fb + c7e066f) give a modest paired gain: correct .34 → .42, abstain .90 → 1.00. Contradiction stays at .26, and procedure answers still miss steps.**
Paired on the same 50 answerable pr-dev questions, same memory (hlm_research_cur_tl):
| Metric | fd83f95 | c7e066f |
|---|---|---|
| Correct | 17 | **21** (+9 gained, −5 lost) |
| Contradiction | 11 | 11 |
| Stale | 2 | 2 |
| Missing main fact | 9 | 5 |
| Partial | 9 | 9 |
| multihop | 2/12 | 3/12 |
| procedure | 3/12 | 3/12 |
| temporal | 5/12 | 6/12 |
| recent | 7/14 | 9/14 |
| Recall | .82 | .84 |
| p95 | 13.1 s | 13.9 s |
Findings:
- **Fix 1 (the code-block literal guard)** removed the block destruction, but the writer seldom writes fenced blocks (1/12 procedure answers). Procedure failures are MISSING steps or facts, not guard drops.
- **Fix 2 (cross-reference pull-in)** always filled its cap of 3 (xref_pulled 207 over 60 questions), which likely adds noise. The 5 losses are each one missing fact, plus one contradiction.
- **The memory replica holds DECISIONS up to D-136**, while the pr-dev gold stops at D-133. That is a small snapshot mismatch (e.g. PD-003), not the main cause of the contradictions.
**Next levers:**
- (a) Gate the xref pull-in on relevance: a D-id or path overlapping the question, or ≥ 2 question words; cap 2.
- (b) Writer coverage for how-to questions: the complete ordered steps with exact commands and flags from the excerpts.
- (c) Recency for "what is current" questions: a planner query for the latest status and the latest decision rows. Contradictions are dominated by the writer answering from an older source when the current one was not retrieved.
D-192 | 2026-09-26 | ACCEPTED | **Trace "brain surgery" on memory.ask (owner request, $5 budget, $2.17 spent): the low real-memory score is not an LLM ceiling. Six mechanical problems stack, and the writer ignoring shown evidence explains 1 of 31 wrong answers.**
Method:
- An opt-in trace recorder `HLM_RESEARCH_TRACE_DIR` (wf-memory-ask **fad3354**; its commit subject mislabels it D-189). One JSON file per request holds the map, the plan, the per-query ranked hits, fusion, drops with reasons, the exact excerpts, the writer I/O, the validator verdicts and the attribution. It is behaviour-neutral (identical responses and requests, tested).
- Three traced pr-dev runs on fresh copies of the PR-set memory: A = as measured before; A2 = A repeated; B = with Memory Map summaries.
- A deterministic funnel (first loss per must-mention fact), then independent reading of every fact the funnel blamed on the writer (it was refuted for 30/31).
- memory.ask keeps no conversation memory: access events, the spend ledger and map summaries are the only read-path writes, and none feeds ranking (the ledger only via budget stops).

| Run | Correct | Contradiction | Faithful | Recall |
|---|---|---|---|---|
| A | .38 | .28 | .863 | .842 |
| A2 | .50 | .30 | .820 | .876 |
| B | .30 | .32 | .788 | .840 |
| c7e066f (earlier) | .42 | .26 | .818 | .844 |

Findings:
- **F1:** gate/dev scripts never started the librarian worker, so ALL earlier measurements ran WITHOUT map summaries, while the frozen protocol and deploy/llm.env.example say they are ON. With summaries the run is worse (.30).
- **F2:** the ~6k-token Memory Map shows big sources as an evenly spaced sample. DECISIONS (136 items) shows 54 ids without summaries and 8 with them; the newest decisions are mostly invisible to the planner. Gold items in the map: 44% → 20% with summaries.
- **F3:** run noise. The same config scored .42 / .38 / .50. A vs A2 flips 12 of 50. Raw-question retrieval and the map are deterministic (60/60), the planner output never is (0/60 identical), and 11 of the 12 flips are planner-driven. Over 3 runs: 14 always correct, 31 at least once, 19 never.
- **F4 (15/31 wrong answers):** retrieved evidence doesn't reach the writer.
  - The 12-slot cap favours hub documents: 27 of 32 cap-dropped gold items were ranked by one query only, at median fused position 19.
  - Xref pull-ins took 156 slots, with 6% gold. 71% of excerpts hold no needed fact.
  - Wrong chunk or clipping.
  - `_doc_best` re-picks a chunk by word overlap and ignores the retrieval rank (it dropped the rank-1 answer chunk in PD-031).
- **F5 (7/31):** the hard-literal guard drops correct sentences: placeholders in inline code are mangled, question literals, notation. Procedure answers are the most affected.
- **F6 (6/31):** the judge marks present facts missing (3 wrong answers are judge-only, all Turkish), and the pr-dev gold stops at D-133 while the replica holds D-136.
- **F7:** the temporal layer is inert on real memory. All 252 explicit links are PHASE0-SPEC merged_from; there are no decision/status links. 1 of 1,065 excerpts is marked superseded. Real supersession is implicit. 6 of 14 contradictions present an older fact as current.

Consequences:
- **Proposed order (owner to decide):**
  - (1) Fix the measurement: judge cross-language matching, gold refresh, mean of 3 runs.
  - (2) The validator.
  - (3) Evidence selection (doc_best rank, a per-query slot guarantee, gated xref, clipping).
  - (4) A recency-aware Memory Map; summaries stay OFF until then (a protocol deviation).
  - (5) Implicit temporal ordering.
  - (6) Planner variance.
  - (7) Re-measure ×3.
- Steps 2–6 can be replayed offline against the 180 saved traces with no LLM spend.
- Full report: docs/private/realdata-hlmemo/TRACE-REPORT-2026-09-26.md (private: it contains dev questions).
D-193 | 2026-09-27 | ACCEPTED | **Measure before fixing (owner): the real ceiling of memory.ask on the real memory is ~.76–.80, not .86. Measured value per fix: judge ≈ +.1 (measurement), validator safe and useful, simple selection rules ≈ 0, ranking/salience large (the oracle gains +.13), "the newer wins" needed for 6/14 contradictions.**
Method:
- Offline replay of candidate fixes on 240 saved traces using the real code. Reproduction gates: excerpt selection 180/180, validator 1,189/1,189 units, map byte-identical.
- An ORACLE run: branch eval-oracle @ 7d1a1fd, EVAL-ONLY and never merged. `HLM_RESEARCH_ORACLE_FILE` drills the gold handles first, then the normal fill to the same cap. Cost $0.69.
- The oracle's 22 judged-wrong answers were read by 2 independent readers.

| Oracle measurement (50 answerable) | Correct |
|---|---|
| Normal pipeline, mean of 3 runs | .43 |
| Oracle, as judged | .56 (multihop .17) |
| + correct judge (7/22 "wrong" answers read as fully correct) | .70 |
| + validator fix | .72–.76 |
| + newer-wins (6 contradictions: an older excerpt beat the newer oracle excerpt in slot 1) | .76–.80 |

Replay:
- **Validator fixes** ((a) placeholder wildcards, (b) question literals, (c) notation, (d) shell variables with a similarity floor): drops A 15→7, A2 11→6, B 16→9, O 15→6. 20+ fact-bearing units rescued; no fabrication found on reading.
- **Simple selection rules** (doc_best by rank, per-query slot guarantee, xref gating): facts shown 116→118/122, with regressions. Not worth it as designed.
- **Newest-10 map listing:** gold-in-map 92→125/211; newest decisions visible 4→9.
- **Same-file temporal flagger:** unusable (5 correct vs 46 wrong flags).

**Proposed order (owner to confirm):**
1. The measurement (judge cross-language/SHA-prefix; gold refresh to D-136; mean of 3 runs).
2. The validator.
3. Map newest-10, with summaries OFF.
4. A newer-wins ceiling test (oracle supersession links on the contradiction subjects), then a reviewed librarian supersession backfill.
5. A ranking ceiling test (offline rerank of the saved hit lists).
6. Writer completeness.
7. Re-measure.

Budget: $2.86 of $5 spent. A new hold-out is needed before the next final. Details: docs/private/realdata-hlmemo/MEASUREMENTS-2026-09-27.md.
D-194 | 2026-09-27 | ACCEPTED | **Measurement fix (step 1 of D-193): the judge rubric v2 FAILED its calibration gate, so the automatic judge stays v1, and its false negatives are corrected by independently READING every judged-incorrect answer (the D-168 adjudication rule). The pr-dev gold is refreshed to the replica (D-136).**
Gold refresh:
- An independent auditor (memory only, never saw system output) made 12 proposals. Accepted: 6 (PD-002 and PD-003 changed substantively, since D-136 makes memory.ask the R4 core and sets its answer contract; PD-014, 046, 051 and 059 got context and traps).
- Rejected: 6 alternate-anchor-only additions (the schema has no alternatives, so they would lower anchor_share).
- pr-dev hashes: v1 94144c2955871047 (kept as questions.v1.jsonl) → v2 2e0ffe3d0f771b19.

Judge v2 (wf-research-proto 2441ce4, `--gate v2`; v1 byte-identical). The rubric allows cross-language paraphrase, id prefixes and number formats, and defines a contradiction as an asserted incompatible fact. Calibrated on 66 questions labelled by the readers; spend $0.35.

| Calibration check | v1 | v2a |
|---|---|---|
| Missed facts recovered | 0/20 | 15/20 |
| False accepts | 0/33 | 1/33 |
| Judge-error contradictions cleared | 0/3 | 3/3 |
| Real contradictions kept | 11/11 | **7/11** |
| Stability | 28/28 | **23/28** |

v2b, checked on a subset, is projected to fail as well. Loosening the rubric trades directly against contradiction detection.

Consequences:
- v1 remains the automatic judge. Every measurement reports the judge number AND an adjudicated number, where independent readers (Claude now, codex after 09-30) read every judged-incorrect answer.
- gate_v2 stays marked "CALIBRATION NOT PASSED".
- Scorer bug to fix: a judge budget stop is caught as a runaway and scored all-false instead of unscored.
D-195 | 2026-09-27 | ACCEPTED | **Fix phase of D-193 done and measured: every mechanism works live, and the true (adjudicated) correct rate rises .58 → .63, with contradiction ~.07 → ~.06. The .80 gate is NOT reached. The automatic judge was the largest distortion: it under-scores correctness by ~.15–.2 and over-flags contradiction about 4×.**
Shipped on wf-memory-ask (not pushed):

| Commit | Change | Measured |
|---|---|---|
| a7816fe | Validator placeholders, question literals, notation | R2 replay: dropped units about halved |
| d60f134 | Memory Map newest-10 | gold in map 92 → 125/211 |
| 8949fac | K4 order | gold@1 15 → 29 offline |
| 16dec5e | Opt-in LLM rerank, prompt rerank/v1 | gold@1 → 40, gold@4 47 = in-pool ceiling |
| 58e5f57 / 8d36f74 | Temporal read side: multi-status lines, cap 4 (orchestrator's call, not the owner's), punctuation-safe spans | – |
| f80a5cb | Prompt v3.2, opt-in | no gain in blind grading, so NOT adopted |

Data:
- 121 curated, chain-aware supersession links: three independent readers read all 123 proposals, 122 correct, 0 false. They are applied on the test DB hlm_research_final only (event 2209). Render check 121/121.
- The automatic pairwise backfill (wf-supersede-backfill) is REJECTED. Precision .69 with 5 high-harm false links; confidence does not separate them. The owner chose curated backfill plus write-time supersession (D-118).
- The step-4 test showed links fix "older wins" (7/16 → 1/16) when the newer item is shown.

Final re-measure: 8d36f74, rerank on, hlm_research_final, gold v2, 3 runs. Totals are over the 50 answerable questions, contradiction included. The "unchanged" column is the paired count on the 46 answerable questions whose gold did not change.

| | Judge correct | Judge contradiction | Adjudicated correct | Adjudicated contradiction | Correct on 46 unchanged |
|---|---|---|---|---|---|
| Baseline (A, A2, c7e066f) | .43 | .28 | .58 (A, A2) | .08 | 27.5 |
| Final (F1–F3) | .44 | .26 | **.63** | **.06** | **29.7** |

- Other final numbers: p95 14.7–15.6 s; $.0034–.0042/q; abstain .90–1.00.
- Adjudication: blind Claude readers on every judged-incorrect answer plus a sample of the judged-correct ones. Inter-reader calibration overlap 20/20.
- Live check: gold@1 14 → 35–39 of 46; status lines on 188–197 excerpts; newer item co-shown 77 → ~155.
- **Side effect:** 33–37 gold excerpts per run carry a "superseded in part" line (mixed excerpts), and the whole judge loss sits in those 21 questions.
- Multihop is unchanged (5/11).

Measurement lesson: for dev decisions, use the adjudicated number (blind readers) instead of judge v1, or pair them.

Remaining levers, for the owner:
- (1) Mark the superseded sentence inline instead of an excerpt-level line.
- (2) Writer strength and completeness (the oracle ceiling is .78 with luna).
- (3) Multihop composition.
- (4) A new hold-out before any final.

Spend on this phase: about $6.35 of $15.
D-196 | 2026-09-27 | ACCEPTED | **Project PAUSED by the owner after about a week of memory.ask development ("şimdilik projeyi durduralım … daha sonra kaldığımız yerden devam edeceğiz").**

State at pause:
- **Candidate:** wf-memory-ask 8d36f74, with curated supersession links on the test DB only.
- **Measured (adjudicated):** correct .63, contradiction ~.06, abstain .90–1.00, p95 ~15 s, about $0.004/q.
- **Gate:** the D-158 gate is NOT passed (correct ≥ .80). The writer ceiling with perfect evidence is .78.
- **Git:** nothing is pushed; main is 54 commits ahead of origin, and the feature branches have no upstream.
- **Budget:** $6.35 of the added $15 spent.
- **Resume guide:** a private file, docs/private/realdata-hlmemo/RESUME-2026-09-27.md. The analysis archive (traces, tools, readings, links) is in docs/private/realdata-hlmemo/archive-2026-09-27/.

Next levers, in order:
1. Inline superseded-sentence marking (the side effect in D-195).
2. One writer-strength probe on the oracle inputs.
3. Multihop composition.
4. A new hold-out, then the final gate (judge v1 + blind-reader adjudication).
5. R4: the D-118 port, a reviewed curated backfill on prod, the manifest (summaries OFF, rerank ON), rehearsal.
D-197 | 2026-09-29 | ACCEPTED | **Writer model test (owner request): Gemini 3.8 Flash (medium) is the best memory.ask writer measured (adjudicated correct .74 vs luna .63, contradiction .03 vs .06), but it fails the latency (p95 33–38 s) and cost ($.021/q) gates. DeepSeek V4.1 Flash max is rejected. The standard writer tests are saturated.**

Method:
- Same final system (8d36f74, rerank on, 121 curated links), with only the writer swapped. The eval-only tree raised the token and timeout limits uniformly.
- Blind-reader adjudication is the primary score; calibration overlap with earlier reads 19/20.
- Standard tests (WS 33q, ORACLE-sel 25q) were re-read blind for all prior models too. Re-read consistency 20/20. Every model scores ~.97 WS and .84–.92 ORACLE, so the old judge-based spread was mostly a judge artefact.

Real system:

| Writer | Correct (adjudicated) | Contradiction | p95 | $/question |
|---|---|---|---|---|
| luna | .63 | .06 | ~15 s | .0034–.0042 |
| DeepSeek V4.1 Flash max (D1) | .64 | .08 | 119 s | .0094 |
| Gemini 3.8 Flash (G1/G2) | .78 / .70 | .03 | 33–38 s | .021 |

DeepSeek's D2 run is invalid: circuit-breaker fallbacks to luna.

Other findings:
- The product under-counts Gemini cost: thinking tokens are excluded from `completion_tokens`.
- The eval tree also needed HTTP_TIMEOUT_S and DETACHED_HOLD_MAX_S raised.

Next: Gemini at reasoning_effort low/minimal on the real system, scored by readers only.
D-198 | 2026-09-29 | ACCEPTED | **Gemini 3.8 Flash reasoning HIGH is no better than MEDIUM (adjudicated correct .72 vs .74, within run noise) at +39% cost and ~2× p95. Owner: the R4 prod writer and the final test use MEDIUM; the R4 caps are HOUR 3 / DAY 8 / MONTH 60 USD.**

Measurement (same system as D-197, Google API direct, reasoning_effort high, PROSE max_tokens 16k):

| Setting | Correct (adjudicated) | Contradiction | Negatives fabricated | p95 | $/question 2026 → 2027 |
|---|---|---|---|---|---|
| high (G3 / G4, reader-only: every answer read) | .76 / .68 | .00 / .04 | 0/10, 0/10 | 53–69 s | .0298 → .0562 |
| medium (G1 / G2, judge-assisted D-195) | .78 / .70 | .04 / .02 | 1/10, 0/10 | 33–38 s | .0214 → .0405 |

- High thinks ~2.2× longer than medium (mean writer output 3.4k tokens, max 15.4k); no truncation at 16k. Standard tests are still saturated (WS .94, ORACLE-sel .92).
- Grading rule, applied to all real-system sets: an EMPTY answer to a negative question (status insufficient_evidence) is a correct abstention. D-195 never packets such answers. One of three readers graded them "no".
- Price/performance ranking (private report 04): in the production view, luna low > Gemini medium > Gemini high > DeepSeek max. Latency ignored: Gemini medium first. No writer reaches .80 correct (oracle ceiling .78, D-193).
- Monthly budget incl. VPS at 20 / 100 / 300 asks per day, Gemini medium: $24 / $77 / $208 (2026) → $35 / $134 / $380 (2027). With luna: $13 / $23 / $49.
- Consequence for R4: Gemini fails both C6 gates (p95 ≤ 20 s, ≤ $0.01/q). The prod final test therefore measures quality on prod data; it cannot self-certify Production Ready. R4 plan v2 pre-registers what the result means (Astra R-16). The OpenRouter prod key keeps its own $50/month provider-side limit as the outer guard.
D-199 | 2026-09-30 | ACCEPTED | **R4 review closed after 2 rounds (D-125). Owner ACCEPTS the residual risk. Final-test rule v2.1: 60 questions, REVERT on contradiction > .08. A local Lima VM rehearsal and the pre-push gate are mandatory before prod.**

Review:
- Round 1 (consult 89, Astra-high): GO-WITH-FIXES, R-1…R-17.
- Round 2 (consult 90, Astra-high + gpt-5.6-sol xhigh, D-085): both GO-WITH-FIXES, no new CRITICAL/HIGH. Open: R-1 partial, cycle check blind to intermediate live links, lock-before-check race, probe accepted malformed replies, per-call fallback share, RUNBOOK `--preview`, R-10 real-time path, R-15 push checks, R-16 measurement definitions.
- All were fixed on r4-rc (08506d5) with regression tests, not accepted as risk. The orchestrator re-ran the targeted regressions (54 + 12 subtests) and unit (850). The implementer reports deploy 240, integration 600 / 2 known environment-only failures.

Residual risk accepted by the owner:
- (a) The endpoint locks do not serialize a concurrent link between two non-endpoint items on a longer chain. Mitigation: the librarian is stopped during the one-time prod link apply.
- (b) A proposal whose reverse link is already live is skipped, not rejected. Nothing wrong is written.
- (c) Review-77 residuals #1/#4/#5/#8/#9 stay open, and #2/#10 are partial, each with a RUNBOOK mitigation.
- (d) The real 170 s path and the public-push hygiene are proven only by steps not yet run: the Lima VM load smoke and `prepush_check.sh`. Both are mandatory gates; a FAIL stops the release.

Final-test rule v2.1 (orchestrator, fixed before any question exists; plan §5.1):
- 50 answerable + 10 negative questions.
- KEEP: ≥ 35 correct, ≤ 2 contradictions, ≤ 1 fabricated negative, ≤ 6/60 fallback, $/q ≤ .03, p95 ≤ 60 s.
- REVERT: ≤ 31 correct, ≥ 5 contradictions, ≥ 16/60 fallback, or a HIGH incident.
- Why: v2's REVERT at > .06 on 32 questions fires on 2 contradictions. That happens with P ≈ .25 for a writer at .03 (exact binomial), and it would revert to luna (.06).

Owner approvals:
- a disposable Lima VM on the owner's Mac for the rehearsal (deleted afterwards);
- `MCP_TOOL_TIMEOUT=180000` added to the owner's Claude Code settings by the orchestrator (effective after a restart).
D-200 | 2026-09-30 | ACCEPTED (measurement) / OWNER CALL pending | **R4 final test: memory.ask with the Gemini 3.8 Flash MEDIUM writer, on a one-to-one replica of prod. 38/50 correct (.76), 3/50 contradictions (.06), 1/10 fabricated negatives (abstain .90), 0/60 fallbacks, $0.0201/q, p95 30.8 s, no HIGH incident. By the pre-registered §5.1 rule (v2.1) this is an OWNER CALL: every KEEP condition holds except contradictions (3 > 2), and nothing reaches REVERT.**

Plan deviation (owner proposal):
- This Mac's IPv4 route to the prod VM broke after the deploy. It was an ISP↔Hostinger routing fault: prod was healthy from 4+ external nodes and over IPv6; fail2ban had 0 bans.
- So the test ran against a one-to-one replica on a local Lima VM, the same as prod in:
  - code 5025db5;
  - the R4 env, with the same config fingerprint 8f03e7ac0882;
  - data: prod's pre-R4 safety dump (sha 66ff4467…), migrated 0008 → 0009;
  - the prod-approved links applied with the §4.6 commands (252 explicit + 119 curated);
  - the Caddy edge, reached over verified TLS through the local CA.
- The orchestrator asked each sealed question once via `claude -p` (MCP), at most 3 in flight, with a verbatim-input check and the raw tool results saved.
- Still to do on prod itself once the IPv4 route heals: the §4.6 link apply and a 3-ask smoke. Prod still has 0 links.

Measurement (sealed hold-out, 50 answerable + 10 negatives, seal sha 3f8d0d67… / 50f649cc…):
- **Blind readers:** 3 readers, majority per question; unanimous on 59/60; per reader 38 / 39 / 38 correct.
- **By category:** recent 10/13, multihop 8/12 (1 contradiction), temporal 10/13 (2 contradictions), procedure 10/12.
- **The contradictions:**
  - H031: gives the superseded G3 Recall@5 0.930 as current; gold 0.980 after D-055.
  - H034: states the D-058 role ladder, then says it was eliminated.
  - H015 (2 of 3 readers): an extra claim that an observer answer is recorded as a fact.
- **Fabricated negative H019** ("MIT license"): the memory holds only the indirect phrase "çevreleyen MIT kodu" (the surrounding MIT code). It is a borderline gold call and does not change the verdict.
- **Operations:**
  - writer gemini medium 60/60, 0 fallbacks;
  - server p50 20.3 s / p95 30.8 s (max 38.2 s);
  - ledger window $1.20366 → $0.0201/q;
  - query/write p95 75/53 ms;
  - 7 of 63 LLM-rerank calls timed out, and those asks used the fused order;
  - 5 abstentions returned an empty answer text.
- **C1 consumer utility** (consumer_v1, without the drill rule because no drill texts existed): 1.77; answerable 42/4/4, negatives 9/0/1.
- **Versus luna on pr-dev (.63 correct, .06 contradiction, D-193/D-195):** correct +.13, contradiction the same. D-130 cannot self-certify (the C6 p95/cost gates, D-198).

Follow-ups:
- temporal stale-as-current (H031/H034);
- empty-text abstentions (give the closest leads);
- the literal validator dropping a lead sentence (H013/H024 start mid-thought);
- the rerank deadline (7/63 timeouts).
D-201 | 2026-09-30 | ACCEPTED (measurement) | **Paired comparison on the same 60 sealed questions and the same prod replica: luna as the writer (the R4 revert setup) scores 32/50 correct vs Gemini medium 38/50, with the same 3 contradictions and 1 fabricated each. Luna needed the fallback writer on 21/60 questions (6 timeouts, then the breaker), so under the §5.1 rule it would be REVERT. Prod links applied; prod now equals the tested configuration.**

Paired comparison:
- Answerable, per question: 28 both right, 10 Gemini only, 4 luna only, 8 neither. Exact McNemar p = .18: the direction favors Gemini but is not significant at n = 50.
- By category, luna vs Gemini: multihop 5/12 vs 8/12, recent 9/13 vs 10/13, temporal 8/13 vs 10/13, procedure 10/12 vs 10/12.
- Contradictions: luna H031, H044, H048; Gemini H015, H031, H034. H031 (the superseded 0.930 given as current) fails in both.
- Luna ops: p95 26.2 s, $0.0073/q (ledger window $0.43829), writer_used luna 39 / fallback profile "openrouter" 21.
- Readers: 3 fresh blind readers, new packet codes, the writer not revealed; unanimous on 59/60.

Prod:
- §4.6 applied over IPv6 SSH: 252 explicit links (event 2334) + 119 backfill links (event 2335), with the librarian stopped. Prod's link set equals the replica's.
- An AAAA record for mcp.hlmemo.com was added with the owner's OK. This Mac's IPv4 route is broken; IPv6 exposes the same ports (80/443/22).
- The owner's KEEP/REVERT decision is pending a real-use session via MCP.
D-202 | 2026-09-30 | ACCEPTED (measurement) | **Prod smoke PASS and a first-hand real-use session: the orchestrator used HLMemo's `memory.ask` directly from Claude Code (prod, via the new AAAA/IPv6 path) for 11 genuine working questions. The answers are accurate, deep and verbatim-cited, and they handle supersession well. Four concrete defects surfaced.**

Smoke and ops:
- 11/11 answered by google-gemini38-flash-medium, 0 writer fallbacks.
- Latency 18–33 s typical, one 63 s outlier; $0.017–0.029 typical, one $0.071 outlier (the refine loop on a question the memory could not answer).
- Rerank timed out on 2 of the first 3 questions (luna slow at the provider).

Quality (checked against the repo/RUNBOOK by the orchestrator; spot drilldowns match verbatim):
- **Correct and useful:**
  - embeddings (D-008, and D-040 reversed by D-042);
  - the risk_check schema and judges (D-066/071/094);
  - the deploy + gates procedure;
  - rollback, with a larger budget;
  - why Postgres/pgvector (D-006);
  - the query rewrite shelved (D-116);
  - the SLA question: a clean "not in memory".
- **Borderline:** the librarian retry count, where "4 defer events" was read as "4 retries".

Defects found:
1. **Silent truncation.** At the default token_budget 3000 a procedural answer (deploy → rollback) was cut after the "3. How to Roll Back" heading, with no marker; the budget used was 2992/3000. Fix: a truncation flag/marker, and/or a larger default budget for memory.ask.
2. **The first list item gets dropped.** The literal validator removes the first item of a numbered list, so the answer starts at "2.". Reproduced 3× (final-test H013; the rollback answer; the query-rewrite answer), with `dropped_literal: 1`. Fix: renumber or keep a lead-in when a claim is dropped.
3. **Freshness gap (dogfooding).** Prod memory stops at D-131 (2026-09-26); nothing was written since the migration. Answers about R4 or the current writer are stale (e.g. "memory.ask is not in production") and carry no "as of" horizon. Fix: resume the D-125 dogfooding writes (memory.write / call_the_day each session), plus an "as of <latest recorded_at>" line on status-type answers.
4. **Project hygiene.** The hlmemo project card is still the D-015 skeleton ("no summary yet"), and the librarian has 178 pending questions.

Comparison: `memory.query` with a drilldown returned the right RUNBOOK chunk as the top hit, far faster. `memory.ask` adds synthesis and citations at ~25 s and ~$0.02.
