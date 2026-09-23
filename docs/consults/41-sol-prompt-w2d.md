You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Review workstream W2d (`memory.risk_check` + `memory.register_lesson` + the preflight wrapper): branch `worktree-agent-a95de937e0b815dbd` (worktree /Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a95de937e0b815dbd), commits 132e1c9 and eb90d68 on top of a77179a. Diff: `git -C <wt> diff a77179a HEAD`.
Spec: docs/decisions/PHASE2-4-ROADMAP.md W2d (G-R1..G-R4, G-LIVE-C) and CC-4; DECISIONS D-014 (never says "no risk"), D-062 (privacy default-deny + race semantics, spend guard), D-063 (latency), D-066, D-067 (LLM robustness).
Results claimed: G-LIVE-C luna PASS (catch 1.0, false-warn ≤0.05); deepseek FAIL (false-warn 0.25). The implementer proposes deepseek as a labelled fallback judge (judge:"ok_fallback"); deterministic false-warn is 0.525.
STATIC REVIEW ONLY (no tests, no edits).
Judge:
(1) Privacy/authorization: can risk_check surface lessons from projects or scopes the caller cannot read (cross-project, hlm-global, device-scoped)? Is the privacy gate before EVERY judge attempt per D-062?
(2) Latency/availability: the read-only DB transaction stays open while the judge runs (≤4 s, 2 in flight). Can this exhaust the pool or block writes under load? What happens with 3+ concurrent risk_checks?
(3) D-014/D-067: can the output ever read as "no risk"? Are the citation guards real? Is calibration overfit to a self-authored fixture (VEC_MAX_DIST 0.16, TAU 0.045)?
(4) register_lesson: request_id idempotency, scope handling, and the hook contract.
(5) The fallback policy: do you agree with labelled deepseek vs degrading to deterministic? Why?
(6) Anything else blocking merge.
OUTPUT (≤ 450 words): ## Verdict (MERGE / MERGE-WITH-FIXES / DO-NOT-MERGE); ## Findings (table, max 6: severity | file:line | issue | fix); ## Fallback policy (2 lines).
