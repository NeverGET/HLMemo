# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-23 — PRODUCTION LIVE at https://mcp.hlmemo.com; e2e COMPLETE — G7 3/3 CLIs PASS (D-047, D-048). main = 912f2d3+. Active goal: D-051/D-058 (Phases 2-5).

## Where we are
- Phase 0 (LLM-free core + deploy tooling) is DONE (D-041..D-044). Production deploy DONE (D-047): Hostinger KVM 2, Vilnius, Ubuntu 26.04, release 912f2d3, Let's Encrypt TLS; all 8 remote gates PASS; G7 claude + codex + agy all PASS against the remote URL (D-048).
- Model strategy (D-050, supersedes D-036/D-043/D-048): Claude (Opus 5.5) subagents implement; codex gpt-6-sol is co-architect + adversarial reviewer; every change gets a neutral verifier + a separate adversarial review.
- Embeddings (D-042): local e5-small for launch; gemini-embedding-2 is a Phase-1 opt-in profile.
- Non-blocking issues: docs/status/BACKLOG.md.

## Operating production
- Local deploy state (secrets, admin token, pinned host key, ssh_config): deploy/.local/153.92.1.166/ (gitignored, 0700). SSH: `ssh -F deploy/.local/153.92.1.166/ssh_config hlm-deploy`. Root/password SSH is disabled; deploy user `hlmdeploy`, key ~/.ssh/hlmemo_deploy_ed25519.
- Upgrade: `bash deploy/scripts/deploy.sh` (see deploy/RUNBOOK.md). Gates: `bash deploy/scripts/remote_gates.sh --url https://mcp.hlmemo.com --admin-token-file deploy/.local/153.92.1.166/admin.token` (add --no-drill to skip the restore over live data).
- The three CLIs on the owner's Mac are registered to production (`hlm` MCP, device g7-<host>, project gates-g7); config backups in deploy/.local/backups/g7-*.

## GOAL (D-051/D-058) — active
Phases 2-5 per docs/decisions/PHASE2-4-ROADMAP.md (contracts frozen D-060) → gates → incremental releases R1-R4 → final acceptance: HLMemo self-migration, then the corpus-A project (YouTube-Automation), then more owner projects if needed → delete local dev stack + lima VMs.
Done today: real-data baseline D-054 → retrieval fixes D-055 (merged 98bec35) → re-measured D-057 (corpus A L2 .75/.77, hit@3 .74; temporal stale-first regressed 4→8/15, a W2b gate) → dev-DB truncation incident + guard D-056 → owner answers D-058 (Phase 5 migration replaces shadow mode; no dev spend cap) → Hostinger firewall active D-059.
In flight (2026-09-23): W0a implementer (worktree branch, own DB hlm_test_w0) and the corpus-B builder (docs/private/realdata-hlmemo/, sealed hold-out; must exist BEFORE HLMemo's memory is imported).
Next: verify + Sol-review W0a → merge → release R1 to prod (D-055 + W0; migrate_env_w0; mint the owner's device via hlm_ops.sh) → dogfood D1 → W1.5 importers / W2a librarian foundation.
Production still runs 912f2d3 (Phase 0). The local dev stack runs main at 0004 with corpus A in project yt-eval.
Rules: implementers never touch DBs hlm/hlm_verify/hlm_retr (D-056); always `ssh -n` in scripts; hold-out sets (docs/private/realdata-*) are never shown to implementers.

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
