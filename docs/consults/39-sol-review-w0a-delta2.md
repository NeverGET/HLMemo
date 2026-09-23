## Verdict: DO-NOT-MERGE

W0a is **not yet safe to merge for a VM rehearsal before production**. Fix the rollback safety gaps, then rehearse the corrected flow on an isolated VM. This was a static review of `6ddd2c8..2862d39`; I ran no tests.

## H/M1/M2 status

| Finding | Status |
|---|---|
| **H** — manual rollback and env secrets | **PARTIAL** |
| **M1** — atomic markers | **PARTIAL** |
| **M2** — ops status during DB outage | **FIXED** |

The deploy lock blocks a concurrent rollback. `ops status` reads loopback `/ready` before its best-effort DB query and still reports readiness when the DB is unavailable.

## Remaining findings

| Severity | Finding |
|---|---|
| **Critical** | The pre-W0 guard accepts `HLM_REGISTRATION_MODE=closed` from a restored env file even with no registration secret ([rollback.sh:98](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/rollback.sh:98)). The pre-W0 `912f2d3` code ignores that mode; registration then opens. A backup containing another retired key satisfies the separate “backup exists” check. |
| **High** | After checkout to a pre-W0 ref, an interrupted rollback cannot be rerun: it copies helpers from the now-old checkout, where they do not exist ([rollback.sh:45](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/rollback.sh:45)). The “save current DB” dump is printed and subject to daily retention; the script never uses it to recover a failed rollback. |
| **High** | Neither rollback nor acceptance checks that `current_ref` matches the running release ([rollback.sh:58](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/rollback.sh:58)). A crash after new services start but before state publication can make rollback restore an older ref’s dump, or acceptance delete backups for a release no longer running. Later publication also drops earlier backup paths, leaving retired-secret files outside acceptance cleanup. |
| **Medium** | JSON replacement is atomic, but consuming the rollback pair and deleting legacy markers are separate operations ([release_state.py:120](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/release_state.py:120)). A crash leaves stale markers; `derive` does not remove them. |