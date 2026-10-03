## Verdict

**NO-GO.** Round 1 found two release-blocking HIGH issues.

1. **HIGH — `src/hlmemo/librarian/roles.py:89,391`: promotion guard under-counts a concurrent batch approval.**  
   `record_role_decision` holds the exclusive role-order lock, but `record_batch_decision` never takes the shared lock. Reproducer: pause promotion after it counts zero; concurrently approve an open batch, which commits `approved` plus an `apply_batch` job; then commit promotion with `--release-pending 0`. The queued job applies the uncounted question.  
   **Minimal fix:** take the shared role-order lock before `batch_questions`; add a two-connection regression proving approval either precedes the count or waits until after promotion.

2. **HIGH — `alembic/versions/0011_question_withdrawn.py:60-78`: a refused downgrade can leave the staged OLD constraint active.**  
   The OLD `_v2` constraint commits before the refusal. Its cleanup still has the 3-second lock timeout. Test sketch: pause after staged ADD; another transaction holds `ACCESS SHARE`; resume downgrade with a withdrawn row. The guard refuses, cleanup DROP times out, Alembic remains at 0011, but `_v2` now rejects future withdrawals.  
   **Minimal fix:** make staged ADD, refusal guard, drop and rename one transaction so refusal rolls everything back; add the contended-refusal reproducer and assert `_v2` is absent.

3. **MEDIUM — `deploy/RUNBOOK.md:364-368`, `deploy/scripts/remote-deploy.sh:421-427`, `deploy/scripts/rollback.sh:161-178`: rollback loses the live D-242 edge network.**  
   Prod’s edge change is only a tracked edit and must be reverted before deploy. Both failure recovery and manual rollback render `ee89c86`’s committed Compose model, which lacks `edge`; Caddy returns to the shared IPv6 bucket.  
   **Minimal fix:** preserve the actual reviewed live Compose model as the rollback baseline, or establish an edge-containing committed baseline first; test both failed-deploy and manual rollback.

4. **MEDIUM — `deploy/RUNBOOK.md:371-372,606-618`: withdrawal instructions use stale “2 real” guidance.**  
   This review’s source of truth is 18 correct / 280 withdrawn. Following the example can terminally withdraw 16 correct questions.  
   **Minimal fix:** require exactly 18 reviewed keep IDs and exactly 280 withdrawal IDs, failing closed on either mismatch.

5. **MEDIUM — `deploy/RUNBOOK.md:580-583`, `REVIEW.diff:886-890`: “older code runs unchanged” is operationally false.**  
   Previous code expects Alembic head 0010; a database at 0011 makes its readiness check fail.  
   **Minimal fix:** say only the old apply logic ignores withdrawn rows; the supported old-release rollback requires restoring the pre-upgrade dump.

No defect found in withdrawn terminality, replay through unchanged `resolved.question_status`, project/operator checks, or `hlm.questions` owner/device visibility. A later dump rollback loses **all** post-snapshot DB changes—not only withdrawals. All 22 changed Python files compiled; integration tests could not run because `HLM_TEST_DSN` and the test dependencies were unavailable.