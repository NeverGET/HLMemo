# HLMemo leaderboard

Rendered from `eval/results/leaderboard.json` by `hlm bench leaderboard` (append-only; do not edit by hand).

## Rules (W-E optimize-to-best, D-058, D-067)

- (1) A change that sets a new best on one corpus must not lose more than 1 point on any other corpus's best (it must report every ranked corpus); otherwise refused unless a decision is logged.
- (2) A merge that drops any corpus below its best by more than 1 point needs a logged decision.
- (3) Every iteration records $/query (retrieval) or $/correct (librarian) next to its score: the production budget is set on price/performance after development (D-058).
- (4) Tuning uses corpus A and the Phase 5 project sets; corpus B's sealed subset is for confirmation only, never for tuning.
- (5) History is append-only. Librarian boards keep the per-task error rates next to $/correct (D-067); only complete runs (full reference pack set by sha256, every case x rep, no infra/cap/partial) are ranked; a gold change is a new gold version and entries are re-scored under it (raw stays).

## Retrieval (best so far per corpus)

| corpus | primary | best | iteration | config | commit | embedder | $/query | fixture | secondary |
|---|---|---|---|---|---|---|---|---|---|
| corpus_a | hit@5 | 0.859 | #9 L@d3ce0ac guarded English query rewrite, prewarmed (HLM_QUERY_REWRITE) | 016c33e768f0 | d3ce0ac | intfloat/multilingual-e5-small@614241f622f5 | 3.94e-05 | 7a24ad29d981 | mrr=0.68; l2_key=0.87; l1_key=0.554; temporal_l2_stale_first=0.533; temporal_l2_key=0.6; stale_claim_l1_top3=0.2; negative_auc=0.813; tokens_query_mean=2949.7; query_p50_ms=84.4; query_p95_ms=126.5; hit@5_tr=0.867; hit@5_en=0.851; prewarm={'states': {'None': 51, 'applied': 48, 'unavailable': 1}, 'wait_seconds': 60.8, 'cold_query_ms_p50': 70.7, 'cold_query_ms_p95': 93.4} |
| corpus_b_dev | evidence Recall@5 | 0.556 | #9 L@d3ce0ac guarded English query rewrite, prewarmed (HLM_QUERY_REWRITE) | 016c33e768f0 | d3ce0ac | intfloat/multilingual-e5-small@614241f622f5 | 3.94e-05 | 5825dedbb693 | hit@5=0.75; mrr=0.506; l2_key=0.667; stale_claim_l1_top3=0.188; temporal_evidence_r5=0.25; temporal_l2_stale_first=0.375; negative_auc=0.772; tokens_query_mean=2948.7; query_p50_ms=79.8; query_p95_ms=120.6; hit@5_tr=0.722; hit@5_en=0.778; prewarm={'states': {'None': 41, 'applied': 38, 'unavailable': 1}, 'wait_seconds': 45.7, 'cold_query_ms_p50': 69.1, 'cold_query_ms_p95': 114.0} |
| corpus_b_sealed | evidence Recall@5 | sealed | - | - | - | - | - | b08a49dfc67e | confirmation only (rule 4): scored once, in aggregate, at the final acceptance |

History (append-only):

