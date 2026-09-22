# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 (end of design day 1)

## Phase: 0 — spec frozen pending codex GO (docs/consults/05-codex-go.md). No product code yet.
Repo: https://github.com/NeverGET/HLMemo (public). Private ops repo not yet created (D-018).

## Authoritative documents (read in this order)
1. docs/decisions/DECISIONS.md — D-001..D-024, binding
2. docs/decisions/PHASE0-SPEC.md — implementation contract (DDL validated on pgvector 0.8.6-pg17)
3. docs/decisions/VALIDATION-GATES.md — G1..G8
4. docs/decisions/PHASE0-ASSUMPTIONS-VERIFIED.md — pins, CLI flags, e5 revision
5. docs/consults/ — codex ⇄ claude rounds 00..05 (design debate trail)
6. docs/research/ — deep-research report, model bench analysis, VPS analysis, legacy estate summary

## Key outcomes today
- Librarian default: deepseek/deepseek-v4.1-flash via OpenRouter, reasoning off; fallback gpt-5.6-luna (bench: bench/results/20260922-122010-final.md)
- VPS proposal: Hetzner CX43 (awaiting owner confirmation)
- Architecture: own bi-temporal Postgres schema, no Graphiti/AGE; events authoritative; multilingual-e5-small; 5 MCP tools; hlm wrapper with preflight; device×project auth matrix
- Roadmap: Phase 0 (LLM-free core) → 1 (drilldown/clues maturity) → 1.5 (raw importers) → 2 (librarian) → 2.5 (per-project reconstruction campaign, ~2× estate incl. work computer) → 3 (consolidation/forgetting, shadow mode) → 4 (experience layer, packed share)
- Self-hosting: project `hlmemo` becomes HLMemo's first project after Phase 0 gates (D-021)

## In flight
- codex round 5 GO/NO-GO → docs/consults/05-codex-go.md

## Open (owner)
- Hetzner CX43 confirmation (D-005) — not blocking Phase 0 (local only)

## Next
- On GO: start Phase-0 implementation per PHASE0-SPEC §6 layout; first tasks = migration 0001 + config + write service (events/versions/chunks), then retrieval, then MCP server + auth, then wrapper, then gates.
- Create NeverGET/hlmemo-ops (private) when the first deploy artifact (hlm.toml, compose override) exists.
