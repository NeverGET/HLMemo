# G-LIVE-B 2026-09-24 — profile `openrouter` (`deepseek/deepseek-v4.1-flash`), verifier `openrouter`

Verdict: **PASS** · mode `record` · reps 3 · calls 316 · JSON-fail 1 (0.3%) · infra errors 0 · p50/p95 1972/13765 ms · cost $0.09791499828

| metric | worst over reps | mean | threshold |
|---|---|---|---|
| placement | 0.984 | 0.984 | ≥ 0.90 |
| contradiction exact | 0.994 | 0.994 | ≥ 0.90 |
| false supersede | 0.000 | 0.000 | ≤ 0.02 |
| positive recall | 0.972 | 0.972 | ≥ 0.85 |
| positive precision | 0.972 | 0.982 | ≥ 0.90 |
| supersession direction | 0.938 | 0.958 | ≥ 0.90 |
| false cross-project raise | 0.000 | 0.000 | ≤ 0.02 |
| every positive class (worst) | contra_new 1.00, contra_old 0.67, contra_none 1.00, duplicate 0.90, refines 1.00 | | ≥ 0.66 each |
| v2 false close (both fixtures) | 0.000 | | ≤ 0.02 |
| v2 close recall (gold whole) | 0.895 | | report |
| v2 refines direction | 1.000 | | ≥ 0.90 |
| v2 duplicate precision (strict) | 1.000 | | ≥ 0.90 |
| v2 ext fixture exact | 1.000 | | report |
| v2 per-class precision (worst) | duplicate 1.00, relates 1.00, refines 1.00, contradicts 0.96 | | report |
| v2 tier precision (worst) | action 0.96, question 1.00 | | report |

Provider calls (ledger: mode | task | profile | model | outcome → n): record|place|openrouter|deepseek/deepseek-v4.1-flash|ok → 48; record|relate_verify|openrouter|deepseek/deepseek-v4.1-flash|ok → 122; record|relate|openrouter|deepseek/deepseek-v4.1-flash|ok → 145; record|relate|openrouter|deepseek/deepseek-v4.1-flash|schema_fail → 3; record|relate|openrouter|deepseek/deepseek-v4.1-flash|schema_retry_ok → 1

Per class (rep 0): contra_new 1.00 (n=13), contra_none 1.00 (n=3), contra_old 1.00 (n=3), duplicate 0.90 (n=10), near_miss 1.00 (n=77), none 1.00 (n=66), refines 1.00 (n=7)

Prompts: v2. Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted; raw provider responses are never stored.
