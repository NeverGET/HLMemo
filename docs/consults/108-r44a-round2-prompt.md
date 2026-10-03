# Consult 108: R4.4a release review ROUND 2 of 2 (D-125 one-way door: data and migration; D-085 critical: Astra low + Sol xhigh)

Archive of `r4.4a` at b564163.
- FIXES.diff = `git diff 77e04c3..b564163`: the round-1 fixes.
- REVIEW.diff = `git diff 7cc7721..b564163`: the whole release.

Round 1 was consult 107 (both NO-GO). Same threat model and rubric as consult 107.

Round-1 findings:
- HIGH 1: the promotion guard under-counted a concurrent batch approval (no shared role lock in `record_batch_decision`).
- HIGH 2: a refused 0011 downgrade could leave the staged `_v2` CHECK.
- MEDIUM 3: rollback to ee89c86 loses the D-242 `edge` network.
- MEDIUM 4: stale "2 real" withdraw guidance.
- MEDIUM 5: a false "older code runs unchanged" claim.

Claimed fixes:
- HIGH 1: a shared lock in `record_batch_decision`. The builder found one more releasable path, a revision changing an item's projects without a role lock. The guard now counts by home and recorded projects and may only over-count. There is a two-connection race test.
- HIGH 2: the downgrade is one transaction together with the alembic version update; a contended-refusal test.
- MEDIUM 3: a rollback.sh WARNING plus a RUNBOOK re-apply step for edge.
- MEDIUM 4: a fail-closed script with EXPECT_PENDING=298/KEEP=18/WITHDRAW=280.
- MEDIUM 5: docs corrected.

Questions:
1. Is each round-1 finding CLOSED, PARTIAL or OPEN? Cite file:line.
2. Did the fixes introduce any regression? Look especially at lock ordering (shared role lock before question locks: any deadlock against promotion, apply or expire?), the guard's over-count rule, and the single-transaction downgrade.
3. Is anything else blocking GO?

**Output:** `## Verdict` (GO / GO-with-fixes / NO-GO); numbered findings with severity, file:line, the scenario and a minimal fix. A HIGH needs a reproducer. At most 40 lines. This is the last round: anything left goes to the owner.
