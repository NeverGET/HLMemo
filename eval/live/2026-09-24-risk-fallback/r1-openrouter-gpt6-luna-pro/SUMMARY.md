# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.05178485 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-gpt6-luna-pro | openai/gpt-6-luna-pro | 0.975 / 0.975 | 0.150 / 0.150 | 48/80 | 3064.5 / 3938.85 | 0.0518 | FAIL |
