# Consult 76 — CRITICAL dual review (D-085, data integrity): write-time supersession (D-118), wf-write-updates @ 8b54800

Your cwd is a clean export of 8b54800. `D118.patch` is the diff from ed8deda (B-real) to 8b54800. `RULES.txt` holds D-083, D-095, D-110, D-113 and D-118. The design note and the design check are docs/consults/74-*. Read-only.

## What it does
`memory.write` items may carry `updates: [{item (the clue the agent saw, v<vid>[.<chunk>]), old_span, mode: revise|supersede, replacement?}]`, at most 8 per item.
- The new memory is ALWAYS written. Each update is applied, linked or rejected on its own, with code, reason and hint.
- **revise:** replace `old_span` (unique, word boundaries, not the whole item) with a replacement quoted verbatim from THIS item's body.
- **supersede:** close the old item and link it.
- **Historical records** (episodes, session notes, decision rows) are link-only whatever the config says, in both modes. This now also applies to the B-real librarian path.
- **Future-dated carrier:** it writes but does not mutate.
- **A↔B chains and double targets** in one batch are batch conflicts.
- **Owner action:** no question is asked. Mutations are tagged with the write's event id, and `ops librarian revert-update` issues a compensating event, refused while a later change depends on it.
- Lock order: item locks → policy → version → write → event id.
- G-SURF 2956/3000.

## Focus
- **Integrity:** can an accepted update corrupt validity intervals (overlap or gap), revise the wrong version, bypass the historical-kinds rule, or mutate outside the writer's capability or project (D-083 exclude)?
- **Atomicity:** can the new memory be written while part of an update is half-applied? Does replay reproduce exactly, including rejected updates?
- **Concurrency:** two writes updating the same target; a write racing a librarian B-real revision of the same item; a revert racing a new update.
- **Abuse by a writer:** mass supersede through many items; chaining across batches over time; a replacement that is a trivial substring ("a") which still passes "found in body"; an old_span that matches inside a larger word or across a line break.
- The revert path: dependency tracking and compensating correctness.

## Output contract (≤ 35 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Findings`: a table of severity | file:line | trigger | fix. Real defects only.
- `## Safe-to-enable`: yes or no, plus the reason.
