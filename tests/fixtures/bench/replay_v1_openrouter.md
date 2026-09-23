# hlm bench — suite v1 — `deepseek/deepseek-v4.1-flash`

profile `openrouter` · mode replay · reps 1 · gold v1 · packs v1 fixtures

prompt placement v1, contradiction v1, summary v1, risk v1 · schema placement v1, contradiction v1, summary v1, risk v1 · redaction redact/1 · max-usd 1.0

budget: replay (no reservation) · config cbbba7dbd24d

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100.0% | 33/33 | 0.0% | 0 (0.0%) / 0 | 0 | 0 | 0 | 0 | 0.005218 | 0.00015811 | 0.00015811 | 0.28 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 placement | 10 | 100.0% | 10 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00013784 | 0.00013784 | - | - | - |
| T2 contradiction | 10 | 100.0% | 10 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00012773 | 0.00012773 | - | - | - |
| T3 summary | 5 | 100.0% | 5 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00023843 | 0.00023843 | - | - | - |
| T4 risk | 8 | 100.0% | 8 | 0.0% | 100.0% | 0.0% | 0/0 | 0 | 0 | 0.00017122 | 0.00017122 | - | - | - |

Packs: v1 100.0% (n=33)
Tokens: prompt 13836, completion 1663 (reasoning 0), cached 1536.
Projection: 0.00015811 USD/call x 60 calls/day x 30 = 0.28 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
