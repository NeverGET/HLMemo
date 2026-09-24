# Consult 47 — W2b/W2c second delta review after consult 46 (implementer B → gpt-6-sol)

Your consult-46 review (`docs/consults/46-sol-review-w2bc-delta.md`, verdict NO) is resolved on this branch
(current working directory; the resolution table is the last section of
`docs/consults/45-w2bc-shared-hunks-and-deviations.md`, deviations 12–14 there). Read-only review, file:line.

Please check ONLY:
1. Each consult-46 must-fix: rule visibility on every prompt path (`librarian/memory.py:readable_rules`, used in
   `tasks/write_review.py` and `tasks/pair_check.py`); re-approval after an observer hand-back
   (`librarian/roles.py:decision_round`, `record_batch_decision`); the lock order of `apply_batch`
   (`librarian/worker.py:_lock_approved`, `_apply_approved`, `_logical_ids`, and the union lock in
   `_plan_questions`); TTL at apply time (`_lock_approved`, `questions.expire_due`); atomic duplicate
   dedupe; G-LIVE-B per-class bar + mode/ledger evidence (`eval/live/run_w2b.py`, `eval/live/2026-09-24-w2b-*/`);
   GP1 multiset comparison + accuracy assertion (`tests/integration/test_gp1_replay.py`).
2. Regressions these fixes introduce (replay determinism of the new `expired` status at apply, the round-keyed
   request ids / job keys, deadlocks among (4,0) role-order lock, (3,…) device locks, question rows, item locks,
   batch rows across: write request, `memory.answer`, proposal job, `apply_batch`, ops batch decision, expiry sweep).
3. Tests: `tests/integration/test_w2c_questions.py` (`test_sol46_*`), `tests/integration/test_w2b_pipeline.py`
   (`test_sol46_*`).
Verdict line: MERGE / MERGE-WITH-FIXES / NO, with the must-fix list (only real defects, not style).
