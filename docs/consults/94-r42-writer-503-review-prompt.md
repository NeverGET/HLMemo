# Consult 94 — R4.2 release review (D-085 critical: Astra low + Sol xhigh), round 2 of 2 for the writer-503 settlement fix

Branch fix-writer-unavailable (worktree root), commits 4ca9688 + 79ade5c on top of main 0986678. Review `git diff 0986678..HEAD`.

Background: Google's OpenAI-compatible API returned HTTP 503 `[{"error":{"code":503,"message":"This model is currently experiencing high demand…","status":"UNAVAILABLE"}}]` (no usage, no choices) in ~0.5 s. D-062 (5) settles every 5xx at the attempt's worst case, so memory.ask booked ~$0.07 of phantom spend per refusal, tripped the $0.12 per-question cap after the luna fallback + refine, and abstained with abstain_reason=budget; the phantom spend also counts toward HOUR/DAY/MONTH caps. Round 1 (consult 93, Astra) = GO-with-fixes: make $0 an opt-in per-profile policy (default off), validate the exact declared envelope, worst case on any usage/output evidence, keep it provider-agnostic, no retry in this change, more negative tests.

Threat model / severity rubric (written before the review):
- HIGH = real spend can escape the caps (a billed call settles at $0), a cap or reservation leaks (never released or double-released), or a provider-specific behaviour leaks into shared code so another provider is mis-settled.
- MEDIUM = a realistic Google response shape is mis-settled (either way), or startup accepts a malformed policy.
- LOW = test/doc gaps.

Questions:
1. Round-1 closure: is each of the 5 round-1 findings closed? (cite file:line)
2. Can any response that Google bills settle at $0 under `is_unbilled_error` + the profile policy? Consider: streaming vs non-streaming, partial bodies, 503 after headers, gzip, HTML 503 from a proxy/CDN, a 503 list with extra fields, `usage` present as {} or 0 values.
3. Reservation/settlement accounting: does the $0 path release the MemoryBudget and DB reservations exactly once on every path (success, fallback, cancellation, timeout, breaker)?
4. D-017: is anything Google-specific outside the profile files?
5. Release safety for deploying on top of prod fda8fa0: config/profile parsing at startup (could an existing llm.env/profile fail to load?), fingerprint/manifest checks, rollback.
Output: `## Verdict` (GO / GO-with-fixes / NO-GO), then numbered findings (severity, file:line, failure scenario, minimal fix). A HIGH needs a concrete reproducer. ≤ 70 lines.
