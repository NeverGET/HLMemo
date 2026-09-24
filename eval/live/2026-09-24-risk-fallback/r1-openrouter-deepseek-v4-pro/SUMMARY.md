# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.37685452 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-deepseek-v4-pro | deepseek/deepseek-v4-pro-0813 | 0.975 / 0.975 | 0.450 / 0.450 | 11/80 | 2938.0 / 3821.5 | 0.3769 | FAIL |
