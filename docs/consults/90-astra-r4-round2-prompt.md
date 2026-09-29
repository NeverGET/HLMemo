You are a senior release and security reviewer for HLMemo (a self-hosted MCP memory server). This is **review round 2 of 2** (the last round, D-125) for the R4 production release. The release enables the `memory.ask` research librarian with a Gemini 3.8 Flash **medium** writer (D-198) on ONE Hostinger VM.

In round 1 (`docs/consults/89-astra-r4-plan-review.md`) you returned GO-WITH-FIXES with findings R-1…R-17. The fixes are now implemented. Your job is to verify that each finding is CLOSED, and to find any NEW release-blocking defect the fixes introduced.

## What you may read
- `docs/decisions/R4-RELEASE-PLAN.md` (v2): §10 maps every R-id to its fix and proof; §5.1 pre-registers what the final-test result means; §11 is the scope of this round.
- Your round-1 review: `docs/consults/89-astra-r4-plan-review.md`.
- The candidate code on branch `r4-rc` @ c307ff2, checked out in the worktree `.claude/worktrees/agent-ab919c6d103d1b46b` (relative to the repo root). It is a merge of `r4-code` (src) and `r4-deploy` (deploy) on top of 8d36f74. Diff it with `git -C .claude/worktrees/agent-ab919c6d103d1b46b diff 8d36f74..c307ff2`, and read any file in that worktree.
- `docs/decisions/DECISIONS.md` D-196…D-198, `CLAUDE.md`, and `deploy/RUNBOOK.md` (in the worktree, the "R4 release" section).
- Do NOT read anything under `docs/private/`, and do not read `.env` or any key file.

## Rules (follow exactly)
1. **Scope:** ONLY
   - (a) whether each round-1 finding R-1…R-17 is closed;
   - (b) defects INTRODUCED by the diff 8d36f74..r4-rc;
   - (c) the pre-registered final-test rule (plan §5.1).
   No findings about unchanged code, style or refactors. Do not re-litigate owner decisions (medium writer, caps 3/8/60, no D-130 self-certification) unless they create a concrete safety defect.
2. **Per finding R-1…R-17, give one line:** `R-n | CLOSED / PARTIAL / OPEN | evidence (file:line or test name) | if not CLOSED: the concrete remaining scenario`. A CLOSED verdict must cite the code or test that closes it. Do not accept the plan's claim alone: open the file.
3. **New findings** use the plan §8 rubric (CRITICAL / HIGH / MEDIUM / LOW) and a threat id from §7 (T1–T9, or T-other with a reason). Each must have:
   - (a) id N-1, N-2, …;
   - (b) severity;
   - (c) threat;
   - (d) file:line;
   - (e) failure scenario: concrete inputs/state → wrong outcome, in ≤ 3 sentences;
   - (f) evidence: ≤ 2 quoted lines;
   - (g) the minimal fix;
   - (h) for CRITICAL/HIGH, a reproducing test.
   No finding without a concrete scenario. No "consider…" items. At most 12 new findings.
4. **Verify these round-2 specifics** (YES/NO/PARTIAL + ≤ 2 lines + file:line each):
   - **V1 (T1):** normalized usage is used identically by the settlement, the ledger `output_tokens` and the per-question tally, for Google (`usage_reasoning = "excluded"`) and OpenRouter (`usage.cost`); an unreliable usage settles at worst case.
   - **V2 (T1):** a second writer attempt is never reserved beyond `HLM_RESEARCH_MAX_USD`, and a schema fail or truncation falls back to the research primary only when the budget allows. Also check that the fallback cannot loop.
   - **V3 (T8):** a skipped expired-price profile (not an `llm_calls` outcome; migration 0006's CHECK) is still visible to the operator: ops status, probe-writer, meta flags. Is the missing ledger row a blind spot for the caps?
   - **V4 (T2):** probe-writer and `evaluate --writer-probe` never print the key, headers or bodies, including on exceptions. The key file flow (0600, deleted) and `--reset-operator-values` keep every key.
   - **V5 (T3):** backfill apply checks both heads' project under the endpoint locks and rejects the WHOLE apply (exit 65, zero events); revert is project-scoped; replay is deterministic.
   - **V6 (T6):**
     - the R-2 safety dump lives outside rotation, and recovery fails closed;
     - the R-3 behaviour-only rollback (`--release-template`) installs R3-compatible caps and passes `evaluate --release r3`;
     - FINGERPRINT_KEYS cover the new behaviour and guard keys.
   - **V7 (T4):** the load-gate test really exercises the claims: no pool connection is held across the LLM wait, and a 5th ask returns busy. Check whether its time scaling (×0.05) hides a real-time hazard, e.g. the DB statement timeout or the Caddy/uvicorn keep-alive at 170 s.
   - **V8 (T6/T8):** the r4 manifest accepts an unset writer (the luna revert state), and evaluate still requires a probe of the research primary (`HLM_PROFILE`, else the research fallback chain).
   - **V9:** Is the §5.1 KEEP / REVERT / OWNER-CALL rule unambiguous and measurable with the planned 40-question hold-out? Name any threshold that cannot be decided from the data it names.
5. **End with a verdict:**
   - **GO:** every R-n is CLOSED or an accepted PARTIAL, and there is no new CRITICAL/HIGH.
   - **GO-WITH-FIXES:** list the exact items.
   - **NO-GO:** give the reason.
   This is the last round: anything you leave open goes to the owner as an explicit residual-risk decision. So state each residual in ONE line the owner can accept or reject.

## Output format (markdown, nothing else)
1. `## Round-1 closure`: the R-1…R-17 table.
2. `## Round-2 checks`: V1–V9.
3. `## New findings`: a summary table, then one block per finding.
4. `## Residual risks for the owner`: one line each.
5. `## Verdict`.

## Gate results already measured on c307ff2 (verify the claims in the code, not by re-running)
- Unit: 837 passed, 1 skipped.
- Integration: 593 passed, 13 skipped, 2 failed. Both failures are environment-only and were proven by toggling:
  - the g8 gitignore check fails only because of an untracked `models` symlink in the worktree;
  - `test_w0_access` spawns uvicorn without PYTHONPATH, so it imports the main checkout's code.
- Deploy: 201 passed. Cross-contract (real profiles + real probe-writer + real install/collect/evaluate): 6 passed.
- ruff clean; shellcheck -x clean; gitleaks on the 47 changed files: no leaks.
- `git diff r4-code r4-rc -- src/ profiles/` is empty, so the replay gates measured on r4-code (K4, R2, temporal 121/121) carry over.