| corpus | # | date | label | commit | config | score | $/query | decision | merge | complete |
|---|---|---|---|---|---|---|---|---|---|---|
| corpus_a | 1 | 2026-09-23 | Phase 0 baseline (D-057) | 16abaf7 | 30f79015c14e | 0.793 | 0.0 | - | False | True |
| corpus_a | 2 | 2026-09-24 | main@1e57e08 re-measure, no librarian (W-E eval copy, 0008) | 1e57e08 | e4b32a157711 | 0.793 | 0.0 | - | False | True |
| corpus_a | 3 | 2026-09-24 | W2b librarian, role assistant, approve-all (G-E-TEMP/G-E-W2b eval copy) | 1e57e08 | b818796e0272 | 0.783 | 0.001368 | - | False | True |
| corpus_a | 4 | 2026-09-24 | J@a65a8f5 (librarian judgement v2 branch), no librarian: the before on the same eval copy | a65a8f5 | f34647b3f244 | 0.793 | 0.0 | - | False | True |
| corpus_a | 5 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 1 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 0652a76c53a8 | 0.761 | 0.002622 | - | False | True |
| corpus_a | 6 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 2 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 6a9428d10322 | 0.739 | 0.001374 | - | False | True |
| corpus_a | 7 | 2026-09-24 | L@d3ce0ac pivot flags off (prod-rule import, source populated) | d3ce0ac | 4f1c79a64af4 | 0.793 | 0.0 | - | False | True |
| corpus_a | 8 | 2026-09-24 | L@d3ce0ac per-source top-5 cap (HLM_RETRIEVAL_SOURCE_CAP) | d3ce0ac | a2bc92b87343 | 0.793 | 0.0 | - | False | True |
| corpus_a | 9 | 2026-09-24 | L@d3ce0ac guarded English query rewrite, prewarmed (HLM_QUERY_REWRITE) | d3ce0ac | 016c33e768f0 | 0.859 | 3.94e-05 | - | False | True |
| corpus_a | 10 | 2026-09-24 | L@d3ce0ac cap + query rewrite, prewarmed | d3ce0ac | d2bdff50cc53 | 0.859 | 3.78e-05 | - | False | True |
| corpus_a | 11 | 2026-09-24 | L@d3ce0ac cap + query rewrite + renditions (0% rendition coverage: all rejected/failed) | d3ce0ac | cf240adadd26 | 0.859 | 0.0005228 | - | False | True |
| corpus_a | 12 | 2026-09-25 | v3@0757c4b no librarian (the before on the same prod-rule import) | 0757c4b | c31f4d5139d3 | 0.793 | 0.0 | - | False | True |
| corpus_a | 13 | 2026-09-25 | Librarian v3@0757c4b C0 (all v3 flags off), assistant, approve-all x1 | 0757c4b | 3391a48f995a | 0.793 | 0.002298 | - | False | True |
| corpus_a | 14 | 2026-09-25 | Librarian v3@0757c4b C1 (LIBRARIAN_V3_TEMPORAL=True), assistant, approve-all x1 | 0757c4b | a04bb0588a3b | 0.793 | 0.001373 | - | False | True |
| corpus_a | 15 | 2026-09-25 | Librarian v3@0757c4b C2 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True), assistant, approve-all x1 | 0757c4b | b09748a8bc4a | 0.793 | 0.002339 | - | False | True |
| corpus_a | 16 | 2026-09-25 | Librarian v3@0757c4b C3 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True, LIBRARIAN_SECTION_TOP=4, LIBRARIAN_DOC_EPISODE=True), assistant, approve-all x1 | 0757c4b | 76bfbaee8885 | 0.793 | 0.00216 | - | False | True |
| corpus_a | 17 | 2026-09-25 | Librarian v3@0757c4b C4 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True, LIBRARIAN_SECTION_TOP=4, LIBRARIAN_DOC_EPISODE=True, LIBRARIAN_SAME_TOP=24, LIBRARIAN_DOC_TOP=8, LLM_JOB_CALL_CAP=60), assistant, approve-all x1 | 0757c4b | 4205012b88e6 | 0.793 | 0.00605 | - | False | True |
| corpus_a | 18 | 2026-09-25 | interim-97344eb (R3 candidate, pivot-s1-r3) flags off | 97344eb | 21db780f382e | 0.793 | 0.0 | - | False | True |
| corpus_a | 19 | 2026-09-25 | interim-97344eb query rewrite ON (D-102 gates), paced prewarm, D-094 chain | 97344eb | 03eb12a6e3ce | 0.826 | 5.83e-05 | - | False | True |
| corpus_a | 20 | 2026-09-25 | interim-97344eb query rewrite ON, paced prewarm, replicate | 97344eb | 03eb12a6e3ce | 0.837 | 6.28e-05 | - | False | True |
| corpus_a | 21 | 2026-09-25 | D-104 pilot Base: prod-rule import, no atomicize (fast path, rewrite off) | 16e0a2d | dbd7d4d79470 | 0.793 | 0.0 | - | False | True |
| corpus_a | 22 | 2026-09-25 | D-104 pilot A0: atomicize/1 claim children, parents excluded (HLM_PILOT_ATOMIC_EXCLUDE_PARENTS) | 16e0a2d | 98fe5f232e8b | 0.750 | 0.0 | - | False | True |
| corpus_a | 23 | 2026-09-25 | D-104 pilot A-oracle (CEILING, not a quality claim): A0 + oracle close of the labelled stale children | 16e0a2d | 167eda1cdb15 | 0.728 | 0.0 | - | False | True |
| corpus_a | 24 | 2026-09-25 | D-104 pilot A-v3: A0 + librarian v3 C2 (TEMPORAL+CLOSE), assistant, approve-all x1 — PARTIAL: OpenRouter credits exhausted (HTTP 402) after 1103/1564 corpus-A child-subject review jobs; corpus B unreviewed; 0 closes | c794456 | d59f361ac718 | 0.750 | 0.011948 | - | False | False |
| corpus_a | 25 | 2026-09-25 | D-104 pilot B-oracle (CEILING, not a quality claim): span-bound revision of the labelled stale spans | 16e0a2d | 3407072c7eb9 | 0.793 | 0.0 | - | False | True |
| corpus_b_dev | 1 | 2026-09-23 | Phase 0 baseline (D-057) | 16abaf7 | 30f79015c14e | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 2 | 2026-09-24 | main@1e57e08 re-measure, no librarian (W-E eval copy, 0008) | 1e57e08 | e4b32a157711 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 3 | 2026-09-24 | W2b librarian, role assistant, approve-all (G-E-TEMP/G-E-W2b eval copy) | 1e57e08 | b818796e0272 | 0.438 | 0.001368 | - | False | True |
| corpus_b_dev | 4 | 2026-09-24 | J@a65a8f5 (librarian judgement v2 branch), no librarian: the before on the same eval copy | a65a8f5 | f34647b3f244 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 5 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 1 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 0652a76c53a8 | 0.431 | 0.002622 | - | False | True |
| corpus_b_dev | 6 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 2 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 6a9428d10322 | 0.410 | 0.001374 | - | False | True |
| corpus_b_dev | 7 | 2026-09-24 | L@d3ce0ac pivot flags off (prod-rule import, source populated) | d3ce0ac | 4f1c79a64af4 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 8 | 2026-09-24 | L@d3ce0ac per-source top-5 cap (HLM_RETRIEVAL_SOURCE_CAP) | d3ce0ac | a2bc92b87343 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 9 | 2026-09-24 | L@d3ce0ac guarded English query rewrite, prewarmed (HLM_QUERY_REWRITE) | d3ce0ac | 016c33e768f0 | 0.556 | 3.94e-05 | - | False | True |
| corpus_b_dev | 10 | 2026-09-24 | L@d3ce0ac cap + query rewrite, prewarmed | d3ce0ac | d2bdff50cc53 | 0.542 | 3.78e-05 | - | False | True |
| corpus_b_dev | 11 | 2026-09-24 | L@d3ce0ac cap + query rewrite + renditions (0% rendition coverage: all rejected/failed) | d3ce0ac | cf240adadd26 | 0.549 | 0.0005228 | - | False | True |
| corpus_b_dev | 12 | 2026-09-25 | v3@0757c4b no librarian (the before on the same prod-rule import) | 0757c4b | c31f4d5139d3 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 13 | 2026-09-25 | Librarian v3@0757c4b C0 (all v3 flags off), assistant, approve-all x1 | 0757c4b | 3391a48f995a | 0.451 | 0.002298 | - | False | True |
| corpus_b_dev | 14 | 2026-09-25 | Librarian v3@0757c4b C1 (LIBRARIAN_V3_TEMPORAL=True), assistant, approve-all x1 | 0757c4b | a04bb0588a3b | 0.451 | 0.001373 | - | False | True |
| corpus_b_dev | 15 | 2026-09-25 | Librarian v3@0757c4b C2 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True), assistant, approve-all x1 | 0757c4b | b09748a8bc4a | 0.451 | 0.002339 | - | False | True |
| corpus_b_dev | 16 | 2026-09-25 | Librarian v3@0757c4b C3 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True, LIBRARIAN_SECTION_TOP=4, LIBRARIAN_DOC_EPISODE=True), assistant, approve-all x1 | 0757c4b | 76bfbaee8885 | 0.451 | 0.00216 | - | False | True |
| corpus_b_dev | 17 | 2026-09-25 | Librarian v3@0757c4b C4 (LIBRARIAN_V3_TEMPORAL=True, LIBRARIAN_V3_CLOSE=True, LIBRARIAN_SECTION_TOP=4, LIBRARIAN_DOC_EPISODE=True, LIBRARIAN_SAME_TOP=24, LIBRARIAN_DOC_TOP=8, LLM_JOB_CALL_CAP=60), assistant, approve-all x1 | 0757c4b | 4205012b88e6 | 0.451 | 0.00605 | - | False | True |
| corpus_b_dev | 18 | 2026-09-25 | interim-97344eb (R3 candidate, pivot-s1-r3) flags off | 97344eb | 21db780f382e | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 19 | 2026-09-25 | interim-97344eb query rewrite ON (D-102 gates), paced prewarm, D-094 chain | 97344eb | 03eb12a6e3ce | 0.486 | 5.83e-05 | - | False | True |
| corpus_b_dev | 20 | 2026-09-25 | interim-97344eb query rewrite ON, paced prewarm, replicate | 97344eb | 03eb12a6e3ce | 0.514 | 6.28e-05 | - | False | True |
| corpus_b_dev | 21 | 2026-09-25 | D-104 pilot Base: prod-rule import, no atomicize (fast path, rewrite off) | 16e0a2d | dbd7d4d79470 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 22 | 2026-09-25 | D-104 pilot A0: atomicize/1 claim children, parents excluded (HLM_PILOT_ATOMIC_EXCLUDE_PARENTS) | 16e0a2d | 98fe5f232e8b | 0.326 | 0.0 | - | False | True |
| corpus_b_dev | 23 | 2026-09-25 | D-104 pilot A-oracle (CEILING, not a quality claim): A0 + oracle close of the labelled stale children | 16e0a2d | 167eda1cdb15 | 0.361 | 0.0 | - | False | True |
| corpus_b_dev | 24 | 2026-09-25 | D-104 pilot A-v3: A0 + librarian v3 C2 (TEMPORAL+CLOSE), assistant, approve-all x1 — PARTIAL: OpenRouter credits exhausted (HTTP 402) after 1103/1564 corpus-A child-subject review jobs; corpus B unreviewed; 0 closes | c794456 | d59f361ac718 | 0.326 | 0.011948 | - | False | False |
| corpus_b_dev | 25 | 2026-09-25 | D-104 pilot B-oracle (CEILING, not a quality claim): span-bound revision of the labelled stale spans | 16e0a2d | 3407072c7eb9 | 0.458 | 0.0 | - | False | True |

