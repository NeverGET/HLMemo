# HLMemo leaderboard

Rendered from `eval/results/leaderboard.json` by `hlm bench leaderboard` (do not edit by hand).

## Rules (W-E optimize-to-best, D-058, D-067)

- (1) A change that sets a new best on one corpus must not lose more than 1 point on any other corpus's best.
- (2) A merge that drops any corpus below its best by more than 1 point needs a logged decision.
- (3) Every iteration records $/query (retrieval) or $/correct (librarian) next to its score: the production budget is set on price/performance after development (D-058).
- (4) Tuning uses corpus A and the Phase 5 project sets; corpus B's sealed subset is for confirmation only, never for tuning.
- (5) Librarian boards keep the per-task error rates next to $/correct (D-067); a gold change is a new gold version and every entry is re-scored under it (raw stays for history).

## Retrieval (best so far per corpus)

| corpus | primary | best | config | commit | embedder | librarian | $/query | secondary |
|---|---|---|---|---|---|---|---|---|
| corpus_a | hit@5 | 0.793 | drill_top5, budget 3000 | 16abaf7 | intfloat/multilingual-e5-small@614241f622f5 | none | 0.0 | mrr=0.662; l2_key=0.772; l1_key=0.533; temporal_l2_stale_first=0.533; negative_auc=0.792; tokens_query_mean=2940.2 |
| corpus_b_dev | evidence Recall@5 | 0.451 | drill_top5, budget 3000 | 16abaf7 | intfloat/multilingual-e5-small@614241f622f5 | none | 0.0 | hit@5=0.625; mrr=0.398; l2_key=0.556; stale_claim_l1_top3=0.25; tokens_query_mean=2965.2 |
| corpus_b_sealed | evidence Recall@5 | sealed | - | - | - | - | - | confirmation only (rule 4): scored once, in aggregate, at the final acceptance |

## Librarian: bench_v2 (score = macro mean of per-task mean scores, %)

Per-task error rate = share of calls scoring < 0.8 (D-067), next to $/correct.

### gold raw (D-066 labels)

| model | run | reps | score | correct | err T5 | err T6 | err T7 | err T8 | err T9 | err T10 | err T11 | err T12 | JSON fail | $/correct | $/month | p50/p95 ms | commit | prompt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 95.8 | 94.5 | 0.0 | 0.0 | 13.3 | 3.3 | 16.0 | 6.7 | 0.0 | 10.0 | 0/200 | 0.006284 | 10.69 | 3859/8594 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna-pro | final | 3 | 95.1 | 93.8 | 0.8 | 7.5 | 23.3 | 1.1 | 5.3 | 0.0 | 0.0 | 3.3 | 0/600 | 0.000616 | 1.04 | 5345/10994 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | calibration | 1 | 94.7 | 90.5 | 0.0 | 10.0 | 33.3 | 10.0 | 8.0 | 0.0 | 0.0 | 0.0 | 0/200 | 0.000328 | 0.53 | 3312/7839 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | final | 3 | 94.2 | 92.0 | 1.7 | 9.2 | 27.8 | 3.3 | 5.3 | 0.0 | 0.0 | 10.0 | 1/600 | 0.000154 | 0.26 | 3866/7781 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | hlm-bench port check | 1 | 93.8 | 89.5 | 0.0 | 12.5 | 40.0 | 3.3 | 8.0 | 0.0 | 0.0 | 10.0 | 1/200 | 0.000313 | 0.50 | 2912/7596 | bb03629 | bench-v2-p1 (sha256 be426d40e385) |
| deepseek/deepseek-v4.1-flash | final | 3 | 89.3 | 83.8 | 5.0 | 10.0 | 15.6 | 0.0 | 74.7 | 0.0 | 3.3 | 26.7 | 0/600 | 0.000240 | 0.36 | 1840/8683 | 83fcc37 | bench-v2-p1 |
| google/gemini-3.1-flash-lite | final | 3 | 79.9 | 77.3 | 20.8 | 15.0 | 43.3 | 11.1 | 20.0 | 0.0 | 80.0 | 17.2 | 2/599 | 0.001470 | 2.04 | 1758/8808 | 83fcc37 | bench-v2-p1 |

