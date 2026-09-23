# hlm bench — suite v2 — `openai/gpt-6-luna`

profile `openrouter-gpt6-luna` · mode replay · reps 1 · gold adj-2+4c0651b4 · packs public · limit 2

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 1.0

budget: replay (no reservation) · config c0366c7ebd60

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 98.8% | 16/16 | 0.0% | 0 (0.0%) / 0 | 0 | 0 | 0 | 0 | 0.004911 | 0.00030692 | 0.00030692 | 0.55 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00019168 | 0.00019168 | 100.0% | - | - |
| T6 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00016205 | 0.00016205 | 100.0% | - | - |
| T7 | 2 | 90.0% | 2 | 0.0% | 90.0% | 0.0% | 0/0 | 0 | 0 | 0.00019092 | 0.00019092 | 90.0% | - | - |
| T8 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00033824 | 0.00033824 | 100.0% | - | - |
| T9 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00042011 | 0.00042011 | 100.0% | - | - |
| T10 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00016355 | 0.00016355 | 100.0% | 100.0% | - |
| T11 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00020049 | 0.00020049 | 100.0% | 100.0% | - |
| T12 | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00078836 | 0.00078836 | 100.0% | - | - |
| all/easy | 14 | 98.6% | 14 | 0.0% | 98.6% | 0.0% | 0/0 | 0 | 0 | 0.00032443 | 0.00032443 | | | |
| all/medium | 2 | 100.0% | 2 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00018442 | 0.00018442 | | | |

Packs: public 98.8% (n=16)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=None, T10_false_answer_rate=0.0, T11_complied=0/2, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.0, T8_coverage=1.0
Tokens: prompt 27704, completion 2898 (reasoning 0), cached 0.
Projection: 0.00030692 USD/call x 60 calls/day x 30 = 0.55 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
