# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 11:20

## Phase: -1 (report analysis + foundational decisions). No code yet.

## In flight
- codex consult #01: architecture critique of the report → docs/consults/01-codex-architecture-critique.md
- research agent: librarian model comparison (DeepSeek V4 Flash 0731 vs alternatives) → docs/research/01-librarian-model-analysis.md
- research agent: VPS provider comparison → docs/research/02-vps-analysis.md

## Open decisions (owner must answer)
- Privacy tier for cloud librarian (PII scrubbing / EU routing / per-project opt-out)
- NotebookLM migration for this project (global protocol asks to confirm)
- Language: Python vs TypeScript for MCP server (Claude leans Python)

## Next
- Merge codex + claude positions into docs/decisions/ (D-006..D-008 resolved)
- Write VALIDATION-GATES.md
- Phase 0 implementation plan

## 2026-09-22 11:25 — OpenRouter access
- Owner added `OPENROUTER_API_KEY` to `.env` (gitignored). Verified via /auth/key: paid tier, key valid.
- Use: empirical librarian bench across candidate models (bench/ directory, in progress).
