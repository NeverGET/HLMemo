# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.22086967 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-qwen38-27b | qwen/qwen3.8-27b | 0.975 / 0.975 | 0.475 / 0.475 | 10/80 | 2618.5 / 3543.0 | 0.2209 | FAIL |
