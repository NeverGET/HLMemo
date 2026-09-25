# HLMemo — Human-Like Memory

Self-hosted, unified long-term memory backend for CLI coding agents (claude-code, codex, antigravity-cli),
exposed as ONE remote MCP server (streamable HTTP). Token-budgeted, clue-based progressive disclosure;
async "librarian" LLM for placement/contradiction/consolidation.

## Working agreement (owner: Cemal)
- **Coworker/reviewer models (D-085):** routine consults/reviews → codex `gpt-6-astra` at reasoning **low**; CRITICAL reviews (release gates, security/privacy, data integrity, migrations) → run BOTH `gpt-6-astra` low and `gpt-5.6-sol` xhigh in parallel and merge the findings (their blind spots differ).
  Consult on every non-trivial design/implementation decision; record the exchange in `docs/consults/`.
  Invocation: `codex exec --skip-git-repo-check -s read-only -m gpt-6-astra -c model_reasoning_effort="low" -o <out.md> - < <prompt.md>` (critical: also `-m gpt-5.6-sol -c model_reasoning_effort="xhigh"`)
- **Local-first validation:** everything runs in docker compose locally and passes the deterministic gate
  (`docs/decisions/` → validation gates) BEFORE any VPS deploy.
- **Decisions live in `docs/decisions/DECISIONS.md`** (append-only ADR log). Chat is transient; files are real.
- **Source of truth for design:** `docs/research/00-deep-research-report.md` (deep-research report, Turkish).
  Deviations from it must be logged as a decision with rationale.

## Product goal and working rules (D-125, D-130)
- **The goal is the RESEARCH LIBRARIAN (D-130).** The caller sends a question with its project context. The librarian uses the project's Memory Map, runs several internal queries, and answers LLM-to-LLM with a refined answer, its primary sources and related sources. Every source is a handle the caller can drill into or pull raw.
- **Production Ready is self-certified.** When the Production Ready gate in D-130 passes on REAL migrated memory, the release certifies itself; no owner OK is needed.
- **Engineering depth goes into LLM performance**, the product's core job, not into infrastructure polish.
- **Ceiling first.** Before building any LLM-quality feature, spend at most one day on an oracle run or prototype on real data. If the ceiling is low, do not build it.
- **Reviews.**
  - Dual review is only for one-way doors: data, security, release.
  - Write the threat model and the severity rubric before the review starts.
  - At most 2 rounds; after that, the owner accepts or rejects the remaining risk explicitly.
  - A HIGH finding needs a test that reproduces it.
- **Parallelism and timeboxes.** Run at most 2–3 workstreams, and finish one before starting another. Timebox every workstream and check in with the owner when the timebox expires. Call a strategic pause after the first failed measurement.
- **Dogfooding.** HLMemo is used as the memory of this project itself.

## Product principle (D-017): provider-agnostic
Librarian model, embedding model, DB and hosting are configuration, never code. Never hard-code a vendor, model id or model-specific prompt quirk outside a provider profile. `bench/` is a user-facing tool for choosing a model.

## Layout
- `docs/research/`   — research inputs (deep-research report, model/VPS analyses)
- `docs/consults/`   — codex ⇄ claude design exchanges (numbered)
- `docs/decisions/`  — ADR log + validation gates
- `docs/status/`     — save-state for resuming sessions (STATUS.md is the first thing to read)

## Project Memory
This project is deliberately NOT on the NotebookLM-first protocol (D-020): HLMemo is the target that legacy memories (NotebookLM, serena, auto-memory) will be migrated INTO. Use docs/status/STATUS.md + docs/decisions/ as the memory layer here; Claude auto-memory is fine for personal working notes.
