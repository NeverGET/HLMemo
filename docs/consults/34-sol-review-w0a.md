## Verdict: DO-NOT-MERGE

The HTTP access controls appear sound, but the **mandatory manual W0a cutover plan is not executable as written and its rollback can select the wrong image**. This was a static review only; I ran no tests, Docker commands, or SSH commands.

## Findings

| # | Severity | File:line | Trigger | Observed vs expected | Fix | Confidence |
|---|---|---|---|---|---|---|
| 1 | High | [RUNBOOK.md:499](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:499) | Follow the W0a command block | It stops writers, then runs literal `git fetch origin R`; `R` and `P` are never assigned. The block can leave the service stopped and can publish literal `R` as a release marker. | Assign and validate full revision values **before** stopping writers; use quoted variables throughout. | High |
| 2 | High | [RUNBOOK.md:511](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:511) | Roll back in the same shell | Exported `HLM_IMAGE=hlmemo:R` overrides the restored `prod.env` value. [restore.sh:49](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/backup/restore.sh:49) can migrate and restart using the **new** image after checkout of the old code. | Set or unset both image overrides for `P`; inspect rendered Compose configuration before restore. | High |
| 3 | Medium | [RUNBOOK.md:504](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:504) | A write commits between dump and writer shutdown | Rollback discards that write. A maintenance window alone does not quiesce clients. | Drain/stop writers before a final rollback dump, or verify a write-free interval. | High |
| 4 | Medium | [RUNBOOK.md:517](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:517) | Use the standard rollback after W0a | The plan updates `current-ref` but not the matching `previous-ref`/`previous-dump` read by the standard procedure. | Persist the old ref and its dump together at promotion. | High |
| 5 | Low | [middleware.py:591](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/middleware.py:591), [cursors.py:77](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/auth/cursors.py:77) | Present a cursor after bearer expiry | HTTP returns `E_AUTH` before cursor verification; expiry does not change cursor generation. The roadmap says `E_INVALID_CURSOR`. No HTTP read bypass results. | Align the gate’s expected error with authentication order, or change the cursor contract. | High |
| 6 | Low | [app.py:212](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/app.py:212) | Anonymous `/ready` request | The permitted public route returns migration IDs, model paths, and truncated DB errors, beyond readiness status. | Return status publicly and keep diagnostics operator-only. | High |

## Cutover risks

- W0a changes Compose, so [remote-deploy.sh:186](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/remote-deploy.sh:186) refuses the automatic path; the manual plan must be repaired before release.
- Migration from production’s `0001` runs the concurrent index work in `0003`/`0004`; failure can leave committed partial DDL. Restore the matching pre-upgrade dump before restarting old writers.
- Existing trusted g7 and any trusted gate devices retain their tokens and grants because `expires_at` is nullable. The runbook’s gate command omits `--g7`; inventory old devices, rotate g7, and revoke unwanted gate devices.
- Preserve verified deploy SSH access: device 1’s old HTTP token is disabled at startup, and ops over SSH becomes the admin path. Restore the retired secrets with the old image during rollback.

## Checked and sound

- [compose.prod.yaml:34](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/compose.prod.yaml:34) pins production, closed registration, and disabled admin HTTP above env-file values; [app.py:407](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/app.py:407) refuses unsafe production startup.
- [middleware.py:79](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/middleware.py:79) closes sensitive routes before body reads; I found no concrete encoding, slash, case, dot, HEAD, or OPTIONS bypass.
- [devices.py:247](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/devices.py:247) returns the same pre-lookup 404 for existing and absent nonself revoke IDs.
- [hlm_ops.sh:43](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/hlm_ops.sh:43) quotes arguments; [cli.py:125](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/ops/cli.py:125) emits tokens to stdout only after commit. I found no HTTP/MCP dispatcher into ops.
- [resolve.py:69](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/auth/resolve.py:69) independently rejects expiry under the transaction lock.