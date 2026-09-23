## Verdict — DO-NOT-MERGE

Static review only; no tests or edits. I found no cross-project or device-scope read leak under the documented per-project read rule: `hlm-global` needs a grant, and `device:<id>` is visible only to that device. The privacy gate runs before each judge attempt, including retries and fallback. `register_lesson` preserves request-id conflict detection and passes `device_scope` through. The API returns `no_matching_evidence`, never “no risk”; the wrapper also says this is no safety guarantee.

## Findings

| Severity | File:line | Issue | Fix |
|---|---|---|---|
| **High** | [mcp_server.py:198](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/src/hlmemo/server/mcp_server.py:198) | The request transaction and device lock remain open through the judge call, contrary to D-062. Two calls can hold 2 of 8 pool connections for about 4 s and delay revocation; a third reaches `busy` only after acquiring a connection and retrieving candidates. | Release the transaction before judging; recheck authority and item visibility before returning the result. |
| **High** | [lesson_service.py:109](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/src/hlmemo/core/lesson_service.py:109) | The priority-2 cross-project check is unwired. A future hook that inserts a job after the write event also needs that job recorded in the event’s resolved payload for replay. | Wire the enqueue path and make its job replayable atomically. |
| **High** | [FALLBACK-POLICY.md:3](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/eval/live/2026-09-24-risk/FALLBACK-POLICY.md:3) | DeepSeek’s 0.25 false-warn rate fails the release-blocking G-LIVE-C limit of 0.10. Labelling the result does not satisfy the gate. | Disable it for `risk_judge` until a fallback passes G-LIVE-C. |
| **Medium** | [risk_judge.py:346](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/src/hlmemo/librarian/risk_judge.py:346) | The citation guard checks IDs, but dropping every invalid match can turn a model `warn` into a judged `no_matching_evidence`. | Treat an all-dropped warning as judge failure and use the deterministic result. |
| **Medium** | [preflight.py:291](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/src/hlmemo/cli/preflight.py:291) | `gather` waits for risk_check even after query succeeds; a failed risk call can delay launch until its 10 s client timeout. | Bound the optional risk wait separately, then emit the failure note. |
| **Medium** | [risk_service.py:67](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd/src/hlmemo/core/risk_service.py:67) | `0.16` and `0.045` were selected on a hand-written fixture with a held-out split from the same fixture. Its 0.525 deterministic false-warn rate limits what calibration proves. | Validate on an independently assembled corpus before treating the thresholds as general. |

## Fallback policy

I disagree with shipping labelled DeepSeek as a qualified judge while it fails G-LIVE-C.  
Use the specified `judged:false` deterministic degradation, clearly labelled as retrieval-only, until another fallback passes.