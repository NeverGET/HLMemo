# 132: a read retry in the importer and a Claude second grader: review record

Tools-only change, one fresh-context review agent (Sonnet), read-only (D-266).

## The change
1. `importers/runner.py`: the paged, read-only manifest read (`hlm.export`) is retried after 2 s and 5 s when it
   fails with a retryable `E_UNAVAILABLE`. Writes are not retried. Background: one page answered 503 at the start
   of a batch during a 1,754-item import (the API's connection was closed by `idle_in_transaction_session_timeout`
   under load); nothing had been written and the identical batch succeeded a minute later.
2. `tools/migrate/blindcheck/run_graders.sh`: grader 2 was `codex exec`; it is a second `claude -p` run with
   grader 1's isolation flags on another model (`GRADER2_MODEL`, default `opus`). The diff came from the machine
   that ran a real 30-packet blind check with it.

## Result: nothing above LOW
Found sound: only the read is wrapped; a retried page resends its signed cursor, which carries the snapshot
(`export_service.py`), so pages never mix snapshots; three attempts at most; the notice goes to stderr; grader 2's
flags equal grader 1's token for token, each grader has its own directory, a dead or silent grader still fails
the script; no reference to the old grader is left.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | LOW | No test of a retried later page and of the stderr notice | **Added** (two pages, the same cursor twice) |
| 2 | LOW | The retry reuses the MCP session; not verified that a session survives a 503 | Left: if it does not, the retry fails again and raises as before |
| 3 | LOW | Two models of one vendor are less independent than two vendors | Recorded in the backlog; a split still counts as the stricter grade |
| 4 | LOW | Other reads (`hlm review`, `hlm project list`) have no retry | Left; they are interactive |

Tests: unit 1945 passed; `tests/integration/test_w15_import.py` 14 passed.
