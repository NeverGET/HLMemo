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
RESUMED 2026-09-23 18:31 (the three agents were sent "continue"). History: paused ~17:10 for an owner break.
- W0a (worktree .claude/worktrees/agent-a142ee2b9b4948da6, last commit 6ddd2c8; 11 uncommitted files) was fixing Sol 36 (docs/consults/36-sol-review-w0a-delta.md): a HIGH-severity manual rollback that does not restore env secrets → rollback.sh through the runner; atomic release-state.json; ops status without the DB. It stopped at "run gates". State: 6ddd2c8 is verified (neutral verifier: all CONFIRMED); Sol 36 #1/#3/#5/#6 FIXED.
- W2a (worktree .claude/worktrees/agent-a21f9ce923d827a45, last commit 6b88877; 7 uncommitted files): Sol 35 findings #1,#2,#5-#8 fixed and committed; G-L3 still FAILS (6.7 s p95 under writes). It was implementing D-063 (GIN fastupdate=off in 0006, stale-while-revalidate DF cache, a gate-release target) and stopped at the write-cost measurement.
- Bench v2 (bench/v2 + docs/private/bench-v2; UNCOMMITTED in the main checkout: bench/run.py, bench/README.md, bench/v2/, a partial result): the builder was stopped during its gpt-6-luna smoke run at 136/200 cases. Resume it via SendMessage. Next: runner/smoke → calibration with a ceiling model → final 4 models × 3 reps (gpt-6-luna, deepseek-v4.1-flash, gpt-6-luna-pro, gemini-3.1-flash-lite) → Sol review → report.
- Leftover test DBs may exist (hlm_test_w0*, hlm_test_w2a*, hlm_retr_w2a); drop them once the agents finish.
Owner instruction for the pause: finish these, report the bench v2 results, and do NOT start merges, deploys or new workstreams until the owner returns.
Resume order afterwards: verify W0a fixes → merge W0a → merge W2a (relink 0006→0005; integrate the librarian lifecycle into remote-deploy.sh; main@head) → R1 cutover rehearsal on lima hlm-2604 → production R1 → mint the owner's device (hlm_ops.sh) → dogfood D1.
Production still runs 912f2d3 (Phase 0). The local dev stack runs main at 0004 with corpus A in project yt-eval.
Rules: implementers never touch DBs hlm/hlm_verify/hlm_retr (D-056); always `ssh -n` in scripts; hold-out sets (docs/private/realdata-*) are never shown to implementers.

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
