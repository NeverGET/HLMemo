# G-LIVE-B 2026-09-24 — profile `openrouter-gpt6-luna` (`openai/gpt-6-luna`), verifier `openrouter-gpt6-luna`

Verdict: **PASS** · mode `record` · reps 3 · calls 301 · JSON-fail 0 (0.0%) · infra errors 0 · p50/p95 3109/6166 ms · cost $0.046411750

| metric | worst over reps | mean | threshold |
|---|---|---|---|
| placement | 0.984 | 0.984 | ≥ 0.90 |
| contradiction exact | 0.989 | 0.994 | ≥ 0.90 |
| false supersede | 0.000 | 0.000 | ≤ 0.02 |
| positive recall | 0.944 | 0.972 | ≥ 0.85 |
| positive precision | 1.000 | 1.000 | ≥ 0.90 |
| supersession direction | 1.000 | 1.000 | ≥ 0.90 |
| false cross-project raise | 0.000 | 0.000 | ≤ 0.02 |
| every positive class (worst) | contra_new 1.00, contra_old 1.00, contra_none 1.00, duplicate 0.90, refines 0.86 | | ≥ 0.66 each |
| v2 false close (both fixtures) | 0.000 | | ≤ 0.02 |
| v2 close recall (gold whole) | 0.000 | | report |
| v2 refines direction | 1.000 | | ≥ 0.90 |
| v2 duplicate precision (strict) | 1.000 | | ≥ 0.90 |
| v2 ext fixture exact | 0.862 | | report |
| v2 per-class precision (worst) | duplicate 1.00, relates 1.00, refines 0.90, contradicts 1.00 | | report |
| v2 tier precision (worst) | action 1.00, question 0.85 | | report |

Provider calls (ledger: mode | task | profile | model | outcome → n): record|place|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 48; record|relate_verify|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 106; record|relate|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 147

Per class (rep 0): contra_new 1.00 (n=13), contra_none 1.00 (n=3), contra_old 1.00 (n=3), duplicate 1.00 (n=10), near_miss 1.00 (n=77), none 1.00 (n=66), refines 1.00 (n=7)

Prompts: v1. Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted; raw provider responses are never stored.
