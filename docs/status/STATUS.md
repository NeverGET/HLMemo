# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-22 12:05

## Phase: -1 (report analysis + foundational decisions). No product code yet. Repo public: https://github.com/NeverGET/HLMemo

## Done this session
- codex consults #01 (critique) and #02 (debate, converged) → docs/consults/
- research: librarian models (DeepSeek 0731 retired + EU-privacy blocked) → docs/research/01
- research: VPS (Hetzner CX43 proposed) → docs/research/02
- decisions D-001..D-016 logged; VALIDATION-GATES.md drafted
## Done (cont.)
- bench full run complete → D-019: deepseek-v4.1-flash (reasoning off) primary, gpt-5.6-luna fallback
## In flight
- Phase-0 spec (Claude Plan agent) + codex consult #03 (DDL + interfaces), to be merged

## Open decisions (owner must answer)
- Privacy tier for cloud librarian (PII scrubbing / EU routing / per-project opt-out)
- NotebookLM migration for this project (global protocol asks to confirm)
- Language: Python vs TypeScript for MCP server (Claude leans Python)

## Tracks (D-018)
- PUBLIC: NeverGET/HLMemo (generic product)
- PRIVATE: NeverGET/hlmemo-ops (our deployment; create at first deploy artifact)

## Next
- Owner answer pending: Hetzner CX43 confirm (D-005). NotebookLM question RESOLVED (D-020: HLMemo is the import target; legacy estate inventory in progress).
- Phase-0 implementation plan (7 tables, 5 tools, hlm wrapper) → consult codex #03 on schema DDL

## 2026-09-22 11:25 — OpenRouter access
- Owner added `OPENROUTER_API_KEY` to `.env` (gitignored). Verified via /auth/key: paid tier, key valid.
- Use: empirical librarian bench across candidate models (bench/ directory, in progress).
