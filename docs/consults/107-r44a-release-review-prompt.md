# Consult 107: R4.4a release review, ROUND 1 of 2 (D-125 one-way door: data and migration; D-085 critical: Astra low + Sol xhigh)

You review a clean archive of branch `r4.4a` at 77e04c3. REVIEW.diff = `git diff 7cc7721..77e04c3` (7cc7721 = main, which prod's next deploy will contain). Prod runs ee89c86 plus the edge-network compose change (D-242).

## Scope (D-244)
- `python -m hlmemo.ops librarian withdraw`: moves open, approved and accepted_pending questions of ONE project to the new terminal status `withdrawn`. It uses one evented librarian system event; the status records go in `resolved.question_status`.
  - It refuses foreign or unknown ids, already-applied questions, and questions inside a running apply.
  - It runs under `FOR UPDATE` row locks.
- Migration 0011 swaps a staged CHECK constraint (same pattern as 0010) and adds `withdrawn`. The downgrade refuses while withdrawn rows exist.
- The promotion guard in `record_role_decision`: `role set assistant|autonomous` refuses while the decision would release eligible approved or accepted_pending questions, unless `--release-pending N` equals the exact per-scope count. `--dry-run` shows the counts.
- `hlm.questions` (owner-only) also returns `verifier_kind`; the `hlm review` card shows the librarian's own doubts.

Context: prod has 298 `accepted_pending` questions in project hlmemo. Independent verification found only 18 of them correct, so 280 will be withdrawn. Today a role promotion would mass-apply all 298.

## Threat model (written before the review)
Assets: the integrity of the librarian questions and links; replay determinism (events → state); the observer guarantee (nothing applied in observer role); the owner-only access rules; migration safety on a live DB.

Questions:
1. Can a withdrawn question EVER be applied later? Consider the sweeper, `release_pending`, a role promotion, `apply_batch` already running, retries, and replay.
2. Does replay of the withdraw event reproduce the exact state under the current and the previous replay code? `resolved.question_status` vs mutations.
3. Can withdraw touch another project's questions, or run without operator authority?
4. Concurrency: a withdraw racing an apply, an expire or a promotion. Any lost update or partially applied batch?
5. Guard: does the count equal exactly what the apply path would release? Check expiry, widen_scope, per-project overrides, deployment-wide vs `--project` scope, and autonomous auto-class proposals. Can the guard be bypassed by any other caller of the promotion code?
6. Migration 0011: is the CHECK swap safe on a live table, idempotent, and do its upgrade and downgrade paths both work? Does the downgrade refusal leave the schema consistent? Is the release gate (alembic head 0011) wired into the deploy scripts and tests?
7. `hlm.questions` and `verifier_kind`: are the owner-token rules unchanged, and is there no new data exposure beyond the device's grants?
8. Release safety: there is a migration, so rollback = a pre-upgrade dump restore. What does it lose? Is env or compose affected? The compose edge network must stay.

## Severity rubric (fixed before the review)
- **HIGH:** a withdrawn question can be applied; a replay mismatch; cross-project or unauthorized mutation; a guard that under-counts or can be bypassed; a migration that can fail or corrupt on live data; an access-rule regression. A HIGH NEEDS a concrete reproducer: steps or a test sketch.
- **MEDIUM:** an operator foot-gun, a missing refusal, a test gap on a changed path, or a misleading docs command.
- **LOW:** clarity.

## Output
`## Verdict` (GO / GO-with-fixes / NO-GO); numbered findings with severity, file:line, the failure scenario and a minimal fix; at most 70 lines. Round 2 is the last round; anything left over goes to the owner (D-125).