## Librarian: bench_v2 (score = macro mean of per-task mean scores, %)

Per-task error rate = share of calls scoring < 0.8 (D-067), next to $/correct. Reference packs: t10_abstain.json b9ad32d0, t11_injection.json 9ac82095, t12_extract_review.json 1e250422, t5_supersession.json 5f2213a4, t5_supersession_private.json 81d81e31, t6_relation.json 26bd4cc1, t6_relation_private.json c50da4d0, t7_query_rewrite.json 6b7580c4, t7_query_rewrite_private.json 86a0576f, t8_consolidation.json c6087e2a, t8_consolidation_private.json f0eb6170, t9_risk_check.json 73928ad2

### gold raw (D-066 labels)

| model | run | reps | score | correct | err T5 | err T6 | err T7 | err T8 | err T9 | err T10 | err T11 | err T12 | JSON fail | $/correct | $/month | p50/p95 ms | commit | config | prompt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 95.8 | 94.5 | 0.0 | 0.0 | 13.3 | 3.3 | 16.0 | 6.7 | 0.0 | 10.0 | 0/200 | 0.006284 | 10.69 | 3859/8594 | 83fcc37 | 43581f1db920 | bench-v2-p1 |
| openai/gpt-6-luna-pro | final | 3 | 95.1 | 93.8 | 0.8 | 7.5 | 23.3 | 1.1 | 5.3 | 0.0 | 0.0 | 3.3 | 0/600 | 0.000616 | 1.04 | 5345/10994 | 83fcc37 | cd9cddf52a09 | bench-v2-p1 |
| openai/gpt-6-luna | calibration | 1 | 94.7 | 90.5 | 0.0 | 10.0 | 33.3 | 10.0 | 8.0 | 0.0 | 0.0 | 0.0 | 0/200 | 0.000328 | 0.53 | 3312/7839 | 83fcc37 | 93e1cbf715aa | bench-v2-p1 |
| openai/gpt-6-luna | final | 3 | 94.2 | 92.0 | 1.7 | 9.2 | 27.8 | 3.3 | 5.3 | 0.0 | 0.0 | 10.0 | 1/600 | 0.000154 | 0.26 | 3866/7781 | 83fcc37 | 93e1cbf715aa | bench-v2-p1 |
| openai/gpt-6-luna | hlm-bench port check | 1 | 93.8 | 89.5 | 0.0 | 12.5 | 40.0 | 3.3 | 8.0 | 0.0 | 0.0 | 10.0 | 1/200 | 0.000313 | 0.50 | 2912/7596 | bb03629 | None | bench-v2-p1 (sha256 be426d40e385) |
| deepseek/deepseek-v4.1-flash | final | 3 | 89.3 | 83.8 | 5.0 | 10.0 | 15.6 | 0.0 | 74.7 | 0.0 | 3.3 | 26.7 | 0/600 | 0.000240 | 0.36 | 1840/8683 | 83fcc37 | 0e122edf389b | bench-v2-p1 |

