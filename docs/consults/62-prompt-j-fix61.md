# Consult 62 — routine check (D-085, astra-low): the review-61 fixes on int-r3-j-fix

Your cwd is a clean export of 6cf1492 (no .git, no secrets). `FIX61.patch` = 3cdeb61..6cf1492 (commits c485713, ab4c59c, 6cf1492). The review-61 findings are in docs/consults/61-astra-review-j-int.md and 61-sol56-review-j-int.md. Read-only; do not modify files.

## Claims to verify
1. **HIGH, TTL race.**
   - Worker path: TTL check after the item locks → policy check for every question → TTL re-check with a fresh clock → staleness/rebase → event id. An expired question is marked `expired`.
   - `memory.answer`: the TTL is re-checked right after `policy_blocked`; an expired question is refused with `E_VERSION_CONFLICT {expired}` and nothing is recorded.
   - The implementer admits a residual: the batch row lock (worker) and the `_rule`/`_widen` writes (answer) still wait after the recheck and before the event id. Judge whether that residual matters. Could a question expire, or be expired by the sweeper, during those waits and still be applied?
2. **MEDIUM, out-of-span scan.** The span must occur exactly once (overlapping copies count) and must not cut a word in two. The rest of the chunk is scanned with no term cap. An ambiguous match means no demotion. D-087 (demote ≤ 6a96ba1) and the 3,000-case property test still hold.
3. **MEDIUM, replay mask.** Job rows are compared pairwise (`ReplayJobRow`). Only a live systemic hand-back against its replayed counterpart skips `run_after`/`last_error`, and that counterpart may carry the recorded back-off code. Check whether this could hide a real divergence.

## Output (≤ 20 lines)
- `## Verdict (MERGE | FIX-NEEDED)`
- Per claim: FIXED, PARTIAL or OPEN, with file:line.
- New findings, only if they are real defects: severity | file:line | trigger | fix.
