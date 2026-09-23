## Verdict

**DO-NOT-MERGE.** Static review of commits `0f5b408` and `19be389` against merge-base `1b2d729`. The privacy path and G-L3 gate need fixes before this workstream can satisfy the frozen contract.

## Findings

| # | Severity | File:line | Trigger | Observed vs expected | Fix | Confidence |
|---|---|---|---|---|---|---|
| 1 | High | [pair_check.py:76](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/tasks/pair_check.py:76) | Cross-project candidate or revoked device | Only the job project’s `policy.librarian` is checked; enqueue-time grants can still put content in a provider prompt. Device-scoped sending is also configurable. | Check every item’s policy, scope, device status and current grants **before** the call; default-deny. | High |
| 2 | High | [cassette.py:118](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/cassette.py:118), [worker.py:205](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/worker.py:205) | Provider echoes a secret | Record mode persists assistant content before validation or redaction; schema-error text can enter job errors and logs. | Redact recorded responses and use content-free error codes. | High |
| 3 | High | [test_gl3_llm_down.py:162](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/tests/integration/test_gl3_llm_down.py:162) | G-L3 latency gate | `19be389` finishes writes before timing queries, removing their overlap. The earlier 543 ms is a failure signal, though this in-process measurement alone does not prove production latency. | Move synchronous chunking off the API event loop; time three query callers **during** 100 acknowledged writes with the librarian stalled/503, preferably through the real API and separate worker. | High |
| 4 | High | [compose.prod.yaml:70](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/deploy/compose.prod.yaml:70), [remote-deploy.sh:228](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/deploy/scripts/remote-deploy.sh:228) | R2 merge/deploy | Migration still targets `phase0@head`; after CC-1 relinks `0006` behind `0005`, it must target `main@head`. The deploy script also omits `librarian` from stop/start/rollback and rejects the Compose change. | Complete the staged rollout, migration target, readiness check and service lifecycle together. | High |
| 5 | High | [actor.py:93](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/actor.py:93) | Item revised after planning or approval | Apply resolves current logical heads without comparing the versions the model assessed; a stale link can attach to changed content. | Lock both heads and compare planned version IDs; supersede stale proposals. | High |
| 6 | Medium | [worker.py:267](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/worker.py:267) | G-L8/CC-5 contract | Proposals are embedded in librarian events, with no question rows; `llm/1` uses a `calls` array instead of CC-5’s specified fields. The addendum calls D-062 *proposed*. | Implement the frozen shapes or approve a replacement decision and gates. | High |
| 7 | Medium | [provider.py:419](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/provider.py:419), [provider.py:506](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/provider.py:506) | Error or re-enqueue loop | HTTP/transport errors settle at $0 despite uncertain billing. The 20-call ceiling counts completed rows per job ID, so a new-ID loop bypasses it; only the hourly cap stops that test. | Charge uncertain outcomes conservatively and count durable attempts by loop lineage. | High |
| 8 | Medium | [actor.py:185](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/actor.py:185) | G-L5 replay | Replay sets `jobs.done_at` to event time; live completion uses `now()`. The equality helper omits that column. | Record completion time in `resolved` and compare the full job projection. | High |

## Deviations verdict

- **Pair-check scaffold:** Conditional for W2a; it does not establish the later W2b flow.
- **Annotate links only:** Conditional foundation scope; other CC-3 mutations remain unproven.
- **Proposals in events:** Reject as fulfillment of G-L8/§4b.
- **Role decisions as `librarian`/`set_role`:** Requires a decision resolving §4b’s specified `librarian_role` event.
- **HTTP $0; timeout worst-case:** Reject HTTP accounting; accept timeout accounting.
- **No fallback after two schema failures:** Accept; G-L1 explicitly expects `schema_fail`.
- **`llm_calls.mode` and three event indexes:** Mode is reasonable; justify the extra indexes and migration downtime before live use.
- **G-L3 opt-in:** Accept only with an enforced release invocation; the current sequential result cannot pass the gate.

## Checked and sound

- Hour → day → month reservation locking and crash sweeping are transactionally structured; the default profile states peak prices.
- UUID5 job-event deduplication, lease fencing and the database’s null-safe uniqueness support exactly one applied event.
- Observer mode blocks direct link mutation in the inspected path.
- Provider requests redact the user message; validated audit output is redacted, and the ledger stores hashes and metadata.
- The new service has 0.5 CPU/512 MiB limits. Production memory and latency still require G-MEM and a valid G-L3 run. No tests or Docker commands were run.