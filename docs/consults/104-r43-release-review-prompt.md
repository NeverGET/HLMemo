# Consult 104: R4.3 release review, ROUND 1 of 2 (D-125 one-way door: release; D-085 critical: Astra low + Sol xhigh)

You review a clean `git archive` of branch `r4.3` at 2f4c358. Prod runs ee89c86 (main line). Review the whole release:
`git diff 866de58..2f4c358`.

## Scope (D-241)
R4.3 contains only:
- the WS-2 prose literal rules **2a** (notation variants) and **2d-iii**, behind the env switch `HLM_RESEARCH_PROSE_RULES`, whose code default is all off = main;
- the replay harness under `eval/r43/`;
- the small item: a "resolved" lesson must quote a verification line verbatim.

The cut rules 2b (extractive repair), 2d-i ("glued") and 2d-ii ("hyphen_order") must be ABSENT, or provably inert. The superseder pull-in (WS-1 item 1) is NOT in this release.

Evidence before the review: two blind readers agreed 21/22.
- 2a newly keeps 3 claims, all SUPPORTED.
- 2d-iii flips as audited.
- The assembly reports the deterministic replay check: with only 2a and 2d-iii on, nothing else differs from main.

## Threat model (written before the review)
Assets:
- the truthfulness of memory.ask answers (the literal validator is the last gate before text reaches the caller);
- project and device scope;
- spend accounting;
- the rollback path.

Adversary or failure sources:
- the writer LLM's output, which is untrusted text;
- memory content, which may contain look-alike notation;
- operator mistakes in llm.env;
- partial deploys.

Questions:
1. Can 2a or 2d-iii make the validator ACCEPT a claim whose literal is not supported by the cited excerpts? This would be a false keep, for example notation variants that equate different values (`v1.2` vs `v12`, `1-3` vs `13`, units, version ranges, hyphen/dot/space variants, TR/DE number formats).
2. Can they REJECT supported claims beyond the audited flips?
3. Is "env key absent or empty → exactly main" true byte for byte on the answer path? Is an unknown or invalid rule name a fail-closed startup error or a silent no-op?
4. Did the removal of 2b/2d-i/2d-ii leave dead branches, half-removed flags, or a changed prompt or schema? The prompt and schema sha must equal main.
5. Is the lesson "resolved → verbatim verification line" check correct, and can it reject valid resolved lessons?
6. Release safety:
   - there is no migration;
   - the install_llm_env and release-template flow carries the new key;
   - an R4.2/B3 env runs the R4.3 code as main;
   - rollback is env-only (rules off), or a redeploy of ee89c86.

## Severity rubric (fixed before the review)
- **HIGH:**
  - a reachable false keep, i.e. unsupported content reaches the caller as validated;
  - a scope or privacy leak;
  - broken spend accounting;
  - env-absent behaviour that differs from main;
  - a rollback that does not restore main behaviour.
  A HIGH finding NEEDS a concrete reproducer: input text plus excerpts, giving the wrong outcome.
- **MEDIUM:** extra rejections of supported claims beyond the audit; fail-open config handling; a test gap on a changed path.
- **LOW:** clarity, docs, naming, and harness-only issues.

## Output
- `## Verdict`: GO, GO-with-fixes or NO-GO.
- Numbered findings, each with severity, file:line, the failure scenario and a minimal fix. A HIGH finding also needs its reproducer.
- At most 70 lines. Round 2 is the last round; anything left over goes to the owner (D-125).

The archive root holds REVIEW.diff = `git diff 866de58..2f4c358` (no git history in the archive).
