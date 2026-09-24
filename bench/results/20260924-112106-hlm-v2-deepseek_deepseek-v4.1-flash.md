# hlm bench — suite v2 — `deepseek/deepseek-v4.1-flash`

profile `openrouter` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 1.5

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config dc0324d70a59

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 89.9% | 516/600 | 14.0% | 1 (0.2%) / 0 | 0 | 0 | 825 | 5767 | 0.193886 | 0.00032314 | 0.00037575 | 0.58 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 96.9% | 114 | 5.0% | 96.9% | 0.0% | 0/0 | 745 | 3537 | 0.00015733 | 0.00016561 | 100.0% | 100.0% | 89.8% |
| T6 | 120 | 94.6% | 112 | 6.7% | 93.8% | 1.2% | 0/0 | 613 | 2590 | 0.00010321 | 0.00011058 | 100.0% | 86.5% | 100.0% |
| T7 | 90 | 89.8% | 82 | 8.9% | 88.3% | 1.1% | 1/0 | 871 | 5902 | 0.00018245 | 0.00020025 | 90.5% | 90.1% | 88.5% |
| T8 | 90 | 97.0% | 90 | 0.0% | 96.6% | 0.4% | 0/0 | 2435 | 22188 | 0.00071704 | 0.00071704 | 98.3% | 98.0% | 94.3% |
| T9 | 75 | 59.7% | 16 | 78.7% | 59.5% | 0.2% | 0/0 | 896 | 8001 | 0.00034396 | 0.00161232 | 69.5% | 67.7% | 44.2% |
| T10 | 45 | 100.0% | 45 | 0.0% | 100.0% | 0.0% | 0/0 | 487 | 1942 | 0.00007043 | 0.00007043 | 100.0% | 100.0% | 100.0% |
| T11 | 30 | 97.3% | 29 | 3.3% | 92.8% | 3.1% | 0/0 | 596 | 1543 | 0.00014218 | 0.00014708 | 100.0% | 94.4% | 98.8% |
| T12 | 30 | 84.0% | 28 | 6.7% | 83.9% | 0.0% | 0/0 | 2757 | 4574 | 0.00161453 | 0.00172985 | 86.7% | 85.0% | 79.9% |
| all/easy | 171 | 94.3% | 159 | 7.0% | 93.7% | 0.4% | 0/0 | 765 | 4048 | 0.00027955 | 0.00030065 | | | |
| all/medium | 240 | 90.5% | 206 | 14.2% | 89.7% | 0.5% | 1/0 | 825 | 5522 | 0.00031621 | 0.00036840 | | | |
| all/hard | 189 | 86.6% | 151 | 20.1% | 86.0% | 0.4% | 0/0 | 871 | 7860 | 0.00037140 | 0.00046486 | | | |

Packs: private 92.5% (n=120), public 89.8% (n=480)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.4286, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.17, T8_coverage=0.9991
Tokens: prompt 1017205, completion 105480 (reasoning 0), cached 763996.
Projection: 0.00032314 USD/call x 60 calls/day x 30 = 0.58 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
