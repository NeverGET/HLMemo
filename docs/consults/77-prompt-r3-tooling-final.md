# Consult 77 — CRITICAL dual review (D-085), RELEASE-GATING for R3: the review-75 fixes on r3-tooling @ f9f5037

Your cwd is a clean export of f9f5037.
- `FIX75.patch`: 3b2ce64..f9f5037.
- `FULL-VS-MAIN.patch`: everything R3 adds (deploy/ and tests/deploy only).
- `RULES.txt`: D-108/D-119/D-121.
- Review 75: docs/consults/75-*review*.md.
Read-only.

## Claims (verify; try to break each with a crash/interrupt/race at a specific step)
1. **Snapshot provenance:** the disk env's non-secret fingerprint must match what BOTH api and librarian were created with before any env snapshot (deploy) or newer-env copy (rollback); otherwise STOP.
2. **Same-ref rerun:** it only verifies. The R2 pair and its env snapshot are kept, and publish refuses to pair a release with itself.
3. **install_llm_env.sh:** it takes the deploy lock and runs one journalled step: write env → recreate both services → `check --release r3`. deploy/rollback refuse while it is unfinished, and a rerun completes it.
4. **Rollback DB restore:** the first safety dump and the destructive-phase start are journalled BEFORE the DB changes, and a retry reuses that dump.
5. **Accept:** `--accept-release` refuses while a rollback is in progress and always requires running == current.
6. **Deploy-attempt journal:** it is written before migration and the new stack. A rerun completes the publish, or recovers with the recorded tuple.
7. **Manifest:** exact D-094 values; api == librarian on every key; disk == running.
8. **Interim:** only an UNLABELED env counts as the D-108 interim.
9. **Cleanup:** snapshot/journal copies are recorded as pending-deletion in the same atomic write, and the next lock holder deletes them idempotently.
10. **D-121:**
    - template caps are MONTH=10 / DAY=2 / HOUR=1 with the guard on;
    - the manifest requires every cap present, DISABLED=false, month ≤ 10 and day/hour ≤ month;
    - install PRESERVES the operator-set caps and key unless `--reset-operator-values`.

## Focus
- For every script and step, enumerate the crash points and check that each converges on a rerun.
- Look for new deadlocks from the lock added to install_llm_env (for example, deploy waiting on install while install waits on deploy).
- Secret handling in journals, fingerprints and snapshots: nothing secret logged or fingerprinted in a reversible way; permissions 0600; symlink-safe.
- Check that preserving the operator key and budgets cannot mask a wrong key or a disabled guard.
- Check that the D-108 order and the rollback drill in docs/status/R3-REHEARSAL.md still match the scripts.

## Output contract (≤ 30 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Per-claim`: items 1–10, each OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table of severity | file:line | trigger | fix. Real defects only.
- `## Release-safe`: yes or no, plus the reason.