Best (raw): `openai/gpt-6-sol` (calibration) 95.8.

Not ranked (incomplete):

- `google/gemini-3.1-flash-lite` (final): 79.9 — 1 of 600 case x rep results missing; 1 infra_error

### gold adj-2+4c0651b4 (after the adjudication)

| model | run | reps | score | correct | err T5 | err T6 | err T7 | err T8 | err T9 | err T10 | err T11 | err T12 | JSON fail | $/correct | $/month | p50/p95 ms | commit | config | prompt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 96.6 | 97.5 | 0.0 | 0.0 | 0.0 | 0.0 | 16.0 | 6.7 | 0.0 | 0.0 | 0/200 | 0.006091 | 10.69 | 3859/8594 | 83fcc37 | 43581f1db920 | bench-v2-p1 |
| openai/gpt-6-luna-pro | final | 3 | 95.6 | 95.2 | 0.8 | 7.5 | 14.4 | 1.1 | 5.3 | 0.0 | 0.0 | 3.3 | 0/600 | 0.000607 | 1.04 | 5345/10994 | 83fcc37 | cd9cddf52a09 | bench-v2-p1 |
| openai/gpt-6-luna | calibration | 1 | 95.4 | 92.5 | 0.0 | 10.0 | 20.0 | 10.0 | 8.0 | 0.0 | 0.0 | 0.0 | 0/200 | 0.000321 | 0.53 | 3312/7839 | 83fcc37 | 93e1cbf715aa | bench-v2-p1 |
| openai/gpt-6-luna | final | 3 | 94.9 | 94.2 | 1.7 | 9.2 | 16.7 | 2.2 | 5.3 | 0.0 | 0.0 | 3.3 | 1/600 | 0.000151 | 0.26 | 3866/7781 | 83fcc37 | 93e1cbf715aa | bench-v2-p1 |
| openai/gpt-6-luna | hlm-bench port check | 1 | 94.4 | 91.5 | 0.0 | 12.5 | 26.7 | 3.3 | 8.0 | 0.0 | 0.0 | 10.0 | 1/200 | 0.000307 | 0.50 | 2912/7596 | bb03629 | None | bench-v2-p1 (sha256 be426d40e385) |
| deepseek/deepseek-v4.1-flash | final | 3 | 89.8 | 85.8 | 5.0 | 10.0 | 6.7 | 0.0 | 74.7 | 0.0 | 3.3 | 13.3 | 0/600 | 0.000235 | 0.36 | 1840/8683 | 83fcc37 | 0e122edf389b | bench-v2-p1 |

