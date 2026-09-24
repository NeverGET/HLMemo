You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Review workstream W2e (low-confidence synthesis): branch `worktree-agent-a5e00c21c8c7b2440` (worktree /Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a5e00c21c8c7b2440), commits 6ce10da and e2f30d8 on top of ad7ccc2. Diff: `git -C <wt> diff ad7ccc2 HEAD`. Spec: docs/decisions/PHASE2-4-ROADMAP.md W2e; DECISIONS D-014, D-062 (privacy default-deny, no DB transaction held across an LLM call), D-067 (citation guards, abstention), D-071 (a fallback that fails a gate is disabled; this one PASSES G-LIVE-D and is labelled tier:fallback). Claimed: G-LIVE-D luna +16.1 pts (min over 3 reps), deepseek +19.4; cited ⊆ hits 100%; no-flag output byte-identical; stalled p95 6.2 s.
STATIC REVIEW ONLY (no tests, no edits). Judge:
(1) Can synthesis leak content the caller cannot read (the recheck after the LLM call, device-scoped items, cross-project)?
(2) Can a synthesized sentence state something not supported by its cited clue (the validator only checks citation presence)? How dangerous is that, and what would a cheap guard be?
(3) Budget/G2 exactness when sentences and hits are dropped; the no-flag byte identity.
(4) Availability: detach + 6 s cap + 4 in flight; what happens under concurrent synthesize calls on a 2 vCPU host with an 8-connection pool?
(5) Is τ_s (0.0434) calibration trustworthy (self-authored fixture)?
OUTPUT (≤ 400 words): ## Verdict (MERGE / MERGE-WITH-FIXES / DO-NOT-MERGE); ## Findings (table, max 6); ## Recommendation for the default (on/off) of the preflight wrapper.
