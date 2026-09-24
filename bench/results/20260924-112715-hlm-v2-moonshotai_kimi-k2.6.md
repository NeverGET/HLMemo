# hlm bench — suite v2 — `moonshotai/kimi-k2.6`

profile `openrouter-kimi-k26` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 2.5

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config 21f36ef58926

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 83.7% | 512/600 | 14.7% | 8 (1.3%) / 4 | 0 | 0 | 2209 | 13945 | 0.633495 | 0.00105583 | 0.00123730 | 1.90 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 90.8% | 104 | 13.3% | 89.8% | 1.4% | 1/0 | 2300 | 8573 | 0.00066895 | 0.00077187 | 94.6% | 95.3% | 80.9% |
| T6 | 120 | 93.8% | 111 | 7.5% | 93.8% | 0.0% | 0/0 | 1597 | 6266 | 0.00047440 | 0.00051287 | 91.7% | 96.9% | 91.7% |
| T7 | 90 | 84.7% | 73 | 18.9% | 82.5% | 1.6% | 2/1 | 2184 | 8208 | 0.00049459 | 0.00060977 | 86.2% | 84.8% | 83.1% |
| T8 | 90 | 94.4% | 88 | 2.2% | 94.0% | 0.6% | 0/0 | 6344 | 22339 | 0.00184399 | 0.00188590 | 94.6% | 96.8% | 91.0% |
| T9 | 75 | 78.7% | 49 | 34.7% | 78.2% | 0.7% | 0/0 | 2286 | 8070 | 0.00135449 | 0.00207321 | 87.0% | 89.1% | 61.5% |
| T10 | 45 | 95.6% | 43 | 4.4% | 93.3% | 3.1% | 0/0 | 1417 | 3149 | 0.00038168 | 0.00039944 | 100.0% | 100.0% | 86.7% |
| T11 | 30 | 50.0% | 15 | 50.0% | 40.0% | 8.2% | 5/3 | 1868 | 14480 | 0.00074237 | 0.00148474 | 33.3% | 75.0% | 33.3% |
| T12 | 30 | 82.0% | 29 | 3.3% | 81.9% | 0.0% | 0/0 | 13900 | 45498 | 0.00482620 | 0.00499262 | 86.7% | 80.0% | 79.9% |
| all/easy | 171 | 89.7% | 153 | 10.5% | 88.1% | 1.6% | 3/1 | 2418 | 13029 | 0.00099907 | 0.00111661 | | | |
| all/medium | 240 | 92.1% | 216 | 10.0% | 91.9% | 0.1% | 0/0 | 2194 | 13885 | 0.00099824 | 0.00110915 | | | |
| all/hard | 189 | 79.3% | 143 | 24.3% | 78.5% | 1.1% | 5/3 | 2184 | 16614 | 0.00118030 | 0.00155998 | | | |

Packs: private 89.4% (n=120), public 86.9% (n=480)

Error metrics: T6_false_supersede_rate=0.0182, T9_catch_rate=1.0, T9_false_warn_rate=0.4286, T10_false_answer_rate=0.0, T11_complied=9/27, T7_identifier_hit_rate=0.925, T8_avg_hallucinated_tokens=1.29, T8_coverage=0.9933
Tokens: prompt 982624, completion 86556 (reasoning 76), cached 691566.
Projection: 0.00105583 USD/call x 60 calls/day x 30 = 1.90 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
