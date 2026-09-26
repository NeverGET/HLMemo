# Consult 80 — round 2 of 2 (D-125 cap), VERIFICATION ONLY: the review-79 fixes on memory.ask (9bceb59..bc333bd)

Your cwd is a clean export of bc333bd. `FIX79.patch` = 9bceb59..bc333bd. Round 1 is in docs/consults/79-*review-memory-ask.md, and the implementer's fix table is docs/consults/79-response-review-memory-ask.md. Read-only.
**Scope:**
- Verify that each round-1 finding T1, T2, T4 (both), T5 and T6 is closed.
- Check for regressions inside the changed code, including the T5 deploy/manifest changes: CODE_RELEASE=r4, a manifest that follows the env label, the R3 manifest keeping research off, install_llm_env writing the r4 marker, and the RUNBOOK R4 section.
- A NEW finding counts only if it is HIGH and comes with a concrete reproducing scenario. Everything else is listed as residual for the owner's decision.

## Output (≤ 20 lines)
- `## Verdict (MERGE | FIX-NEEDED)`
- Per finding: T1, T2, T4-high, T4-med, T5, T6 → FIXED or OPEN, with file:line.
- New HIGH, only with a reproducing scenario: file:line | scenario | fix.
- `## Residual (owner decision)`: ≤ 5 bullets.