Best (adj-2+4c0651b4): `openai/gpt-6-sol` (calibration) 96.6.

Not ranked (incomplete):

- `google/gemini-3.1-flash-lite` (final): 80.5 — 1 of 600 case x rep results missing; 1 infra_error

Error metrics (adjusted gold):

- `openai/gpt-6-sol` (calibration): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1429, T10_false_answer_rate=0.1, T11_complied=0/10, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.63, T8_coverage=0.9827
- `openai/gpt-6-luna` (calibration): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=0/10, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=1.57, T8_coverage=0.9808
- `openai/gpt-6-luna` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=0.975, T8_avg_hallucinated_tokens=1.76, T8_coverage=0.9838
- `openai/gpt-6-luna-pro` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=1.61, T8_coverage=0.988
- `google/gemini-3.1-flash-lite` (final): T6_false_supersede_rate=0.0182, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=21/30, T7_identifier_hit_rate=0.9012, T8_avg_hallucinated_tokens=3.11, T8_coverage=0.9414
- `deepseek/deepseek-v4.1-flash` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.4286, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.1, T8_coverage=1.0
- `openai/gpt-6-luna` (hlm-bench port check): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=0/10, T7_identifier_hit_rate=0.963, T8_avg_hallucinated_tokens=2.17, T8_coverage=0.9747
