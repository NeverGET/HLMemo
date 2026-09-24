# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 3 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.10630371 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| openrouter-qwen38-27b-fast | qwen/qwen3.8-27b | 1.000 / 1.000 | 0.050 / 0.033 | 232/240 | 1459.0 / 3168.45 | 0.1063 | PASS |
