## Verdict

**FIX-NEEDED.** Static review of `git diff af7daa6 e0a785e` and D-072/D-073 only; no tests run.

## Per-finding status

1. **FIXED** — [0007_import.py:73](/Users/cemalkurt/Projects/HLMemo/alembic/versions/0007_import.py:73): the online index’s open-validity predicate matches the source-owner write check.
2. **FIXED** — [0007_import.py:67](/Users/cemalkurt/Projects/HLMemo/alembic/versions/0007_import.py:67): required source keys can no longer pass the CHECK as `UNKNOWN`.
3. **FIXED** — [common.py:46](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/common.py:46): date evidence is restricted to date-led headings and explicit record formats.
4. **FIXED** — [plan.py:75](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:75): request IDs include the expected version, covering the A→B→A cycle.
5. **PARTIAL** — [plan.py:225](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:225): cross-project re-import is stable, but a forged `origin` can still select a native item.
6. **PARTIAL** — [plan.py:273](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:273): renamed sections are re-mapped, but similarity alone can join unrelated sections.

## New findings

| Severity | File:line | Issue | Fix |
|---|---|---|---|
| High | [plan.py:225](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:225) | Forged `origin: target-project/victim-id` plus the current `version_id` revises an unrelated native item of the same kind. A version check proves freshness, not origin. | Require server-verifiable export identity; otherwise import as a new sourced item. |
| High | [0007_import.py:193](/Users/cemalkurt/Projects/HLMemo/alembic/versions/0007_import.py:193) | Pre-build duplicate open keys cause `23505`, leaving an INVALID index. Retry drops it, then fails on the same rows. | Detect duplicates, provide reconciliation, and clean up after non-lock failures. |
| Medium | [write_service.py:811](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:811) | `Item.close` ends version validity but leaves outgoing links open; they can reappear if the item is reopened. | Close matching link validity through infinity. |
| Medium | [plan.py:268](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:268) | Different heading-only sections score `1.0` and can inherit each other’s logical ID. | Reject empty-body matches and ambiguous candidates. |
| Medium | [plan.py:303](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:303) | The mass-close guard permits closing all items when five or fewer are in scope. | Enforce the share limit for small scopes too. |

Close authorization and event replay checks appear intact. `librarian_priority` is server-derived; a client-supplied field cannot jump the queue.