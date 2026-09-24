# Dual review 57 — implementer J's disposition

Reviews: `57-astra-review-j-delta.md`, `57-sol56-review-j-delta.md` (both DO-NOT-MERGE); D-086.

1. **Signal-only replay race (High, both) — fixed.** `_lock_targets` locks every item a plan
   writes (placement-signal subjects and proposal subjects) with the write path's per-item lock,
   once, sorted, BEFORE the event id is allocated; a job that writes the same version waits and
   commits after it, so replay order = commit order. Test
   `test_review57_signal_only_jobs_on_one_version_replay_identically` (deterministic: the first
   job is held between its event id and its commit) fails without the lock (live 2, replay 4) and
   passes with it.
2. **Widen requeue loop (both) — fixed per D-086 §1.** Promotions, release jobs and the sweeper
   never select a `widen_scope` answer; it stays `accepted_pending`, listed by
   `ops librarian questions list` with `awaiting: owner_apply`, until an explicit `memory.answer`
   by a writer on every touched project applies it. Test: three sweeper periods queue nothing and
   write no event; the owner's answer then widens.
3. **Job events (D-086 §2) — as decided.** One terminal event (done|failed, `uuid5("job:<key>")`),
   ≤ 4 compact back-off `defer` events, systemic hand-backs job-row only; the replay comparisons
   (G6 `dump_projections`, the full jobs dump, `dump_w2b`) skip `run_after`/`last_error` of a
   still-queued librarian job, documented in each. Tests: repeated hand-backs write no event and the
   completion is the single terminal event; a permanently failing job has 4 back-offs + 1 terminal.
4. **Same-sentence false demotion (Medium) — fixed.** A hit is demoted only when ALL the query's
   evidence in the matched chunk lies inside the quoted span (clause level); a mixed match is not
   demoted. The evidence is the query's discriminative terms (D-055 DF filter; 3+ characters or
   identifiers). Example "API uses port 8080 and backups retain 30 days" / quote "API uses port
   8080" / query "backups retain" → not demoted (unit test).
5. **Cycles (Medium) — fixed.** Edges are taken in a deterministic order and an edge that would
   close a cycle is ignored, so the order is always a DAG and cycle members never drop below
   unrelated hits (unit test).
