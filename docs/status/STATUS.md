# STATUS — HLMemo (read this first when resuming)
Updated: 2026-09-23 — PHASE 0 DONE (D-044). main = c737fc5+.

## Where we are
- Phase 0 (LLM-free core + deploy tooling) is DONE and verified; see DECISIONS D-041..D-044 and docs/bakeoff/closing/.
- Model strategy (D-036/D-043): gpt-6-astra (codex) = implementer when available (quota back 2026-09-29); until then Claude subagents implement; every change gets a neutral verifier + a separate adversarial review.
- Embeddings (D-042): local e5-small for launch; gemini-embedding-2 is a Phase-1 opt-in profile.
- Non-blocking issues: docs/status/BACKLOG.md.

## Next: VPS stage (D-032)
Owner inputs needed:
1. Provider purchase: Hostinger KVM 2 (≈€8.71/mo incl. KDV on 24 months) or OVH VPS-2 (≈€8.65/mo incl. VAT, daily backups) — docs/research/06. Choose Ubuntu 24.04, EU location.
2. Install the deploy SSH public key at purchase (key: ~/.ssh/hlmemo_deploy_ed25519.pub on the owner's Mac).
3. Domain (A/AAAA record) or the sslip.io fallback (hlm.<ip-dashed>.sslip.io).
4. KVKK answer (work-computer memories and third-party personal data).
Then: deploy/bootstrap.sh over SSH → deploy/scripts/deploy.sh → remote gates (TLS /ready, G5 isolation, G7 three CLIs against the remote URL, WAN latency) → backup/restore drill on the server → D-021 self-hosting.

## Local environment facts
- Dev stack: compose.yaml (project hlmemo). Its image tag `hlmemo:dev` was rebuilt from the fix-auth tree during verification (same code as main now).
- Test DBs on the compose Postgres (hlm_retr = cached G3 embeddings; never truncate). Always run pytest with HLM_TEST_DSN, under bash for ignore lists.
- Worktrees under /Users/cemalkurt/Projects/HLMemo-bake/ are finished; branches merged.
