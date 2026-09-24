# G-LIVE-D (memory.query synthesis) 2026-09-24

Fixture `tests/fixtures/synthesis`: the 62 weak-evidence questions (top RRF < τ_s = 0.0434) of the held-out `test` split, plus the weak negatives; 1 reps per profile, each profile alone, production path with the 6 s cap. Fast path only (answer key in one drilldown of the top-3 clues): 0.435. Pass: synthesis accuracy (answer key in the synthesis text) − fast path ≥ 0.03 on every rep. Spent $0.03770674 of the $0.3 guard.

| profile | model | synthesis acc min / mean | Δ vs fast path min / mean | system acc min | negatives answered (max) | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|---|
| openrouter-gpt6-luna | openai/gpt-6-luna | 0.597 / 0.597 | +0.161 / +0.161 | 0.645 | 0 | 1404.0 / 2807.05 | 0.0377 | PASS |
