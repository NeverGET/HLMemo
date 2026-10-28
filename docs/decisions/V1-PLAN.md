# HLMemo v1 plan: from an operated system to a product (2026-10-28)

## Scope (owner, 2026-10-28; D-269)
- **Self-hosted, one owner.** One owner runs their own server for many devices and projects (today's architecture).
  A hosted service is a later question: if self-hosting cannot give every user the same experience, we discuss a
  service that we operate, with data security and privacy built in.
- **v1 supports Claude Code only.** Codex and agy keep working over MCP, but get no hooks and no support promise.
- **The user picks the model.** We publish, with charts, how each model and effort level performs on HLMemo's own
  jobs and what it costs; the profile is the user's choice. Our own default favours quality over price (Claude
  Haiku 5.5 at several effort levels, Claude Opus 5.5 low/medium, prompt caching), measured, not assumed.

## The problem v1 solves
Today quality comes from operator work: the orchestrator verifies librarian proposals, curates migrations, promotes
lessons, and project chats test-drive the system for days. A user who clones the repository will not do that. v1 turns
each piece of operator work into one of three things:
1. **Code or a gate** that runs without a person (a guard, an eval in the release gate);
2. **A safe default** that switches the risky part off;
3. **A small review queue** the user can clear in minutes (`hlm review`).

Test drives and dogfooding remain *our* evaluation source; users receive releases, they do not collect feedback.

## The feature ladder
Every LLM feature has one switch with four levels: `off → observe → suggest → act`.
- `observe`: runs, logs, changes nothing visible.
- `suggest`: shown to the user or the chat as a proposal, applied only on acceptance.
- `act`: applied automatically, as a reversible event.

A release ships each feature at the level its measurement supports. A level goes up only after our own eval passes
its bar on fresh data; never on user feedback alone. This is how later features and promotions arrive without
reopening v1.

## Workstreams (at most 2–3 at a time)
| # | Workstream | Exit criterion |
|---|---|---|
| W1 | **Librarian, one narrow class to `act`** | historical-kind rule in the librarian path (D-269); decisive fresh-set measurement; a shadow week; then `act` only for "a newer item replaces part of a current fact", everything else `suggest` |
| W2 | **Model report** | a model × effort × caching matrix on HLMemo's jobs (write_review, verify, memory.ask, risk judge) with quality, cost and latency; published as charts with the recommended profiles |
| W3 | **Install and onboarding (Claude Code)** | `hlm setup` from a clean machine: compose up, owner device, first project, MCP registration, hooks (brief, digest, capture), backups on by default; a light first import (CLAUDE.md, auto-memory) with the secret scan; no private wrapper needed |
| W4 | **Proactive recall** | ceiling first: trigger-tagged procedural lessons, then an LLM relevance judge over the top candidates; ships at `suggest` only if useful ≥ the bar and harmful = 0 |
| W5 | **Hardening for strangers** | dual security review of the install path and defaults; the upgrade path (migrations, rollback) tested from the previous release |

Order (owner, 2026-10-28): **our own goals first, packaging last.**
- First, every feature works soundly on our own projects: W1 and W2 now, then W4 and the quality items in the backlog.
- Then installability and deployment (W3, W5) are re-assessed against that final state and developed from it.

## Not in v1
A hosted service; multi-user servers; hooks for Codex/agy; consolidation and decay; a fully autonomous librarian.

## v1.0 release gate
- The deterministic gates and the release gate (D-063) pass.
- A clean-machine install by `hlm setup` reaches a working brief in a new Claude Code session.
- Each LLM feature's level is backed by a recorded measurement (the model report).
- The dual review of W5 is closed.
- README and USAGE describe the product as it is.