Best (raw): `openai/gpt-6-sol` (calibration) 95.8.

### gold adj-1+565f9d71 (after the adjudication)

| model | run | reps | score | correct | err T5 | err T6 | err T7 | err T8 | err T9 | err T10 | err T11 | err T12 | JSON fail | $/correct | $/month | p50/p95 ms | commit | prompt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 97.7 | 99.0 | 0.0 | 0.0 | 0.0 | 0.0 | 8.0 | 0.0 | 0.0 | 0.0 | 0/199 | 0.006009 | 10.71 | 3859/8600 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna-pro | final | 3 | 95.6 | 95.1 | 0.8 | 7.5 | 14.4 | 1.1 | 5.3 | 0.0 | 0.0 | 3.3 | 0/597 | 0.000609 | 1.04 | 5358/11007 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | calibration | 1 | 95.4 | 92.5 | 0.0 | 10.0 | 20.0 | 10.0 | 8.0 | 0.0 | 0.0 | 0.0 | 0/199 | 0.000322 | 0.54 | 3324/8069 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | final | 3 | 94.9 | 94.1 | 1.7 | 9.2 | 16.7 | 2.2 | 5.3 | 0.0 | 0.0 | 3.3 | 1/597 | 0.000151 | 0.26 | 3874/7781 | 83fcc37 | bench-v2-p1 |
| openai/gpt-6-luna | hlm-bench port check | 1 | 94.4 | 91.5 | 0.0 | 12.5 | 26.7 | 3.3 | 8.0 | 0.0 | 0.0 | 10.0 | 1/199 | 0.000307 | 0.51 | 2912/7658 | bb03629 | bench-v2-p1 (sha256 be426d40e385) |
| deepseek/deepseek-v4.1-flash | final | 3 | 90.2 | 86.3 | 5.0 | 10.0 | 6.7 | 0.0 | 70.7 | 0.0 | 3.3 | 13.3 | 0/597 | 0.000234 | 0.36 | 1846/9038 | 83fcc37 | bench-v2-p1 |
| google/gemini-3.1-flash-lite | final | 3 | 80.7 | 79.5 | 20.8 | 15.0 | 31.1 | 11.1 | 16.0 | 0.0 | 80.0 | 17.2 | 2/596 | 0.001433 | 2.05 | 1758/8808 | 83fcc37 | bench-v2-p1 |

Best (adj-1+565f9d71): `openai/gpt-6-sol` (calibration) 97.7.

Error metrics (adjusted gold):

- `openai/gpt-6-sol` (calibration): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1429, T10_false_answer_rate=0.0, T11_complied=0/10, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.63, T8_coverage=0.9827
- `openai/gpt-6-luna-pro` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=1.61, T8_coverage=0.988
- `openai/gpt-6-luna` (calibration): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=0/10, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=1.57, T8_coverage=0.9808
- `openai/gpt-6-luna` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.1905, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=0.975, T8_avg_hallucinated_tokens=1.76, T8_coverage=0.9838
- `openai/gpt-6-luna` (hlm-bench port check): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=0/10, T7_identifier_hit_rate=0.963, T8_avg_hallucinated_tokens=2.17, T8_coverage=0.9747
- `deepseek/deepseek-v4.1-flash` (final): T6_false_supersede_rate=0.0, T9_catch_rate=1.0, T9_false_warn_rate=0.4286, T10_false_answer_rate=0.0, T11_complied=0/30, T7_identifier_hit_rate=1.0, T8_avg_hallucinated_tokens=0.1, T8_coverage=1.0
- `google/gemini-3.1-flash-lite` (final): T6_false_supersede_rate=0.0182, T9_catch_rate=1.0, T9_false_warn_rate=0.2857, T10_false_answer_rate=0.0, T11_complied=21/30, T7_identifier_hit_rate=0.9012, T8_avg_hallucinated_tokens=3.11, T8_coverage=0.9414
