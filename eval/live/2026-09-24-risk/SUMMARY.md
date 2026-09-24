# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 3 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.03281100 of the $2.5 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-gpt6-luna | openai/gpt-6-luna | 1.000 / 1.000 | 0.050 / 0.033 | 237/240 | 1460.0 / 3070.2 | 0.0109 | PASS |
| openrouter | deepseek/deepseek-v4.1-flash | 1.000 / 1.000 | 0.250 / 0.242 | 229/240 | 858.0 / 2011.2 | 0.0219 | FAIL |
