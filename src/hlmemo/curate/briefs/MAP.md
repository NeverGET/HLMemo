# Supersession curation, mapping pass: find candidate pairs

## Goal
HLMemo is a memory server. Project `{{PROJECT}}` holds the memory items in the export below: decisions, status docs, runbook sections, consults and notes. Many OLD items still state facts that are no longer true, for example "prod caps are 1/2/10", "the writer is model X", "prod runs R3" or "pending owner OK". An agent that reads such an item alone acts on stale information.

A `supersedes` link from the NEWER item (src) to the OLDER item (dst) fixes this. You only FIND candidate pairs. Later passes verify each pair, quote the exact spans, and try to refute it, and the owner approves the result. Precision still matters: list a pair only when you have read both items.

## Inputs (read-only)
- **Export:** `{{EXPORT_DIR}}`.
  - It holds one file per current item, in folders such as fact/, episode/, doc_chunk/, lesson/ and session_note/. `INDEX.md` lists every file with its title.
  - The frontmatter has `clue` ("vNNN"), `logical_id`, `version_id`, `kind`, `title`, `valid_from`, `source` (JSON; `source.path`) and, optionally, `links`. The BODY follows the second `---`.
  - A `links:` line lists the item's OUTGOING links. A pair that is already linked by `supersedes`, in either direction, needs no candidate.
  - Decision rows are items whose body starts `D-NNN | date | status | text`. Find them with `grep -l '^D-121 |' -r {{EXPORT_DIR}}`.
- **Your slice:** `{{SLICE_FILE}}`, JSON `{"files":[...]}`. These are the export files (relative paths) whose items you examine as the possibly OLDER side. Search the WHOLE export for the newer side.
{{REFERENCES}}
- Today is {{TODAY}}.

## What makes a candidate
- The older item holds a statement that is no longer true as a statement about the present.
- A NEWER, current item states the replacement clearly.
  - If the newer item you found is itself outdated, use the later one.
  - If no current item states the replacement, emit no pair.

### HISTORY POLICY (owner rule, D-244)
Flag ONLY text that reads as a PRESENT-tense state or an instruction an agent could act on today, for example "still OPEN", "Prod runs R3", "resume here", "pending owner OK", "PROPOSED", "not in prod yet" or "the writer is X". Dated findings, measurements, experiment results, review records and logs that are clearly framed as the past stay as true HISTORY. They are never stale, even when a later item changed the state they describe. Being older is not being stale, and a rule that is still valid is not stale.

## Do NOT propose
- Statements that are still true: design and safety rules that remain valid.
- A later refinement that does not contradict the older text.
- Research documents and consult proposals, unless they assert the current production configuration.

## Also report (no pair)
`file_fixes`: a stale statement inside a LIVING document (a status page, the project card, a runbook section that describes the current procedure, a backlog). Such text should be EDITED, not linked. Give `{path, stale_text (verbatim, ≤200 chars), current_fact, source_clue}`.

## Output contract
Write ONE JSON file to `{{OUTPUT_FILE}}`:
```
{"pairs":[{"newer_logical_id":int,"older_logical_id":int,"area":"short topic","why":"≤200 chars"}],
 "file_fixes":[...], "notes":"≤5 lines"}
```
The file is checked against a schema. An invalid file is sent back to you once.

Your final message is at most 6 lines: the pair count, the file_fixes count, and any doubt.

## Hard rules
- Read-only everywhere except your one output file.
- Use no network, no `hlm` CLI, no database, no git writes and no other agents.
- Do not invent ids. Every id comes from a file you opened.
- Never print secrets. If you ever see a key-like string, do not copy it.
