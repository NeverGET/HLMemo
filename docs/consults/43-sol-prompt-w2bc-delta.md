# Consult 43 — W2b/W2c delta review after consult 41 (implementer B → gpt-6-sol)

Your consult-41 review (`docs/consults/41-sol-review-w2bc.md`, verdict NO) is resolved in commits
78b25d5, 6d2c247 and later on this branch (current working directory). The resolution table is at the end of
`docs/consults/42-w2bc-shared-hunks-and-deviations.md`. Read-only review, be concrete (file:line).

Please check ONLY:
1. Each of your seven findings (#1–#7) and B/C1/C2: is the fix correct and complete? Point to any hole.
   Key code: `librarian/questions.py` (`_rule`, `_replan_job`, `answer` expiry + actor checks),
   `librarian/tasks/write_review.py` (`readable_rules`, owner note), `librarian/roles.py`
   (`batch_questions` expiry, `lock_role_order`), `librarian/actor.py` (`materialize(planned=…)`,
   `pending_question_exists`), `librarian/worker.py` (`apply`, `_apply_approved`, role_denied hand-back,
   duplicate proposals), `librarian/guards.py` (tie rule, no-answer drop), `prompts/relate_verify/v1.md`,
   `core/read_service.py` (`_add_librarian_block` after hits), `eval/live/run_w2b.py` (class bars, chain).
2. Regressions the fixes introduced (replay determinism, idempotency, lock ordering/deadlocks between the
   role-order advisory lock (4,0), the device-access lock (3,…) and the logical-id locks).
3. Evidence: `eval/live/2026-09-24-w2b-*/SUMMARY.md` (3 configurations, 3 reps each) and
   `tests/integration/test_gp1_replay.py` + `tests/fixtures/w2b/gp1_golden.json`.
Verdict line: MERGE / MERGE-WITH-FIXES / NO, with the must-fix list.
