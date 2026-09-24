# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-23 — PRODUCTION LIVE at https://mcp.hlmemo.com; e2e COMPLETE — G7 3/3 CLIs PASS (D-047, D-048). main = 912f2d3+. Active goal: D-051/D-058 (Phases 2-5). R1 live (D-068).

## Where we are
- Phase 0 (LLM-free core + deploy tooling) is DONE (D-041..D-044). Production deploy DONE (D-047): Hostinger KVM 2, Vilnius, Ubuntu 26.04, release 912f2d3, Let's Encrypt TLS; all 8 remote gates PASS; G7 claude + codex + agy all PASS against the remote URL (D-048).
- Model strategy (D-050, supersedes D-036/D-043/D-048): Claude (Opus 5.5) subagents implement; codex gpt-6-sol is co-architect + adversarial reviewer; every change gets a neutral verifier + a separate adversarial review.
- Embeddings (D-042): local e5-small for launch; gemini-embedding-2 is a Phase-1 opt-in profile.
- Non-blocking issues: docs/status/BACKLOG.md.

## Operating production
- Local deploy state (secrets, admin token, pinned host key, ssh_config): deploy/.local/153.92.1.166/ (gitignored, 0700). SSH: `ssh -F deploy/.local/153.92.1.166/ssh_config hlm-deploy`. Root/password SSH is disabled; deploy user `hlmdeploy`, key ~/.ssh/hlmemo_deploy_ed25519.
- Upgrade: `bash deploy/scripts/deploy.sh` (see deploy/RUNBOOK.md). Gates: `bash deploy/scripts/remote_gates.sh --url https://mcp.hlmemo.com --admin-token-file deploy/.local/153.92.1.166/admin.token` (add --no-drill to skip the restore over live data).
- The three CLIs on the owner's Mac are registered to production (`hlm` MCP, device g7-<host>, project gates-g7); config backups in deploy/.local/backups/g7-*.

## OVERNIGHT PLAN (owner, 2026-09-23 22:32) — execute on wake (effort xhigh), in roadmap order, skip nothing
Deliverable by morning: a FINAL e2e PROD TEST REPORT (docs/status/E2E-PROD-REPORT.md + summary to the owner).
1. Test the current R1 system; fix/optimize what is found (carried items: D-015 skeleton card on project create, ops status/librarian subcommands, bench-v2 gold adjudication of the 11 ceiling misses).
2. Make the LLM infrastructure + librarian work with the primary models (gpt-6-luna default, deepseek-v4.1-flash fallback; D-066/D-067): W2b (placement/contradiction/cross-project, observer role), the needed W1.5 importer + `hlm export`, the D-067 robustness guards; every gate (G-L*, G-L3 on the 2 vCPU VM, the live gate, Sol review).
3. Librarian tests (bench v2 live gate, real-data corpus A/B); fix/optimize.
4. Only when everything works as promised: release to prod (R2: librarian ON, observer role; llm.env with the OpenRouter key on the server), then import HLMemo's own memory (a TEST import: it will be wiped and redone from scratch in the final release; the controlled "cold-start" supervised librarian training on owner projects is Phase 5 with the owner) and run the first e2e prod test (corpus B dev + sealed where allowed, agent A/B if feasible). Report results, gaps, bugs, improvements.
Owner framing: the librarian's gradual supervised learning (observe → apply) happens during the per-project migration (Phase 5), done by us as the cold-start; end users get the prepared infrastructure. After the report: fallback-model research (pro-tier fallback), evaluate the e2e test, then 3-4 owner projects for supervised librarian training.

### Overnight progress log
- 01:07 run started. D-069: wave 1 launched in parallel (own worktrees/DBs): A = W1.5 import/export + skeleton card + ops status (0007_import); B = W2b+W2c + D-067 guards + ops librarian (0008_librarian_tasks, relink at merge); C = W2d risk_check/register_lesson + preflight; D = W2f hlm bench + bench-v2 gold adjudication + leaderboard.
- 02:06 W2f MERGED (f239fcc, D-070) after Sol 40 fixes; main unit 424 passed.
- 02:5x W2d done (Sol 41 → fixing; D-071 risk fallback = retrieval-only). W1.5 done (G-I4 0.794 open, D-072) → Sol 42 reviewing. B (W2b/W2c) still running.
- W1.5 MERGED (0c0c013, D-073), main unit 447 passed, head 0007_import.
- W2d MERGED locally (efc2f4e + post-merge fix 4a0b7e2; conflicts in write_service/mcp_server/tools resolved; librarian_priority unified). Sol 43 on W1.5 delta = FIX-NEEDED (forged origin, dup keys in 0007, close links, section remap, mass-close) → A fixing on branch w15-fix2.
- R2 integration TODO: mount llm.env into the api service too (risk_judge and W2e synthesis run in the api; today only the librarian gets it); remove the HLM_LIBRARIAN_ENABLED pin; wire W2b enqueue to batch.librarian_priority (2 lessons, 6 imports, else 3) and record the job in resolved.jobs; relink 0008 → 0007; HLM_PROFILE=openrouter-gpt6-luna in prod llm.env.
- Next: Sol review + neutral verify per branch (max 2 fix rounds) → wave 2 W2e → integrate (0008→0007 relink) → full gates + G-L3 on the VM with the librarian ON → R2 rehearsal on the VM → R2 prod (llm.env, observer) → HLMemo test import → e2e (corpus B dev) → docs/status/E2E-PROD-REPORT.md.

## GOAL (D-051/D-058) — active
Phases 2-5 per docs/decisions/PHASE2-4-ROADMAP.md → gates → incremental releases → final acceptance (HLMemo self-migration, then the corpus-A project) → delete local dev stack + lima VMs.
State 2026-09-23 night: **R1 is LIVE in production** (ee6ce9c, D-068): D-055 retrieval, W0a access hardening (ops over SSH, closed registration), W2a librarian foundation (DISABLED). Owner device id 21 `cemals-mb-pro-3` (hlmemo:write); the three CLIs point to production; the repo hlm.toml points to production, project hlmemo. **Dogfood D1 starts in the next chat** (a restart is needed so the MCP connection uses device 21).
Model tiers (D-066/D-067): default gpt-6-luna, fallback deepseek-v4.1-flash; the Phase 5 two-tier luna/luna-pro needs a non-OpenAI pro fallback (bench v2 candidates); LLM robustness requirements D-067.
Next (in order):
1. After a soak: `PATH="$PWD/deploy/.local/153.92.1.166/bin:$PATH" bash deploy/scripts/deploy.sh --accept-release hlm-deploy` (deletes the pre-W0 secret backups on the server).
2. W1.5 importers (markdown/auto-memory/serena/context files; `hlm export`) → the corpus-B dev baseline via the importer.
3. W2b placement/contradiction/cross-project (observer role) + W2f `hlm bench` integration of bench v2; the gold adjudication of the 11 ceiling misses.
4. Before R2 (librarian on): G-L3 on the 2 vCPU VM, Sol review, the live gate on gpt-6-luna.
Operating: device ops `bash deploy/scripts/hlm_ops.sh --state deploy/.local/153.92.1.166 device list|mint|revoke|rotate`; deploy with the full SHA and `--accept-compose-change=<sha256>` when compose.prod.yaml changes. Rollback to pre-W0 is refused (one-way door, D-065).
Rules: implementers never touch the DBs hlm/hlm_verify/hlm_retr (D-056); `ssh -n` in scripts; hold-out sets (docs/private/realdata-*, bench-v2 private) are never shown to implementers.

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
