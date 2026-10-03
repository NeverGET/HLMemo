# Consult 96: B3 release review (D-085 critical, Astra low + Sol xhigh), round 1 of 2

Branch: worktree-agent-a7fcbdd45d58e5e3d (worktree root). Review `git diff main..HEAD` (4 commits on main 130c345).

## What it does
1. **D-118 write-time supersession, ported from an older branch.** memory.write takes `items[].updates[{item, old_span, mode: revise|supersede, replacement?}]`; `expected_version` is accepted but not advertised.
   - The writing agent revises or supersedes an existing memory in the same transaction as its new write.
   - Ack `updates[]` per update.
   - A minimal revise/reopen/unrevise apply path, `revert_write_update` plus `ops librarian revert-update`, HLM_LIBRARIAN_REVISE_KINDS, ADR_DIRS.
   - A writer close carries `keep_source` (the survivor keeps source and code_refs). Librarian closes are unchanged, so old events must replay byte-identically.
2. **Read side (D-207 #5).**
   - memory.raw gains `superseded_by[{logical_id, version_id, scope, valid_from, valid_to, quote?}]` (≤ 5).
   - memory.query hits gain `superseded: true` + `superseded_by[{clue, scope}]` (≤ 3), only when a live link exists.
   - Nothing is hidden or re-ranked (the D-057 hiding is unchanged). A superseder the caller cannot see is never named.
3. **The client brief** (src/hlmemo/brief) excludes on these fields.
4. **G-SURF** (the advertised tool-schema token budget, 3000): `$schema` was removed from every advertised input schema to fit; 2991/3000 now.

## Threat model and severity rubric (written before the review)
- **HIGH:**
  - a write can close, revise or supersede an item the caller cannot read or write (scope/grant/device-scope leak);
  - a wrong item is superseded (ambiguous old_span, stale expected_version, a race between concurrent writers);
  - replay of the event log diverges from live state (old events must replay byte-identically);
  - a revert cannot restore the pre-update state;
  - superseded_by leaks the existence or content of an item the caller cannot see.
- **MEDIUM:** a historical kind (episode/decision record) gets superseded instead of linked; part-scope links mis-marked; budget/packing regressions in memory.query or memory.ask from the larger hits; a schema change that breaks existing clients; G-SURF wording loss.
- **LOW:** docs and tests.

## Questions
1. Walk the write path: auth/grant checks for the target item, expected_version/old_span uniqueness, transaction boundaries, idempotency (request_id replay), concurrency (two writers updating the same item).
2. Replay determinism: do the new event types replay identically, and do old events replay byte-identically?
3. The revert path completeness.
4. Read-side leak checks for superseded_by on raw and query.
5. Client compatibility: are old clients unaffected? The schema change and the `$schema` removal.
6. Release safety: no migration claimed. Verify that no schema or table change is needed for the new event types; deploy and rollback onto prod fda8fa0.

Output: `## Verdict` (GO / GO-with-fixes / NO-GO), then numbered findings (severity, file:line, failure scenario, minimal fix). A HIGH needs a concrete reproducer. ≤ 80 lines.
