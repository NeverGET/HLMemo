# hlm bench — suite v2 — `openai/gpt-6-luna`

profile `openrouter-gpt6-luna` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 1.5

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config c0366c7ebd60

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 95.4% | 561/600 | 6.5% | 0 (0.0%) / 0 | 0 | 0 | 2631 | 6307 | 0.102622 | 0.00017104 | 0.00018293 | 0.31 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 99.5% | 119 | 0.8% | 98.4% | 0.7% | 0/0 | 2589 | 4282 | 0.00011350 | 0.00011445 | 100.0% | 100.0% | 98.3% |
| T6 | 120 | 92.4% | 110 | 8.3% | 91.2% | 0.9% | 0/0 | 2345 | 3334 | 0.00009374 | 0.00010227 | 91.7% | 98.6% | 84.7% |
| T7 | 90 | 88.6% | 75 | 16.7% | 87.7% | 1.3% | 0/0 | 2674 | 3449 | 0.00011386 | 0.00013663 | 90.5% | 88.6% | 86.8% |
| T8 | 90 | 92.4% | 84 | 6.7% | 90.8% | 1.2% | 0/0 | 4145 | 6031 | 0.00027617 | 0.00029590 | 94.0% | 92.5% | 90.6% |
| T9 | 75 | 94.2% | 70 | 6.7% | 92.0% | 1.7% | 0/0 | 2429 | 4326 | 0.00018819 | 0.00020163 | 98.1% | 100.0% | 85.2% |
| T10 | 45 | 100.0% | 45 | 0.0% | 100.0% | 0.0% | 0/0 | 1247 | 2393 | 0.00007175 | 0.00007175 | 100.0% | 100.0% | 100.0% |
| T11 | 30 | 99.7% | 30 | 0.0% | 99.5% | 0.2% | 0/0 | 1999 | 4002 | 0.00010956 | 0.00010956 | 100.0% | 100.0% | 99.2% |
| T12 | 30 | 96.6% | 28 | 6.7% | 91.8% | 3.5% | 0/0 | 7771 | 12652 | 0.00073405 | 0.00078648 | 100.0% | 95.0% | 95.4% |
| all/easy | 171 | 95.6% | 161 | 5.9% | 94.9% | 0.5% | 0/0 | 2611 | 5581 | 0.00015290 | 0.00016240 | | | |
| all/medium | 240 | 96.7% | 232 | 3.3% | 95.9% | 0.5% | 0/0 | 2581 | 6041 | 0.00016524 | 0.00017094 | | | |
| all/hard | 189 | 91.1% | 168 | 11.1% | 89.5% | 1.3% | 0/0 | 2826 | 10177 | 0.00019481 | 0.00021916 | | | |

Packs: private 93.1% (n=120), public 95.0% (n=480)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=1.83, T8_coverage=0.9888
Tokens: prompt 985149, completion 110110 (reasoning 31440), cached 656793.
Projection: 0.00017104 USD/call x 60 calls/day x 30 = 0.31 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
