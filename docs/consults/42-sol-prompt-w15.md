You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Review workstream W1.5 (hlm import/export, the item source contract, migration 0007_import, the skeleton card, ops status): branch `worktree-agent-ad7c9692cf72a8abd` (worktree /Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd), 5 commits on top of a77179a. Diff: `git -C <wt> diff a77179a HEAD`.
Spec: docs/decisions/PHASE2-4-ROADMAP.md W1.5 (G-I1..G-I4) and CC-1/CC-2; DECISIONS D-015, D-020, D-021, D-062, D-069; docs/research/03-legacy-memory-inventory-public.md §1–10. The implementer adopted a plain source_key column + CHECK, an EXCLUDE constraint for one current item per (project, source_key), a skeleton-card backfill after the DDL, imports as ordinary write events (the CC-2 `import` kind unused), and dates without a time read in the machine's zone (--tz). G-I4 scored 0.794 against 0.90 (misses attributed to ranking: TR↔EN, self-similar docs).
STATIC REVIEW ONLY (no tests, no edits).
Judge:
(1) The temporal rule: can mtime or commit date leak into valid_from or recorded_at? Is evidence-date parsing robust (false positives from random dates in text, e.g. a table of dates that are not validity dates)?
(2) Migration 0007 on a live DB: lock_timeout, idempotency, the backfill's cost with many projects, the EXCLUDE constraint vs concurrent imports (deadlocks, E_VERSION_CONFLICT races), the downgrade.
(3) Idempotency and revisions: an uuid5 request_id collision across projects? A changed file → revision under concurrency; a file split into sections → stable section keys when headings are edited?
(4) Export → import round trip: can a crafted exported file (frontmatter logical_id) revise items in another project or bypass authz?
(5) Imports as ordinary writes: consequences for the librarian enqueue priority (the roadmap wants imports at priority 6) and for replay.
(6) Does G-I4's failure point to an import defect rather than ranking? Look at the heading-split granularity and chunking.
OUTPUT (≤ 450 words): ## Verdict (MERGE / MERGE-WITH-FIXES / DO-NOT-MERGE); ## Findings (table, max 6: severity | file:line | issue | fix); ## G-I4 (2 lines).
