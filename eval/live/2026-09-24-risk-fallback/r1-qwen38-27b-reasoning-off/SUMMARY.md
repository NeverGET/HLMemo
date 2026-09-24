# G-LIVE-C (memory.risk_check) 2026-09-24

Fixture `tests/fixtures/risk` (40 positive / 40 negative), 1 reps, production path with the 4 s cap. Pass: catch >= 0.85 and false-warn <= 0.1 on every rep. Spent $0.03297554 of the $2 guard.

| profile | model | catch min / mean | false-warn max / mean | judged | p50 / p95 ms | cost $ | pass |
|---|---|---|---|---|---|---|---|
| qwen38-27b-reasoning-off | qwen/qwen3.8-27b | 1.000 / 1.000 | 0.025 / 0.025 | 79/80 | 1702.0 / 3454.5 | 0.0330 | PASS |
