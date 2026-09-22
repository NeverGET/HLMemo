# HLMemo — Human-Like Memory

Self-hosted, unified long-term memory backend for CLI coding agents (claude-code, codex, antigravity-cli),
exposed as ONE remote MCP server (streamable HTTP). Token-budgeted, clue-based progressive disclosure;
async "librarian" LLM for placement/contradiction/consolidation.

## Working agreement (owner: Cemal)
- **Coworker model:** codex CLI with `gpt-6-astra` (reasoning high/xhigh) is an active co-architect.
  Consult it on every non-trivial design/implementation decision; record the exchange in `docs/consults/`.
  Invocation pattern: `codex exec --skip-git-repo-check -s read-only -m gpt-6-astra -c model_reasoning_effort="xhigh" -o <out.md> - < <prompt.md>`
- **Local-first validation:** everything runs in docker compose locally and passes the deterministic gate
  (`docs/decisions/` → validation gates) BEFORE any VPS deploy.
- **Decisions live in `docs/decisions/DECISIONS.md`** (append-only ADR log). Chat is transient; files are real.
- **Source of truth for design:** `docs/research/00-deep-research-report.md` (deep-research report, Turkish).
  Deviations from it must be logged as a decision with rationale.

## Product principle (D-017): provider-agnostic
Librarian model, embedding model, DB and hosting are configuration, never code. Never hard-code a vendor, model id or model-specific prompt quirk outside a provider profile. `bench/` is a user-facing tool for choosing a model.

## Layout
- `docs/research/`   — research inputs (deep-research report, model/VPS analyses)
- `docs/consults/`   — codex ⇄ claude design exchanges (numbered)
- `docs/decisions/`  — ADR log + validation gates
- `docs/status/`     — save-state for resuming sessions (STATUS.md is the first thing to read)

## Project Memory
Not yet migrated to NotebookLM-first memory (decision pending, see STATUS.md).
