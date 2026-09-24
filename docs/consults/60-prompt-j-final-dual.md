# Consult 60 — CRITICAL dual review (D-085): J final delta before merge

Your cwd is the worktree of branch `worktree-agent-ab3766c317d87ca93`. Review the delta `a23ac63..b243014` (commits 1b450cf, d57be9a, b243014). Read-only; do not modify files.

The previous dual review (docs/consults/57-astra-review-j-delta.md, 57-sol56-review-j-delta.md, available in the main checkout at /Users/cemalkurt/Projects/HLMemo/docs/consults/) said DO-NOT-MERGE and listed 5 items. The implementer's disposition is docs/consults/57j-disposition.md (in this worktree). Also read the D-086 and D-087 rows in /Users/cemalkurt/Projects/HLMemo/docs/decisions/DECISIONS.md.

## Implementer's claims (verify, don't trust)
1. **Signal-only replay race:** signal targets are now locked per item, in a consistent order, BEFORE the event id is allocated (`_lock_targets`). An interleaving test fails without the lock (live 2, replay 4) and passes with it.
2. **D-086 §1:** widen_scope is never picked up by promotion, release jobs or the sweeper. It stays `accepted_pending` and is listed as `awaiting=owner_apply`. Over 3 sweeper periods nothing is queued and no event is written, and the owner's answer then widens.
3. **D-086 §2:**
   - One terminal event per job, at most 4 back-off (defer) events.
   - A systemic hand-back writes only the job row.
   - The G6, jobs and W2b replay dumps skip `run_after`/`last_error` of a queued job.
4. **Read side (D-087):** the shipped rule is the 6a96ba1 sentence rule AND the review-57 clause rule over the same term set, so it can only demote LESS than 6a96ba1.
   - The example "API uses port 8080 and backups retain 30 days", with the quote "API uses port 8080" and the query "backups retain", is NOT demoted.
   - Links that close a cycle are ignored.
   - The a65a8f5 rule 3 (move behind the superseder wherever it ranks) is gone.
   - A frozen copy of the 6a96ba1 rule checks new ⇒ old over more than 1,000 generated cases.
5. **Gates:** lint clean; unit 546; integration 456 (the transient DB-outage failures pass on re-run); gate-release G3 0.980, G4 p95 262/270 ms, G-L3 p95 320/343 ms at N=3.

## Focus
- Are the review-57 HIGH items truly closed?
  - The replay race: can any other event-producing path still allocate its id before taking its locks?
  - widen_scope: is there any path at all that applies it without an owner answer?
- Does the D-086 §2 implementation lose information that replay/export needs? Is skipping `run_after`/`last_error` in the replay comparison safe, i.e. could a real divergence hide there?
- Read side: can the combined rule ever demote MORE than 6a96ba1? Does cycle handling drop a hit from the result or from the budget?
- **PROMOTION-READY:** is the D-077 prerequisite (no stranded accepted answers) now met for the whole branch?
- Any new deadlock, scope leak or D-074 observer mutation introduced by this delta.

## Output contract (≤ 35 lines, any language)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## PROMOTION-READY (yes|no)`, with one line.
- `## Per-item`: items 1–4, each FIXED / PARTIAL / OPEN with file:line.
- `## New findings`: a table with the columns severity | file:line | trigger | fix. Real defects only.
