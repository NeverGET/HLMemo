# G-LIVE-B 2026-09-24 — profile `openrouter` (`deepseek/deepseek-v4.1-flash`), verifier `openrouter`

Verdict: **FAIL** · mode `record` · reps 3 · calls 299 · JSON-fail 8 (2.6%) · infra errors 0 · p50/p95 1640/12154 ms · cost $0.08355022182

| metric | worst over reps | mean | threshold |
|---|---|---|---|
| placement | 0.984 | 0.984 | ≥ 0.90 |
| contradiction exact | 0.978 | 0.985 | ≥ 0.90 |
| false supersede | 0.000 | 0.000 | ≤ 0.02 |
| positive recall | 0.889 | 0.926 | ≥ 0.85 |
| positive precision | 1.000 | 1.000 | ≥ 0.90 |
| supersession direction | 0.750 | 0.833 | ≥ 0.90 |
| false cross-project raise | 0.000 | 0.000 | ≤ 0.02 |
| every positive class (worst) | contra_new 0.85, contra_old 0.33, contra_none 1.00, duplicate 1.00, refines 1.00 | | ≥ 0.66 each |
| v2 false close (both fixtures) | 0.000 | | ≤ 0.02 |
| v2 close recall (gold whole) | 0.000 | | report |
| v2 refines direction | 1.000 | | ≥ 0.90 |
| v2 duplicate precision (strict) | 1.000 | | ≥ 0.90 |
| v2 ext fixture exact | 0.897 | | report |
| v2 per-class precision (worst) | duplicate 1.00, relates 1.00, refines 1.00, contradicts 1.00 | | report |
| v2 tier precision (worst) | action 0.96, question 0.89 | | report |

Provider calls (ledger: mode | task | profile | model | outcome → n): record|place|openrouter|deepseek/deepseek-v4.1-flash|ok → 48; record|relate_verify|openrouter|deepseek/deepseek-v4.1-flash|ok → 112; record|relate|openrouter|deepseek/deepseek-v4.1-flash|ok → 138; record|relate|openrouter|deepseek/deepseek-v4.1-flash|schema_fail → 17; record|relate|openrouter|deepseek/deepseek-v4.1-flash|schema_retry_ok → 1

Per class (rep 0): contra_new 0.85 (n=13), contra_none 1.00 (n=3), contra_old 0.33 (n=3), duplicate 1.00 (n=10), near_miss 1.00 (n=77), none 1.00 (n=66), refines 1.00 (n=7)

Prompts: v1. Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted; raw provider responses are never stored.
