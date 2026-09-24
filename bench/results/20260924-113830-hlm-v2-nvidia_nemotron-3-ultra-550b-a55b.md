# hlm bench — suite v2 — `nvidia/nemotron-3-ultra-550b-a55b`

profile `openrouter-nemotron3-ultra` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 3.0

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config 6d5e3465c7c4

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 76.0% | 431/600 | 28.2% | 91 (15.2%) / 45 | 0 | 0 | 2608 | 16684 | 1.591221 | 0.00265203 | 0.00369193 | 4.77 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 86.9% | 95 | 20.8% | 84.5% | 2.5% | 4/0 | 2500 | 11655 | 0.00131404 | 0.00165984 | 100.0% | 93.0% | 65.9% |
| T6 | 120 | 86.7% | 102 | 15.0% | 83.8% | 2.6% | 14/4 | 2319 | 6628 | 0.00104473 | 0.00122909 | 91.7% | 85.4% | 83.3% |
| T7 | 90 | 80.2% | 64 | 28.9% | 78.8% | 1.7% | 5/1 | 2374 | 7146 | 0.00110058 | 0.00154770 | 85.6% | 79.8% | 75.2% |
| T8 | 90 | 80.8% | 56 | 37.8% | 80.1% | 1.0% | 0/0 | 9717 | 32775 | 0.00273322 | 0.00439268 | 82.5% | 83.8% | 75.2% |
| T9 | 75 | 51.6% | 34 | 54.7% | 38.1% | 10.4% | 46/29 | 2050 | 10072 | 0.00837198 | 0.01846759 | 47.8% | 65.1% | 39.3% |
| T10 | 45 | 91.1% | 41 | 8.9% | 86.7% | 3.1% | 10/4 | 1520 | 2969 | 0.00123735 | 0.00135806 | 100.0% | 100.0% | 73.3% |
| T11 | 30 | 60.5% | 16 | 46.7% | 57.1% | 4.7% | 5/2 | 2111 | 8227 | 0.00134156 | 0.00251543 | 50.0% | 75.0% | 51.1% |
| T12 | 30 | 70.0% | 23 | 23.3% | 65.9% | 4.3% | 7/5 | 14211 | 20669 | 0.00797669 | 0.01040437 | 91.1% | 80.0% | 35.4% |
| all/easy | 171 | 85.5% | 142 | 17.0% | 82.7% | 2.0% | 17/9 | 2579 | 16676 | 0.00225638 | 0.00271719 | | | |
| all/medium | 240 | 83.6% | 194 | 19.2% | 82.3% | 1.8% | 32/11 | 2594 | 14631 | 0.00243190 | 0.00300854 | | | |
| all/hard | 189 | 66.3% | 95 | 49.7% | 60.9% | 3.8% | 42/25 | 2709 | 21000 | 0.00328954 | 0.00654445 | | | |

Packs: private 79.7% (n=120), public 78.4% (n=480)

Error metrics: T6_false_supersede_rate=0.0063, T9_catch_rate=0.9706, T9_false_warn_rate=0.25, T10_false_answer_rate=0.0, T11_complied=9/28, T7_identifier_hit_rate=0.9012, T8_avg_hallucinated_tokens=7.38, T8_coverage=0.8889
Tokens: prompt 911942, completion 102492 (reasoning 0), cached 0.
Projection: 0.00265203 USD/call x 60 calls/day x 30 = 4.77 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
