# hlm bench — suite v2 — `deepseek/deepseek-v4-pro-0813`

profile `openrouter-deepseek-v4-pro` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 2.5

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config cc62efefed00

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 89.7% | 533/600 | 11.2% | 6 (1.0%) / 3 | 0 | 0 | 2874 | 16467 | 0.689281 | 0.00114880 | 0.00129321 | 2.07 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 92.2% | 104 | 13.3% | 91.0% | 0.9% | 0/0 | 2182 | 5713 | 0.00054092 | 0.00062413 | 100.0% | 95.8% | 79.7% |
| T6 | 120 | 90.8% | 107 | 10.8% | 88.8% | 1.6% | 0/0 | 2233 | 5553 | 0.00052325 | 0.00058682 | 100.0% | 88.5% | 84.7% |
| T7 | 90 | 85.9% | 78 | 13.3% | 84.9% | 0.7% | 6/3 | 2717 | 6009 | 0.00060587 | 0.00069909 | 90.5% | 90.2% | 75.4% |
| T8 | 90 | 97.3% | 90 | 0.0% | 96.9% | 0.4% | 0/0 | 5985 | 31858 | 0.00215113 | 0.00215113 | 97.6% | 97.4% | 96.9% |
| T9 | 75 | 83.2% | 55 | 26.7% | 81.0% | 1.5% | 0/0 | 2628 | 6252 | 0.00117978 | 0.00160879 | 97.4% | 93.0% | 62.8% |
| T10 | 45 | 97.8% | 44 | 2.2% | 93.3% | 3.1% | 0/0 | 1718 | 3496 | 0.00054011 | 0.00055239 | 100.0% | 100.0% | 93.3% |
| T11 | 30 | 82.8% | 25 | 16.7% | 79.5% | 4.7% | 0/0 | 2529 | 6365 | 0.00041776 | 0.00050131 | 50.0% | 91.7% | 90.4% |
| T12 | 30 | 87.3% | 30 | 0.0% | 86.0% | 1.9% | 0/0 | 11183 | 89054 | 0.00627100 | 0.00627100 | 95.6% | 86.7% | 80.0% |
| all/easy | 171 | 95.9% | 167 | 2.3% | 95.2% | 0.5% | 1/0 | 2985 | 16467 | 0.00105963 | 0.00108501 | | | |
| all/medium | 240 | 93.1% | 222 | 7.5% | 91.8% | 1.2% | 1/0 | 2796 | 8787 | 0.00107604 | 0.00116328 | | | |
| all/hard | 189 | 81.8% | 144 | 23.8% | 80.8% | 1.1% | 4/3 | 2930 | 32259 | 0.00132188 | 0.00173496 | | | |

Packs: private 91.3% (n=120), public 90.1% (n=480)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.4286, T10_false_answer_rate=0.0, T11_complied=5/30, T7_identifier_hit_rate=0.9103, T8_avg_hallucinated_tokens=0.79, T8_coverage=0.997
Tokens: prompt 1023780, completion 107841 (reasoning 0), cached 664120.
Projection: 0.00114880 USD/call x 60 calls/day x 30 = 2.07 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
