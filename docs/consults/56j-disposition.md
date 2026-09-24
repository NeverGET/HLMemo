# Consult 56 — implementer J's disposition of Sol's review of branch J

Review: `docs/consults/56-sol-review-j-judgement.md` (DO-NOT-MERGE, PROMOTION-READY no). Fixed in
new commits on the same branch:

1. **Scope-churn stranding (High) — fixed.** Three re-evaluation paths, one rule
   (`roles.releasable`: every CURRENT touched project assistant+ → one apply job per batch):
   (a) EVERY promotion re-evaluates EVERY pending answer (not only those touching the decided
   project); (b) a revision that changes an item's `project_ids` enqueues `release_pending`
   (`librarian_release:<event_id>`, recorded in the write event, no LLM) for the answers naming it;
   (c) a sweeper in the worker loop (every 300 s, above observer only; one `EXISTS` when nothing is
   pending) re-queues any eligible answer through one recorded `librarian` event. A stale action
   is superseded by the apply job with `reason: "stale"` in the recorded status change (conflicts:
   `conflict`, v1 closes: `legacy_close`). Test `test_sol56_scope_churn_never_strands_an_answer`
   (4 orders incl. the sweeper): always superseded with the reason, never pending; replay identical.
2. **Concurrent batch replay (High) — fixed.** A librarian job allocates its event id only after
   ALL its locks (items, the project batch lock, batch rows); `_batch_after_apply` reads the batch
   row `FOR UPDATE`; an owner batch decision locks the batch row before its first event id. Two
   writers of one batch therefore get ids in commit order and replay (event-id order) reproduces
   them. Test `test_sol56_concurrent_jobs_racing_on_one_batch_replay_identically`: a deterministic
   interleaving (the first-leased job is held before its batch step until the second commits a new
   batch; BATCH_MAX=1) — the rebuild used to hit the one-open-batch unique index; now identical.
3. **Partial supersession read side (High) — fixed.** Demotion only when the query matched the
   OUTDATED statement: the matched chunk contains the link's quoted span and the chunk statement(s)
   sharing the most query terms overlap it (no shared term: only if the span is most of the chunk).
   Applied on the fetched head (after `n_fetch`), one stable topological order for chains
   (smallest original rank first; cycles keep their order). G3 unchanged (no such links).
4. **One event per job (Medium) — fixed, one residual deviation.** Exactly ONE terminal event per
   job: completion or permanent failure, both under `uuid5("job:<key>")` (a job cannot record
   both). Systemic hand-backs (outage, breaker, budget, NotReady, LLM off) write the job row only.
   Residual: a back-off (attempt consumed) still writes one compact non-terminal `defer` event
   (≤ MAX_ATTEMPTS−1 = 4 per job) so a rebuild keeps the attempt count that bounds retries. The
   replay-identity dumps no longer compare the scheduling hints (`run_after`, `last_error`) of a
   job that is still QUEUED; this amends Sol 38 #6 (exact restoration of a handed-back job) and
   needs an orchestrator decision entry.
5. **Connection envelope (Medium) — fixed; measured.** `HLM_LIBRARIAN_DB_CONNECTIONS` (default 8):
   the ledger/reservation pool is one connection per job slot; a job holds at most one own
   connection at a time (opened lazily for apply/defer, never across provider calls); ONE shared
   lease renewer renews every in-flight lease in one statement; `_BoundedConnect` caps the
   worker's own connections at envelope − pool (a slot waited for > 60 s hands the job back);
   `check_connection_envelope` refuses `2·N + 2 > envelope` at start (N=3 fits 8). Test
   `test_sol56_connection_envelope` (peak ≤ 5 own connections at N=3). G-L3 at N=3 re-measured.
6. **Cross-project admin re-plan (Medium) — fixed.** The custom/legacy re-plan's capabilities
   cover every current subject project and the question's touched projects; a subject the
   answering device cannot read refuses the re-plan with a recorded reason (`replan_refused`); the
   batch-path re-plan refuses (audited) when the proposing capabilities do not cover a subject.
