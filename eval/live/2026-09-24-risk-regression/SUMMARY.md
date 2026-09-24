# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.01749551 of the $0.5 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-gpt6-luna | openai/gpt-6-luna | 1.000 / 1.000 | 0.075 / 0.075 | 80/80 | 1423.0 / 3109.8 | 0.0175 | PASS |
