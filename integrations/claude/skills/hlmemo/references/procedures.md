# HLMemo procedures: catch-up and migration

Moved out of `../SKILL.md` (sections 5 and 6) so the skill stays under the 24 KB that some clients load. Read this file when the owner asks for the catch-up or a migration.

## 5. Catch-up (protocol §4b)

The owner starts it in the project's own chat ("do the HLMemo catch-up"). The goal: the project's memory matches
the project's current state. You write only into this project's slug, under R1–R21.

1. **Read memory.** The brief (card, lessons, pending counts), then `memory_query` the main areas: overview,
   architecture, deploy, current work, open problems, lessons. Drill the top hits; list what memory calls current.
2. **Read the project**, without changing it: README and docs, CLAUDE.md, `git log` since the newest memory
   item's date, open TODOs, and this folder's auto-memory files (`~/.claude/projects/<dir>/memory`) if they exist.
3. **Diff.** Three lists: (a) present-tense memory claims that are no longer true; (b) durable facts and
   decisions missing from memory; (c) lessons the project learned that memory lacks.
4. **Correct the stale** (R11, R12). For each (a), write the current fact with `updates` against the clue you
   read and a verbatim `old_span`: `revise` for one changed statement, `supersede` for a wholly outdated item.
5. **Add what is missing** (R5, R6, R8): one claim per item, with evidence and dates from the source. Lessons
   via `register_lesson`, project scope only; cross-project ones become "Promotion candidates" in the session note.
6. **Fix or create the card** (R17). If a card line is now false, make a minimal `card_update`. With no real card
   yet (the brief has no "Now" section; `memory_query` returns the skeleton card), write the initial card in
   `call_the_day` `card_update`: present-tense lines only (what the project is, its stack, production state,
   current work and conventions), ≤ 420 tokens, every line backed by an item or file you read, with
   `expected_version_id` = the skeleton's version (Example D).
7. **Close** with `call_the_day`: the counts (corrected, added, lessons), the clues written, and `## Open` for
   anything you could not decide.
8. **Report to the owner**, then stop. The operator checks the result in the next library session.

At most about 40 writes per catch-up session; if more are needed, close and continue in a new session. Legacy
files (old memory stores, notes from other tools) are migration material, not catch-up material. The report:
```
HLMemo catch-up: project <slug>, session note v<id>
Corrected: <n> stale claims (applied <a>, linked <l>, rejected <r>): v.., v..
Added: <n> facts/episodes: v.., v..   Lessons: <n>: v..
Card: unchanged | updated (v<old> -> v<new>)
Promotion candidates: <n> (listed in the note)
Left for a next session: <what the write limit cut, or "nothing">
Questions for you:
1. <a decision only the owner can make, with the clues involved>
```

## 6. Migration (protocol §4)

Migrating a project's legacy memory (auto-memory, serena, `CLAUDE.md`, NotebookLM, repository notes) into its **own
slug** has its own skill: load **`hlm-migrate`** and follow it. It follows `docs/migration/PLAYBOOK.md` in the HLMemo
repository: owner decisions and the tier first, curated and dated items (facts hold only the present), a secret gate,
a local rehearsal, a sealed review package, then production batches after the owner's OK, with the operator opening
and closing the production side. Legacy files are migration material, not catch-up material (§5).
