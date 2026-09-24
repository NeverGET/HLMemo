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
| corpus_a | hit@5 | 0.793 | #1 Phase 0 baseline (D-057) | 30f79015c14e | 16abaf7 | intfloat/multilingual-e5-small@614241f622f5 | 0.0 | 7a24ad29d981 | mrr=0.662; l2_key=0.772; l1_key=0.533; temporal_l2_stale_first=0.533; negative_auc=0.792; tokens_query_mean=2940.2 |
| corpus_b_dev | evidence Recall@5 | 0.451 | #1 Phase 0 baseline (D-057) | 30f79015c14e | 16abaf7 | intfloat/multilingual-e5-small@614241f622f5 | 0.0 | 5825dedbb693 | hit@5=0.625; mrr=0.398; l2_key=0.556; stale_claim_l1_top3=0.25; tokens_query_mean=2965.2 |
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
| corpus_b_dev | 1 | 2026-09-23 | Phase 0 baseline (D-057) | 16abaf7 | 30f79015c14e | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 2 | 2026-09-24 | main@1e57e08 re-measure, no librarian (W-E eval copy, 0008) | 1e57e08 | e4b32a157711 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 3 | 2026-09-24 | W2b librarian, role assistant, approve-all (G-E-TEMP/G-E-W2b eval copy) | 1e57e08 | b818796e0272 | 0.438 | 0.001368 | - | False | True |
| corpus_b_dev | 4 | 2026-09-24 | J@a65a8f5 (librarian judgement v2 branch), no librarian: the before on the same eval copy | a65a8f5 | f34647b3f244 | 0.451 | 0.0 | - | False | True |
| corpus_b_dev | 5 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 1 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 0652a76c53a8 | 0.431 | 0.002622 | - | False | True |
| corpus_b_dev | 6 | 2026-09-24 | Librarian judgement v2 (J@a65a8f5), role assistant, approve-all, --approve-rounds 2 (G-E-TEMP/G-E-W2b eval copy) | a65a8f5 | 6a9428d10322 | 0.410 | 0.001374 | - | False | True |

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
