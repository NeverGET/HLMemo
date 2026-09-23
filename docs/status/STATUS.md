# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-23 — PRODUCTION LIVE at https://mcp.hlmemo.com; e2e COMPLETE — G7 3/3 CLIs PASS (D-047, D-048). main = 912f2d3+. Active goal: D-051 (Phases 2-4).

## Where we are
- Phase 0 (LLM-free core + deploy tooling) is DONE (D-041..D-044). Production deploy DONE (D-047): Hostinger KVM 2, Vilnius, Ubuntu 26.04, release 912f2d3, Let's Encrypt TLS; all 8 remote gates PASS; G7 claude + codex + agy all PASS against the remote URL (D-048).
- Model strategy (D-036/D-043): gpt-6-astra (codex) = implementer; quota restored 2026-09-23 by the owner (D-048); every change gets a neutral verifier + a separate adversarial review.
- Embeddings (D-042): local e5-small for launch; gemini-embedding-2 is a Phase-1 opt-in profile.
- Non-blocking issues: docs/status/BACKLOG.md.

## Operating production
- Local deploy state (secrets, admin token, pinned host key, ssh_config): deploy/.local/153.92.1.166/ (gitignored, 0700). SSH: `ssh -F deploy/.local/153.92.1.166/ssh_config hlm-deploy`. Root/password SSH is disabled; deploy user `hlmdeploy`, key ~/.ssh/hlmemo_deploy_ed25519.
- Upgrade: `bash deploy/scripts/deploy.sh` (see deploy/RUNBOOK.md). Gates: `bash deploy/scripts/remote_gates.sh --url https://mcp.hlmemo.com --admin-token-file deploy/.local/153.92.1.166/admin.token` (add --no-drill to skip the restore over live data).
- The three CLIs on the owner's Mac are registered to production (`hlm` MCP, device g7-<host>, project gates-g7); config backups in deploy/.local/backups/g7-*.

## GOAL (D-051) — active
Phases 2, 3 and 4 working as promised → all gates → production deploy → HLMemo migrates itself into production memory (first prod test, fresh chat after MCP reload) → then delete local dev stack + lima VMs (keep them until then).
- Plan: docs/decisions/PHASE2-4-ROADMAP.md (being drafted; Sol review next, then owner questions).
- W0 first: manual server-side device registration, public self-registration off (D-052); extra VPS security layer (D-053 OPEN).
- Coworker: codex gpt-6-sol (D-050); Claude subagents implement.
- Deferred by owner: backup timer/off-host copies (not installed on purpose), KVKK (D-049), Hostinger token rotation (owner's call).

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
