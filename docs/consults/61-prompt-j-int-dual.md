# Consult 61 — CRITICAL dual review (D-085): J review-60 fixes + integration with main (int-r3-j @ 3cdeb61)

Your cwd is a CLEAN EXPORT of 3cdeb61 (no .git, no secrets). Stay inside it and do not modify files.
The files to review:
- `REVIEW60-FIXES.patch`: b243014..d3282ab, the fixes for review 60. COMMITS.txt lists the commits.
- `MERGE-RESOLUTION.patch`: the combined diff (`git show --cc`) of merge 206f21b (main 625d72e into J b243014), restricted to the two files that conflicted, `src/hlmemo/db/librarian_queries.py` and `src/hlmemo/librarian/worker.py`.
The previous reviews are docs/consults/60-astra-review-j-final.md and 60-sol56-review-j-final.md. Read the rows D-083, D-084, D-086 and D-087 in docs/decisions/DECISIONS.md.

## Implementer's claims (verify, don't trust)
1. **Legacy `proposal.mutation`:** the widen exclusion moved from SQL into `proposal_actions()`. Legacy non-widen answers are picked by promotion, release and the sweeper; legacy widen answers are never queued. No other `proposal->'actions'` SQL filter exists.
2. **Cycles:** edges inside a cyclic SCC are ignored and the original order is kept. There is a tight-budget test.
3. **Out-of-span terms:** a query term that also appears outside the quoted span blocks demotion.
4. **D-087 ordering:** rank_new(h) ≤ max(rank_baseline(h), rank_6a96ba1(h)) holds by construction. A property test over 3,000 cases runs against a frozen 6a96ba1.
5. **Replay-dump mask:** only systemic hand-backs (and their replayed NULL counterpart) are masked; consumed back-offs are compared raw. Residual: a back-off followed by a hand-back would be flagged.
6. **Merge resolution:**
   - `librarian_queries.py`: main's D-083 policy helpers (FOR SHARE) are kept, followed by J's `supersession_among`.
   - `worker._apply_approved`: main's `policy_blocked` recheck runs first, then J's staleness check and in-batch rebase. Both run AFTER the item locks and BEFORE event-id allocation.
   - The D-084 `provider.py` comes from main only.
7. **Gates on 3cdeb61:** lint; unit 575; integration 494/0 failed; the Q exclude tests (15) and fallback-budget tests (15) pass; gate-release G3 0.980, G4 p95 256 ms, G-L3 386/377 ms at N=3.

## Focus
- Is each of claims 1–5 truly closed? For claim 4, try to construct a counterexample.
- **Merge resolution:**
  - Did either side's semantics get lost? D-083 exclude rechecks at plan and apply time, the D-083 FOR SHARE ordering, the D-084 latency attempt policy / per-model breakers, and J's locks-before-event-id, D-086 events and N=3 envelope.
  - Is there any path where the policy recheck runs after the event id, or outside the locks?
  - Does any auto-merged file (candidates, write_review, questions, fixtures) silently combine incompatibly?
- **PROMOTION-READY:** is the D-077 prerequisite met for the whole tree?
- Any new deadlock, scope leak, D-074 observer mutation or replay divergence.

## Output contract (≤ 35 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## PROMOTION-READY (yes|no)`, with one line.
- `## Per-claim`: claims 1–6, each FIXED/OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table with the columns severity | file:line | trigger | fix. Real defects only.
