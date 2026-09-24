# Dual review 60 — implementer J's disposition

Reviews: `60-astra-review-j-final.md` (FIX-NEEDED), `60-sol56-review-j-final.md` (DO-NOT-MERGE).

1. **Legacy `proposal.mutation` answers stranded (High, both) — fixed.** The widen exclusion moved
   out of SQL (`NOT (proposal->'actions' @> …)` was NULL for a W2a row) into Python over
   `proposal_actions()` (both stored formats); SQL keeps only `kind <> 'widen_scope'`. No other
   `proposal->'actions'` filter exists (grep). Test
   `test_review60_legacy_mutation_answers_are_released_widens_never[promotion|release|sweeper]`:
   the legacy non-widen answer is picked on every path; the legacy widens (kind widen_scope, and
   a widen mutation under another kind) are never queued over three sweeper periods. Fails on the
   old filter (3/3), passes now.
2. **Cycles (Sol) — fixed.** Every ordering edge inside a cyclic strongly connected component is
   ignored, so a cycle keeps its original interleaving. Test
   `test_review60_cycle_never_loses_a_hit_under_a_tight_budget` ([3, 9, 1] stays [3, 9, 1];
   6a96ba1 gave [9, 3, 1]; hit 3 stays in a two-hit budget).
3. **Same term in the valid clause (Astra) — fixed.** A query term that also occurs in the chunk
   OUTSIDE the quoted span means no demotion ("port" in "API uses port 8080 and backups use port
   9090" → not demoted; "API 8080" → demoted).
4. **D-087 whole-list guarantee — fixed by construction.** Each hit gets the deadline
   max(baseline rank, 6a96ba1 rank); Lawler's backward rule (place last, among the hits whose
   constraints allow it, the latest deadline; ties: the later baseline rank) meets every deadline
   because the 6a96ba1 order itself is feasible (the new constraints are a subset of its
   constraints; constraints between two of its cycle members are dropped). Property test
   `test_review60_no_hit_ranks_worse_than_baseline_or_6a96ba1`: 3,000 generated heads against a
   frozen copy of the whole 6a96ba1 procedure, rank_new ≤ max(rank_baseline, rank_6a96ba1) for
   every hit. (Kosaraju SCC bug found by the property/unit tests and fixed before commit.)
5. **Replay-dump mask (Sol) — fixed.** `worker.SYSTEMIC_HANDBACK_CODES` names the hand-back codes;
   the three dumps mask `run_after`/`last_error` only for queued jobs whose `last_error` is a
   hand-back code or NULL (the replayed counterpart of a hand-back); a queued job after a consumed
   back-off (job-specific code, event-recorded) is compared raw — asserted in
   `test_one_terminal_event_per_job_and_compact_backoffs`. Residual (documented in the dump): a
   back-off followed by a hand-back diverges by design (D-086 §2) and would be flagged.
