# hlm bench — suite v2 — `openai/gpt-6-luna-pro`

profile `openrouter-gpt6-luna-pro` · mode live · reps 3 · gold adj-2+4c0651b4 · packs public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 0.5

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config 384d71c52adb

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 92.6% | 68/75 | 9.3% | 0 (0.0%) / 0 | 0 | 0 | 4162 | 7098 | 0.046934 | 0.00062579 | 0.00069021 | 1.13 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T9 | 75 | 92.6% | 68 | 9.3% | 91.1% | 1.5% | 0/0 | 4162 | 7098 | 0.00062579 | 0.00069021 | 98.1% | 100.0% | 80.6% |
| all/easy | 18 | 98.1% | 17 | 5.6% | 94.2% | 2.8% | 0/0 | 4097 | 7879 | 0.00062271 | 0.00065934 | | | |
| all/medium | 30 | 100.0% | 30 | 0.0% | 100.0% | 0.0% | 0/0 | 3710 | 5387 | 0.00059617 | 0.00059617 | | | |
| all/hard | 27 | 80.6% | 21 | 22.2% | 75.2% | 5.9% | 0/0 | 4493 | 7098 | 0.00066075 | 0.00084954 | | | |

Packs: public 92.6% (n=75)

Error metrics: T9_catch_rate=1.0, T9_false_warn_rate=0.2381
Tokens: prompt 752485, completion 19424 (reasoning 11003), cached 422514.
Projection: 0.00062579 USD/call x 60 calls/day x 30 = 1.13 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
