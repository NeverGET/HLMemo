# hlm bench — suite v2 — `deepseek/deepseek-v4-pro-0813`

profile `openrouter-deepseek-v4-pro` · mode live · reps 3 · gold adj-2+4c0651b4 · packs private, public

prompt bench-v2-p1 (sha256 be426d40e385) · schema bench-v2-s1 · redaction redact/1 · max-usd 4.0

budget: db (shared llm_budget of 'hlm_bench_fb': hour 15.00000000 / day 15.00000000 / month 15.00000000 USD) + --max-usd run cap · config eb59a23b473f

## Overview

| macro mean | correct | error rate | JSON fail first/final | infra | cap | p50 ms | p95 ms | cost USD | $/task | $/correct | $/month |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 95.1% | 563/600 | 6.2% | 29 (4.8%) / 8 | 0 | 0 | 8992 | 50698 | 2.037181 | 0.00339530 | 0.00361844 | 6.11 |

## Per task

| task | n | mean | correct | error rate | min-rep | rep-sd | JSON first/final | p50 ms | p95 ms | $/task | $/correct | easy | medium | hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T5 | 120 | 99.0% | 118 | 1.7% | 98.5% | 0.7% | 3/0 | 7060 | 29195 | 0.00162827 | 0.00165586 | 100.0% | 100.0% | 96.7% |
| T6 | 120 | 96.7% | 116 | 3.3% | 95.0% | 1.2% | 4/2 | 4187 | 31169 | 0.00119793 | 0.00123924 | 100.0% | 100.0% | 88.9% |
| T7 | 90 | 90.3% | 79 | 12.2% | 88.3% | 1.5% | 1/0 | 9308 | 44596 | 0.00222404 | 0.00253372 | 93.3% | 90.6% | 87.0% |
| T8 | 90 | 96.2% | 89 | 1.1% | 93.6% | 1.8% | 8/1 | 18055 | 96235 | 0.00626849 | 0.00633892 | 98.3% | 97.1% | 92.9% |
| T9 | 75 | 89.1% | 62 | 17.3% | 85.5% | 3.0% | 7/3 | 10237 | 57135 | 0.00408466 | 0.00494112 | 94.8% | 95.3% | 78.5% |
| T10 | 45 | 95.6% | 43 | 4.4% | 93.3% | 3.1% | 3/2 | 2349 | 10450 | 0.00091490 | 0.00095745 | 100.0% | 88.9% | 100.0% |
| T11 | 30 | 96.3% | 29 | 3.3% | 90.0% | 4.5% | 0/0 | 6842 | 34967 | 0.00151282 | 0.00156499 | 100.0% | 91.7% | 99.2% |
| T12 | 30 | 97.9% | 27 | 10.0% | 97.9% | 0.0% | 3/0 | 46671 | 442463 | 0.01802681 | 0.02002979 | 100.0% | 94.6% | 100.0% |
| all/easy | 171 | 98.1% | 169 | 1.2% | 97.7% | 0.3% | 3/0 | 7527 | 39191 | 0.00265162 | 0.00268300 | | | |
| all/medium | 240 | 96.0% | 227 | 5.4% | 95.5% | 0.7% | 8/2 | 7997 | 34386 | 0.00276584 | 0.00292423 | | | |
| all/hard | 189 | 91.2% | 167 | 11.6% | 88.2% | 2.2% | 18/6 | 11739 | 107048 | 0.00486747 | 0.00550870 | | | |

Packs: private 96.2% (n=120), public 94.9% (n=480)

Error metrics: T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=0.9506, T8_avg_hallucinated_tokens=0.47, T8_coverage=0.9932
Tokens: prompt 1010809, completion 456511 (reasoning 357811), cached 703785.
Projection: 0.00339530 USD/call x 60 calls/day x 30 = 6.11 USD/month.

correct = score >= 0.8; error rate = 1 - correct/n; JSON first = the first answer failed parse/schema (the provider retries once), final = the retry failed too (scored 0); infra/cap calls are excluded. Definitions: src/hlmemo/bench/report.py.
