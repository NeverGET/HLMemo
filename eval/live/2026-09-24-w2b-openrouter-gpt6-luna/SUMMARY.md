# G-LIVE-B 2026-09-24 — profile `openrouter-gpt6-luna` (`openai/gpt-6-luna`), verifier `openrouter-gpt6-luna`

Verdict: **PASS** · mode `live` · reps 3 · calls 228 · JSON-fail 0 (0.0%) · infra errors 0 · p50/p95 2465/3624 ms · cost $0.03178459

| metric | worst over reps | mean | threshold |
|---|---|---|---|
| placement | 0.984 | 0.984 | ≥ 0.90 |
| contradiction exact | 0.994 | 0.996 | ≥ 0.90 |
| false supersede | 0.000 | 0.000 | ≤ 0.02 |
| positive recall | 0.972 | 0.982 | ≥ 0.85 |
| positive precision | 1.000 | 1.000 | ≥ 0.90 |
| supersession direction | 1.000 | 1.000 | ≥ 0.90 |
| false cross-project raise | 0.000 | 0.000 | ≤ 0.02 |
| every positive class (worst) | contra_new 1.00, contra_old 1.00, contra_none 1.00, duplicate 1.00, refines 0.86 | | ≥ 0.66 each |

Provider calls (ledger: mode | task | profile | model | outcome → n): live|place|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 48; live|relate_verify|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 69; live|relate|openrouter-gpt6-luna|openai/gpt-6-luna|ok → 111

Per class (rep 0): contra_new 1.00 (n=13), contra_none 1.00 (n=3), contra_old 1.00 (n=3), duplicate 1.00 (n=10), near_miss 1.00 (n=77), none 1.00 (n=66), refines 0.86 (n=7)

Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted; raw provider responses are never stored.
