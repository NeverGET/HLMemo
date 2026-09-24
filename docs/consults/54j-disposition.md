# Consult 54j — implementer J's disposition of Sol's review

Position: `54j-sol-prompt-librarian-judgement.md` (written before reading Sol). Review:
`54j-sol-review-librarian-judgement.md` (NO-GO as written). What changed because of it:

1. **Running apply jobs (accepted).** A promotion now skips a batch only while an apply job of it is
   still QUEUED (it has not planned yet). A RUNNING one may hold a snapshot from before the answer,
   so the promotion enqueues its own job; two apply jobs of one batch serialize on the question row
   locks. Test `test_sol54j_a_running_apply_job_does_not_absorb_a_release`.
2. **Fact-level supersession (mostly accepted).** Automatic closes are limited to ONE-statement
   items (`auto_ok` needs `single_statement`); a multi-statement whole-item close is always an
   owner question. Stored v1 questions carrying a close without the v2 evidence (`close_ok`) are
   never applied: the batch path and `memory.answer` supersede them and re-plan their subjects
   (`legacy_close`). The read-side concern (a partial link that does not hide keeps stale-first at
   8/15) is answered with rule 3 of `core/supersession.py`: a scoped (`part`) supersedes link does
   not hide the item but ranks it right after the item that replaced its statement. Claim-level
   versions / span-aware retrieval are out of scope here (the `split_and_supersede` resolution is
   the owner-facing path to single-fact items).
3. **Action tier (accepted in part).** The verifier now separates duplicate from refinement
   (`relate_verify/v2.adds_detail`: `none` confirms a duplicate/restatement, the refiner's letter
   confirms a refinement). Sol read the hold-out "duplicate 0/6" as recall; it is precision (0 of 6
   proposed duplicates were right), which is what the near-identical rule targets. Semantic
   duplicates are kept as `relates` links and — like every judgement a guard had to correct (a
   non-identical duplicate, a flipped refine direction) — capped at `question`. High-impact
   disagreement keeps the D-067 rule (no proposal / downgrade), unchanged.
4. **Refine direction (kept, bounded).** The brief requires valid_from/recorded_at; time is used
   only when specificity is undecided and then never yields an action (capped at `question`,
   flag `refine_direction_by_time`). The verified refiner orients the link (refiner → refined).
5. **doc_chunk (accepted in part).** Chunks get a RESERVED same-project list (top 3) so they never
   displace fact/lesson candidates; chunk subjects need dated/decision content (the brief's rule)
   or an explicit evidence date; pairs with a chunk raise contradictions only.
6. **Concurrency (accepted).** The batch lock key is `(5, hashtext('librarian_batch:<id>'))` (bigint
   safe), held to commit (covers open_batch through the insert); only `_assign_batches` creates an
   open batch. Shutdown awaits every in-flight job before the provider/pool close. The breaker's
   half-open trial is synchronous under asyncio (no await between check and set). The 2-vCPU VM
   measurement is left to the orchestrator (local G-L3 runs with `HLM_LIBRARIAN_CONCURRENCY=3`).
7. **Approve-all chains (accepted).** Dependency order + per-question TTL/staleness/authority
   recheck; directed supersedes edges tracked (inverse → re-plan); `already_satisfied` recorded;
   re-plans only for subjects not closed by the batch. Links to an item closed earlier in the same
   event are allowed: a closed item keeps a current (survivor) row, as for any close.
8. **Gates (accepted).** `relations.json` gold is frozen; its semantic duplicates are scored with
   the pipeline's `relates` (reported as such). A separate v2 fixture (`relations_v2.json`) carries
   strict near-identical duplicates, `relates`, refines with a gold refiner, partial and whole
   supersessions and doc_chunk pairs; both definitions are reported. The hold-out
   (G-E-TEMP/G-E-W2b, blind labels) stays with the orchestrator.
