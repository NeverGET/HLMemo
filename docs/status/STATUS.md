# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 — Codex round 8 D-027 integration and verification

## Phase 0 — D-027 fixes integrated; requested verification green, orchestrator closeout pending
Repo: https://github.com/NeverGET/HLMemo (public, main). Private ops repo not yet created (D-018).

Round 8 evidence: [08-codex-finish-verification.md](../consults/08-codex-finish-verification.md).
Ruff clean (90 files); 199 unit/fixture + 83 normal integration + 8 cached gate tests passed.
Latest G3 Recall@5 0.930; G4 p95 273.2 ms. Compose config, api/worker builds, test image build and
offline model/tokenizer/pytest smokes passed. C4 planner regression discovered and fixed without
changing authorization. `hlm_retr` retains 11,574 embeddings. No commit/deploy/API-worker startup.
O2 remains WEAK: restart policy verified in rendered configuration, actual crash/restart and
worker progress monitoring not demonstrated. No new contract question. G7 was not rerun.

## Measured (2026-09-22, Apple M3 Pro, PG 17.11, pgvector 0.8.6)
| Gate | Result |
|---|---|
| G1 boot/migrate | PASS |
| G2 budget | PASS (1000 random budgets, 0 overflow; raw paging TODO D-026) |
| G3 Recall@5 | PASS 0.930 (TR .971 / DE .909 / EN .909; identifier 25/25) |
| G4 latency | PASS p95 263-290 ms (improved from 371 ms after the C4 repair) |
| G5 auth/isolation | PASS (device×project, pending/revoked/restart) |
| G6 durability | PASS (idempotency, conflicts, backdated segments, replay rebuild) |
| G7 clients | PASS re-run 2026-09-22 (claude 2.1.280, codex 0.155.1, agy 1.2.8): write→query→drilldown→raw + preflight injection verified; see PHASE0-GATE-REPORT.md |
| G8 secrets | PASS (gitleaks over history, untracked secrets/models) |
Tests (after D-027/D-029 fixes, re-run by the orchestrator): 199 unit+fixture, 99 integration (1 skipped), 8 gate = 306 green. Lint clean.

## Resume — do in this order
1. Read docs/decisions/DECISIONS.md D-025..D-027 and docs/consults/07-codex-code-review.md.
2. Review uncommitted D-027 fixes and round-8 evidence; preserve changes. Remaining O2 monitoring/crash-restart proof is documented. Normal suites use explicit `HLM_TEST_DSN` pointing to `hlm_verify`, never `hlm` or `hlm_retr`.
3. Orchestrator decides closeout/commit; no commit was authorized in the round-8 implementation task.
4. (G7 already PASS) Re-run G1–G6 per PHASE0-GATE-REPORT.md resume block after fixes; agy pin: VERSIONS says 1.1.4 but agy self-updated to 1.2.8 (1.1.4 timed out) → re-pin to 1.2.8 (claude 2.1.278 / codex 0.155.1 / agy 1.1.4): `docker compose up -d --wait db migrate api worker`, then follow docs/USAGE.md; evidence into docs/decisions/PHASE0-GATE-REPORT.md.
5. D-021: create project `hlmemo` inside HLMemo, import docs/ + auto-memory, open the next dev session with `hlm claude`.
6. Sync PHASE0-SPEC.md text with D-026 deviations.

## Local environment facts
- Stack: `compose.yaml` (db/migrate/api/worker); models mounted from `./models` (470 MB, gitignored, `models.lock` has hashes). Dev admin token in `.hlm-dev.env` (gitignored). This machine's config in `hlm.toml` (gitignored).
- Test DBs on the compose Postgres: `hlm` (default), `hlm_retr` (G3 fixture with embeddings cached — do NOT drop, re-embed takes 12 min), `hlm_mcp`, `hlm_full`.
- NEVER run pytest without `HLM_TEST_DSN` (conftest truncates the live `hlm` db; use `hlm_test`).
- Alembic: always `alembic upgrade phase0@head` (D-026).

## Open (owner)
- Hetzner CX43 confirmation (D-005). Not blocking.

## Roadmap after Phase 0
1 (clues/drilldown maturity, raw paging) → 1.5 (raw importers: serena/auto-memory/context files/codex SQLite/NotebookLM) → 2 (librarian via OpenRouter deepseek-v4.1-flash, D-019) → 2.5 (per-project reconstruction campaign, D-022, ~2× estate incl. work computer) → 3 (consolidation/archival shadow mode, D-012) → 4 (experience layer, packed share).
