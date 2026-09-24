# hlm bench — suite v2 — `openai/gpt-6-luna-pro`

profile `openrouter-gpt6-luna-pro` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 2.0

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config 384d71c52adb

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 95.4% | 564/600 | 6.0% | 0 (0.0%) / 0 | 0 | 0 | 4058 | 9492 | 0.347013 | 0.00057836 | 0.00061527 | 1.04 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 98.0% | 116 | 3.3% | 96.9% | 1.4% | 0/0 | 3829 | 5926 | 0.00042856 | 0.00044334 | 100.0% | 100.0% | 93.2% |
| T6 | 120 | 91.0% | 108 | 10.0% | 89.6% | 1.2% | 0/0 | 3437 | 5748 | 0.00037970 | 0.00042189 | 91.7% | 97.2% | 81.9% |
| T7 | 90 | 89.8% | 77 | 14.4% | 89.0% | 0.7% | 0/0 | 4541 | 6069 | 0.00044804 | 0.00052368 | 91.7% | 88.5% | 89.5% |
| T8 | 90 | 92.6% | 88 | 2.2% | 91.7% | 0.9% | 0/0 | 7135 | 10158 | 0.00087116 | 0.00089096 | 94.7% | 93.0% | 90.1% |
| T9 | 75 | 94.7% | 71 | 5.3% | 92.0% | 1.9% | 0/0 | 3987 | 6603 | 0.00062984 | 0.00066533 | 100.0% | 100.0% | 85.2% |
| T10 | 45 | 100.0% | 45 | 0.0% | 100.0% | 0.0% | 0/0 | 2447 | 3892 | 0.00027789 | 0.00027789 | 100.0% | 100.0% | 100.0% |
| T11 | 30 | 99.0% | 30 | 0.0% | 98.5% | 0.4% | 0/0 | 3447 | 8724 | 0.00042308 | 0.00042308 | 100.0% | 100.0% | 97.5% |
| T12 | 30 | 98.0% | 29 | 3.3% | 97.9% | 0.1% | 0/0 | 9764 | 17392 | 0.00196195 | 0.00202960 | 100.0% | 98.2% | 95.6% |
| all/easy | 171 | 96.1% | 165 | 3.5% | 95.9% | 0.2% | 0/0 | 3841 | 8278 | 0.00052677 | 0.00054593 | | | |
| all/medium | 240 | 96.6% | 229 | 4.6% | 96.1% | 0.4% | 0/0 | 4007 | 8838 | 0.00056185 | 0.00058884 | | | |
| all/hard | 189 | 89.9% | 170 | 10.1% | 88.8% | 1.3% | 0/0 | 4356 | 10431 | 0.00064599 | 0.00071819 | | | |

Packs: private 92.3% (n=120), public 94.8% (n=480)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=2.2, T8_coverage=0.9882
Tokens: prompt 3990501, completion 254052 (reasoning 87636), cached 1989928.
Projection: 0.00057836 USD/call x 60 calls/day x 30 = 1.04 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
