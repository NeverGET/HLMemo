# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-23 — PRODUCTION LIVE at https://mcp.hlmemo.com (D-047). main = 912f2d3+.

## Where we are
- Phase 0 (LLM-free core + deploy tooling) is DONE (D-041..D-044). Production deploy DONE (D-047): Hostinger KVM 2, Vilnius, Ubuntu 26.04, release 912f2d3, Let's Encrypt TLS; all 8 remote gates PASS; G7 claude + agy PASS against the remote URL; codex blocked only by its OpenAI quota (resets 2026-09-29).
- Model strategy (D-036/D-043): gpt-6-astra (codex) = implementer when available (quota back 2026-09-29); until then Claude subagents implement; every change gets a neutral verifier + a separate adversarial review.
- Embeddings (D-042): local e5-small for launch; gemini-embedding-2 is a Phase-1 opt-in profile.
- Non-blocking issues: docs/status/BACKLOG.md.

## Operating production
- Local deploy state (secrets, admin token, pinned host key, ssh_config): deploy/.local/153.92.1.166/ (gitignored, 0700). SSH: `ssh -F deploy/.local/153.92.1.166/ssh_config hlm-deploy`. Root/password SSH is disabled; deploy user `hlmdeploy`, key ~/.ssh/hlmemo_deploy_ed25519.
- Upgrade: `bash deploy/scripts/deploy.sh` (see deploy/RUNBOOK.md). Gates: `bash deploy/scripts/remote_gates.sh --url https://mcp.hlmemo.com --admin-token-file deploy/.local/153.92.1.166/admin.token` (add --no-drill to skip the restore over live data).
- The three CLIs on the owner's Mac are registered to production (`hlm` MCP, device g7-<host>, project gates-g7); config backups in deploy/.local/backups/g7-*.

## Next
1. 2026-09-29+: re-run `remote_gates.sh ... --no-drill --g7` to close g7-codex (quota only).
2. D-021 self-hosting: create the real `hlmemo` project on production, register this Mac's device for it, start using HLMemo's own memory; then migrate legacy memories (NotebookLM/serena/auto-memory), per-project reconstruction (D-020).
3. Owner: rotate the Hostinger API token (it was pasted into chat); answer KVKK (work-computer memories).
4. Phase 1: librarian (OpenRouter, D-019) per the deep-research report.

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
