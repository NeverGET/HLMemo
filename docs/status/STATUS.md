# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 12:05

## Phase: -1 (report analysis + foundational decisions). No product code yet. Repo public: https://github.com/NeverGET/HLMemo

## Done this session
- codex consults #01 (critique) and #02 (debate, converged) → docs/consults/
- research: librarian models (DeepSeek 0731 retired + EU-privacy blocked) → docs/research/01
- research: VPS (Hetzner CX43 proposed) → docs/research/02
- decisions D-001..D-016 logged; VALIDATION-GATES.md drafted
## In flight
- bench agent: empirical librarian bench harness (bench/) + 2-model smoke run

## Open decisions (owner must answer)
- Privacy tier for cloud librarian (PII scrubbing / EU routing / per-project opt-out)
- NotebookLM migration for this project (global protocol asks to confirm)
- Language: Python vs TypeScript for MCP server (Claude leans Python)

## Next
- Owner answers: privacy tier (D-016), NotebookLM migration, Hetzner CX43 confirm (D-005)
- Full bench run on shortlist (qwen3.8-flash, gpt-5.6-luna, mistral-small-2603 + deepseek-flash as control)
- Phase-0 implementation plan (7 tables, 5 tools, hlm wrapper) → consult codex #03 on schema DDL

## 2026-09-22 11:25 — OpenRouter access
- Owner added `OPENROUTER_API_KEY` to `.env` (gitignored). Verified via /auth/key: paid tier, key valid.
- Use: empirical librarian bench across candidate models (bench/ directory, in progress).
