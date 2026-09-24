# hlm bench — suite v2 — `deepseek/deepseek-v4-pro-0813`

profile `openrouter-deepseek-v4-pro` · mode live · reps 3 · gold adj-2+4c0651b4 · packs public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 2.0

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config eb59a23b473f

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 90.8% | 63/75 | 16.0% | 4 (5.3%) / 1 | 0 | 0 | 12538 | 87351 | 0.265862 | 0.00354483 | 0.00422003 | 6.38 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T9 | 75 | 90.8% | 63 | 16.0% | 89.2% | 1.7% | 4/1 | 12538 | 87351 | 0.00354483 | 0.00422003 | 96.1% | 94.2% | 83.5% |
| all/easy | 18 | 96.1% | 16 | 11.1% | 94.2% | 2.8% | 1/0 | 12634 | 110614 | 0.00336682 | 0.00378767 | | | |
| all/medium | 30 | 94.2% | 25 | 16.7% | 93.0% | 1.7% | 0/0 | 6991 | 51271 | 0.00278901 | 0.00334681 | | | |
| all/hard | 27 | 83.5% | 22 | 18.5% | 77.8% | 4.5% | 3/1 | 17010 | 87351 | 0.00450329 | 0.00552677 | | | |

Packs: public 90.8% (n=75)

Error metrics: T9_catch_rate=1.0, T9_false_warn_rate=0.15
Tokens: prompt 215365, completion 56988 (reasoning 53276), cached 178135.
Projection: 0.00354483 USD/call x 60 calls/day x 30 = 6.38 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
