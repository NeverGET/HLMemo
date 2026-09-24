# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.03095829 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-glm53-flash | z-ai/glm-5.3-flash | 1.000 / 1.000 | 0.075 / 0.075 | 69/80 | 1297.0 / 3034.8 | 0.0310 | PASS |
