# Consult 93 — routine review (D-085, astra low): writer 503 settlement fix

Context (HLMemo, provider-agnostic LLM layer; prod writer = Gemini 3.8 Flash medium via Google's OpenAI-compatible API):
- On 2026-10-01 Google returned HTTP 503 "This model is currently experiencing high demand" (status UNAVAILABLE) in ~0.5 s with no usage and no choices. Two prod memory.ask questions fell back to the luna writer and then abstained with abstain_reason=budget: D-062 rule (5) settles every 5xx at the attempt's worst case (~$0.07), the refine step adds another worst case, and the per-question cap HLM_RESEARCH_MAX_USD=$0.12 trips. The same phantom booking also counts toward the HOUR/DAY spend caps.
- Fix (branch fix-writer-unavailable, commit 4ca9688): a 503/529 whose body is the provider's own error object, with no usage and no choices, settles at $0 like a 4xx. Every other 5xx, a non-JSON/empty 503, and a 503 that reports usage still settle at worst case.

Review `git diff fda8fa0..HEAD` in this worktree (src/hlmemo/librarian/provider.py, tests). Answer:
1. Is the $0 settlement safe? Can a 503 with an error body ever correspond to a billed generation (OpenAI-compat or native Google, other providers behind the same code path, e.g. OpenRouter)? Is the matching tight enough (status, body shape, absence of usage/choices) to avoid under-counting?
2. Provider-agnostic (D-017): is anything Google-specific hard-coded outside a profile?
3. Should the writer, under attempt_policy="latency", retry once after a fast 503 (e.g. 1–2 s backoff, only when the 503 settled at $0) before falling back? Risks?
4. Any missing test.
Output: `## Verdict` (GO / GO-with-fixes / NO-GO), then numbered findings with severity (HIGH/MEDIUM/LOW), each with file:line and a minimal fix. ≤ 60 lines.
