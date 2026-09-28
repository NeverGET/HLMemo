# Jev ("TypeSafe AI's System One model") — research → test → integration (started 2026-09-28)

Owner request (2026-09-28): change the method. First a plain DEEP RESEARCH on Jev (what it is, how it works, why it exists,
where it is used), compile it, then design and run GENERAL tests (not tied to HLMemo) to see how well it works, then
discuss integration. Access via OpenRouter (balance ~$11). HLMemo itself stays PAUSED (D-196): no docker, no project code.

Seed links (owner):
- https://vercel.com/i/what-is-jev
- https://vercel.com/kb/jev-from-typesafe-ai
- https://vercel.com/ai-gateway/models/jev
- https://vercel.com/connect/jev
- https://vercel.com/docs/connect

Phases
1. Research (parallel, $0): 01-primary-sources.md (vendor + Vercel docs) · 02-independent-evidence.md (OpenRouter data,
   third-party evals, community, background: System 1/2, type-safe/constrained decoding, red flags).
2. Compile: 03-JEV-DOSSIER.md (Turkish, for the owner): claims vs evidence, confidence per claim.
3. Test plan (hypothesis-driven, shown to the owner before spending) → 04-TEST-PLAN.md; run → 05-TEST-RESULTS.md. Budget ≤ ~$1–2.
4. Integration discussion (HLMemo) → 06-INTEGRATION.md.
Rules: every claim sourced (URL + quote); vendor claims vs independent facts kept apart; no invented facts.

## Pre-registered opinions (owner request, 2026-09-28)
The owner wants my own view after the research AND how it changes after the tests.
- 03b-MY-VIEW-BEFORE-TESTS.md — written after the research, BEFORE any test is run, and committed immediately (git timestamp =
  provenance; no silent edits later). Each belief: statement · my confidence (%) · what test result would change it (falsifier).
- 07-MY-VIEW-AFTER-TESTS.md — the same beliefs side by side with the results: confirmed / refuted / shifted, the new
  confidence, and why. Surprises called out explicitly.

## Test run (owner approved T1–T8, 2026-09-28)
- General T1–T7: raw + scripts in docs/private/jev-tests/general/ (gitignored); aggregates → 05-TEST-RESULTS-general.md. Cap $0.80.
- T8 (HLMemo rerank, own memory data): raw in docs/private/jev-tests/t8/; aggregates only → 05-TEST-RESULTS-T8.md. Cap $0.25.
- Then: 06-INTEGRATION.md (discussion) and 07-MY-VIEW-AFTER-TESTS.md (belief-by-belief vs 03b).
