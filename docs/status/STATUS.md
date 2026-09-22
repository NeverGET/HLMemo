# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 evening — SAFE PAUSE (machine shut down)

## Phase 0 — implemented, gates measured, code review NO-GO (fix backlog open)
Repo: https://github.com/NeverGET/HLMemo (public, main). Private ops repo not yet created (D-018).

## Measured (2026-09-22, Apple M3 Pro, PG 17.11, pgvector 0.8.6)
| Gate | Result |
|---|---|
| G1 boot/migrate | PASS |
| G2 budget | PASS (1000 random budgets, 0 overflow; raw paging TODO D-026) |
| G3 Recall@5 | PASS 0.930 (TR .971 / DE .909 / EN .909; identifier 25/25) |
| G4 latency | PASS p95 371 ms (3 callers, 300 q) |
| G5 auth/isolation | PASS (device×project, pending/revoked/restart) |
| G6 durability | PASS (idempotency, conflicts, backdated segments, replay rebuild) |
| G7 clients | server-side PASS; real-CLI run: see PHASE0-GATE-REPORT.md (may be PENDING) |
| G8 secrets | test added; gitleaks clean on tree |
Tests: 231 (fresh DB) + 8 (cached embeddings, `hlm_retr`) = 239 green.

## Resume — do in this order
1. Read docs/decisions/DECISIONS.md D-025..D-027 and docs/consults/07-codex-code-review.md.
2. Fix D-027 backlog top-3 first (S1 raw provenance leak, C1 commit-before-ack, C2 two-pass replay), each with a regression test; then the rest (S2, S3, S4, C3..C6, O1..O3). Use parallel agents on disjoint paths, own DB per agent (`CREATE DATABASE hlm_<name>`, `HLM_TEST_DSN`).
3. Codex round 8 re-review (`docs/consults/08-*`) → GO.
4. Complete G7 with real CLIs (claude 2.1.278 / codex 0.155.1 / agy 1.1.4): `docker compose up -d --wait db migrate api worker`, then follow docs/USAGE.md; evidence into docs/decisions/PHASE0-GATE-REPORT.md.
5. D-021: create project `hlmemo` inside HLMemo, import docs/ + auto-memory, open the next dev session with `hlm claude`.
6. Sync PHASE0-SPEC.md text with D-026 deviations.

## Local environment facts
- Stack: `compose.yaml` (db/migrate/api/worker); models mounted from `./models` (470 MB, gitignored, `models.lock` has hashes). Dev admin token in `.hlm-dev.env` (gitignored). This machine's config in `hlm.toml` (gitignored).
- Test DBs on the compose Postgres: `hlm` (default), `hlm_retr` (G3 fixture with embeddings cached — do NOT drop, re-embed takes 12 min), `hlm_mcp`, `hlm_full`.
- Alembic: always `alembic upgrade phase0@head` (D-026).

## Open (owner)
- Hetzner CX43 confirmation (D-005). Not blocking.

## Roadmap after Phase 0
1 (clues/drilldown maturity, raw paging) → 1.5 (raw importers: serena/auto-memory/context files/codex SQLite/NotebookLM) → 2 (librarian via OpenRouter deepseek-v4.1-flash, D-019) → 2.5 (per-project reconstruction campaign, D-022, ~2× estate incl. work computer) → 3 (consolidation/archival shadow mode, D-012) → 4 (experience layer, packed share).
