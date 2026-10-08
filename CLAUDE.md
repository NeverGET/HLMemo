# HLMemo: Human-Like Memory

A self-hosted, long-term memory backend for CLI coding agents (Claude Code, Codex, agy), served as one remote MCP
server (`hlm`, streamable HTTP). Reads are token-budgeted and progressive: clues first, then drill down.

## Direction
- **The hybrid model (D-246).**
  - Project chats write memory under the protocol in `docs/protocol/HLMEMO-PROTOCOL.md`.
  - The orchestrator acts as the library operator in sessions the owner starts.
  - The server librarian answers `memory.ask` (the research librarian of D-130) and flags candidates; it does not act on its own.
  - A fully autonomous librarian is a later goal.
- **The aim is LLM quality, not infrastructure polish.**
  - Measure a ceiling (an oracle or prototype run on real data, at most a day) before building an LLM-quality feature.
  - Treat the first failed measurement as a reason to pause and talk to the owner.
- **Provider-agnostic (D-017).** Models, embedders, the DB and hosting are configuration. Model ids live only in provider profiles.

## How we work here
- **Decisions are appended to `docs/decisions/DECISIONS.md`.**
  - `docs/research/00-deep-research-report.md` is the design baseline; a deviation gets a decision entry.
  - Chat is transient, so decisions belong in files.
- **Validate locally first.** Docker compose plus the deterministic gates, before anything reaches the VPS.
- **Reviews are proportionate.**
  - Codex `gpt-6-astra` at low effort is the everyday second opinion. Record consults in `docs/consults/`.
  - One-way doors (data, security, releases) get Astra low and `gpt-6.1-sol` xhigh in parallel (owner, 2026-10-08; consult 120). Write the threat model and the severity rubric before the review, cap it at 2 rounds, and give a HIGH finding a reproducing test. The owner decides any residual risk.
  - Command: `codex exec --skip-git-repo-check -s read-only -m gpt-6-astra -c model_reasoning_effort="low" -o <out.md> - < <prompt.md>`.
- **Keep 2-3 workstreams at most,** each with a timebox, and check in with the owner when a timebox ends.
- **Production is Hostinger VM 2002259 (mcp.hlmemo.com).**
  - Deploys and prod data writes happen with the owner's OK, following `deploy/RUNBOOK.md`.
  - Prod gates run with `--no-drill`, because the drill restores over live data.
  - The Mac reaches the server over IPv6.
- **The GitHub repo is public.**
  - Owner-project names and data stay in the gitignored `docs/private/`.
  - The `.githooks/pre-push` privacy gate blocks a leaking push. Fix the finding rather than bypass it.
  - Prod memory is private and may hold owner-project data.

## Memory
- **This project dogfoods HLMemo.**
  - The project is `hlmemo`; its SessionStart brief arrives at session start, and the `hlmemo` skill explains reading and writing.
  - Repo docs remain the source of truth and are synced into memory with `hlm import markdown … --keep-missing`, dry run first.
- **Resume from `docs/status/STATUS.md`** and the newest decisions.

## Layout
`docs/research/` inputs · `docs/consults/` reviews · `docs/decisions/` ADR log and plans · `docs/status/` state and backlog ·
`docs/protocol/` the writer protocol · `deploy/` runbook and scripts · `integrations/claude/` the skill · `src/hlmemo/` the code.
