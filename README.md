# HLMemo — Human-Like Memory

Self-hosted, unified long-term memory backend for CLI coding agents (claude-code, codex, antigravity-cli), exposed as a single remote MCP server over streamable HTTP.

Core ideas: token-budgeted, clue-based progressive disclosure; bi-temporal facts (what was true when, and when we learned it); an asynchronous "librarian" LLM for placement, contradiction handling and consolidation; cross-project experience layer.

Status: design phase (September 2026). See `docs/status/STATUS.md` for the live state and `docs/decisions/DECISIONS.md` for the decision log.

## Layout
- `docs/research/` — deep-research report and provider/model analyses
- `docs/consults/` — design exchanges between the Claude orchestrator and the codex co-architect
- `docs/decisions/` — ADR log and validation gates
- `docs/status/` — resumable save-state
- `bench/` — empirical librarian-model benchmark (OpenRouter)

## Design principles
- **Bring your own model.** The librarian LLM is any OpenAI-compatible endpoint (OpenRouter, a vendor API, or local vLLM/Ollama), chosen by configuration. The embedding model, database and hosting target are configuration too. `bench/` lets you measure candidate models on the actual librarian jobs with your own key before you pick one; our results are a default, not a lock-in.
- **Local first.** The whole stack runs in Docker Compose and must pass the deterministic validation gates before any server deployment.
- **Evidence is never deleted.** Forgetting affects ranking and reversible archival only.

## Secrets
Copy `.env.example` to `.env`. `.env` is gitignored and a pre-commit hook (`.githooks/pre-commit`, gitleaks + pattern grep) blocks secret-like content. Enable it with `git config core.hooksPath .githooks`.
