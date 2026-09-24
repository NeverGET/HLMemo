## Verdict (NO-GO)

D-074’s observer guarantee is still breakable. Static review only; no tests were run.

## Per-finding status

1. **Observer mutations — FAIL.** `memory.answer` checks every touched project under the role lock, but `apply_batch` checks only the batch’s home project. An A→B action can change B while B’s effective role is observer ([worker.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/worker.py:563)).
2. **`accepted_pending` → apply — FAIL.** The worker rechecks staleness, TTL and capabilities, and events record replayable jobs. Promotion can nevertheless strand pending answers; databases already at `0008` never receive the edited status CHECK ([roles.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/roles.py:124), [0008_librarian_tasks.py](/Users/cemalkurt/Projects/HLMemo/alembic/versions/0008_librarian_tasks.py:63)).
3. **`check_librarian` — PARTIAL.** API and librarian settings require enabled/live/observer, heartbeat requires enabled/observer, and judge errors fail. Heartbeat age is collected but never checked, so a stale heartbeat can pass ([check_librarian.py](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/check_librarian.py:103)).
4. **TTL fix — PASS.** `apply_batch` reads a fresh database clock after the item-lock wait and expires questions before materialization ([worker.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/worker.py:511)).

## Remaining blockers

Check every touched project in the worker; add a new migration for existing `0008` databases; ensure promotion jobs survive an observer worker consuming them and are queued when a touched project is promoted; reject stale heartbeats.