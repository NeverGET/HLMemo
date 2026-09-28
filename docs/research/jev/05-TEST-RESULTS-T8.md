# T8 results: can Jev rerank the librarian's candidate excerpts? (run 2026-09-28)

Aggregates only. The scripts, the raw answers and the data stay in `docs/private/jev-tests/t8/` (private).

## Setup
- **Data.** HLMemo run A (pr-dev traces, 2026-09-27 archive), 50 answerable questions. The questions mix languages: 25 EN, 15 TR, 10 TR written in ASCII. Each question comes with the payload of the earlier LLM-rerank test: the top-30 candidates of the current order (mean 28.9), each as [handle, title, first 300 characters].
- **Gold.** A candidate is gold if its version id is one of the question's gold version ids in `funnel-A.jsonl`. All gold versions here are current (none superseded), so this test measures relevance and says nothing about supersession.
- **Metric.** Candidate level, DB-free: the position of the first gold candidate in each ordering.
- **Orderings.**
  - **V0:** the payload order.
  - **LLM:** `openai/gpt-6-luna` at low effort, from the 2026-09-27 run. Its top-8, then the rest in V0 order.
  - **J1:** one Jev request per question. `state = {question, candidates:[{id,title,text}]}`, with one 5-level Score per candidate: "How useful is the candidate with id \`<id>\` in \`candidates\` for answering \`question\`?". The levels run from Irrelevant to Essential. Ordered by the returned `score` (the expected level); ties keep V0 order.
  - **J2:** one Jev request per (question, candidate) pair. `state = {question, candidate:{title,text}}`, with one Noul: "Does this excerpt (\`candidate\`) contain information needed to answer the question (\`question\`)?". Ordered by probability; ties keep V0 order.
- **Model and endpoint.** `typesafe/jev-1.13` (served as `jev-1.13-20260917`) on `POST https://openrouter.ai/api/alpha/decisions`.
- **Prompting.** One wording per variant, fixed before the run, with no prompt tuning.
- **Calls.** 1,496 Jev calls plus 1 smoke call. There were 0 HTTP errors and 0 retries, and every answer was well-formed.

## Ranking quality (n = 50 answerable questions)
| Ordering | gold@1 | gold@4 | gold@8 | gold in pool (ceiling) | median first-gold pos |
|---|---|---|---|---|---|
| V0 (current order) | 15 | 34 | 44 | 49 | 3 |
| LLM rerank (gpt-6-luna, low) | **40** | **47** | **48** | 49 | 1 |
| **Jev J1** (1 request, 30 Scores) | **35** | **44** | 45 | 49 | 1 |
| Jev J2 (30 requests, 1 Noul each) | 24 | 39 | 46 | 49 | 2 |
| *(sensitivity)* J1 ordered by P(level ≥ 3) | 33 | 43 | 45 | 49 | 1 |
| *(sensitivity)* J1 + J2 combined | 28 | 43 | 45 | 49 | 1 |

- The LLM rerank's candidate-level numbers (40/47) match RANK.md's L1 row. V0's gold@8 is 44 here, against 45 in RANK.md, because RANK.md scores the final excerpt list, which includes the refine phase.
- **J1 recovers most of the LLM's gain over V0.**
  - At gold@1 it gains +20 of the LLM's +25 (80%).
  - At gold@4 it gains +10 of the LLM's +13 (77%).
- **J1 vs LLM, paired.** J1 is directionally worse, but the difference is not significant at n = 50.
  - gold@1: 4 questions only J1 gets right, 9 only the LLM gets right (McNemar p = 0.27).
  - gold@4: 1 vs 4 (p = 0.38).
  - First-gold position: J1 is better on 5 questions, worse on 12, and equal on 33.
- **J2 is clearly worse than the LLM.**
  - gold@1: 2 vs 18 (p < 0.001).
  - gold@4: 0 vs 8 (p = 0.008).
  - About 42% of J2's probabilities per question are ties, because the values are rounded to 0.01.
- **Separating gold from non-gold candidates** (AUC over all candidates; 0.5 = chance):

  | Ordering | Pooled AUC | Within-question mean AUC |
  |---|---|---|
  | J1 | 0.84 | 0.87 |
  | J2 | 0.81 | 0.82 |
  | V0 rank | 0.73 | – |

**Questions where Jev differs from the LLM (ids only)**
- **J1, gold@1.**
  - Jev only: PD-010, PD-019, PD-021, PD-028.
  - LLM only: PD-002, PD-005, PD-007, PD-015, PD-020, PD-036, PD-047, PD-049, PD-050.
- **J1, gold@4.**
  - Jev only: PD-045.
  - LLM only: PD-005, PD-036, PD-039, PD-047.
- **J1, first-gold position.**
  - Better than the LLM: PD-010, PD-019, PD-021, PD-028, PD-045.
  - Worse than the LLM: PD-002, PD-005, PD-007, PD-015, PD-020, PD-030, PD-036, PD-039, PD-041, PD-047, PD-049, PD-050.
- **J2, gold@4.**
  - Jev only: none.
  - LLM only: PD-005, PD-007, PD-013, PD-015, PD-016, PD-023, PD-036, PD-039.
- **J2, gold@1.**
  - Jev only: PD-021, PD-028.
  - LLM only: 18 questions.

**By category (gold@1 / gold@4)**
| Category | n | V0 | LLM | J1 | J2 |
|---|---|---|---|---|---|
| recent | 14 | 4 / 10 | 13 / 14 | 11 / 13 | 6 / 11 |
| temporal | 12 | 3 / 7 | 9 / 11 | 9 / 11 | 7 / 8 |
| procedure | 12 | 4 / 10 | 10 / 12 | 10 / 11 | 7 / 11 |
| multihop | 12 | 4 / 7 | 8 / 10 | 5 / 9 | 4 / 9 |

J1's gap to the LLM is concentrated in multihop questions: 3 of its 5 missing gold@1 hits are there.

## Turkish vs English
| Ordering | EN (n=25) gold@1 / @4 / @8 | TR+TR-ascii (n=25) gold@1 / @4 / @8 |
|---|---|---|
| V0 | 8 / 18 / 23 | 7 / 16 / 21 (in pool 24) |
| LLM | 21 / 24 / 25 | 19 / 23 / 23 |
| J1 | 18 / 22 / 23 | 17 / 22 / 22 |
| J2 | 13 / 20 / 22 | 11 / 19 / 24 |

- **Turkish questions do not do worse on this task.**
  - J1's gap to the LLM is −3/−2 (gold@1/@4) in EN and −2/−1 in TR.
  - The AUC of J1's score is 0.82 in EN and 0.87 in TR.
  - J2 is −8/−4 against the LLM in both languages.
- Caveat: the excerpts share vocabulary with the question, and n = 25 per side, so this is not evidence against B5, which T5 tests.

## Confidence vs success (question level; "hi/lo half" = split at the median confidence)
| Signal | Success | hi half | lo half | AUC | point-biserial r |
|---|---|---|---|---|---|
| J1 top-1 score | gold@4 | 24/25 | 20/25 | 0.80 | 0.39 |
| J1 top-1 score | gold@1 | 19/25 | 16/25 | 0.65 | 0.29 |
| J1 top-1 `confidence` field | gold@1 | 18/25 | 17/25 | 0.64 | 0.23 |
| J2 top-1 probability | gold@1 | 17/25 | 7/25 | 0.73 | 0.42 |
| J1 / J2 top1−top2 margin | gold@1 | 18/25 · 12/25 | 17/25 · 12/25 | 0.49 · 0.48 | ≈ 0 |

- The top score is a usable escalation signal: a low J1 top score flags most of the questions where gold misses the top-4.
- The margin between the first two candidates carries no signal.

## Latency and cost per question
| Ordering | Latency p50 | Latency p95 | Input tokens | Cost / question | Total (50 q) |
|---|---|---|---|---|---|
| LLM rerank (luna, low; 2026-09-27) | 2,883 ms | 4,554 ms | 3,985 (+239 output) | $0.00062 | $0.0309 |
| Jev J1 (1 request) | **422 ms** | 804 ms | 8,919 | **$0.00037** | $0.0187 |
| Jev J2 (30 requests, concurrency 10; wall time) | 1,136 ms | 1,310 ms | 13,955 (483 per request) | $0.00059 | $0.0293 |

- **Billing.** The billed cost is exactly the billed input tokens × $0.042/M, with no extra fee. The cost comes from `usage.cost` in each response; nothing is estimated.
- **Token counts are high.** Jev counts about 2.25× the o200k tokens of the same payload. The 5 level texts, repeated for each of the 30 Score questions, are the likely cause (not verified). As a result, **J1 is only about 1.7× cheaper than the luna rerank**, not orders of magnitude. J2 costs about the same as luna.
- **Latency is the real gain.** J1 is about 7× faster at the median than the LLM rerank.
- **Measurement notes.**
  - J1 ran at concurrency 4, and each latency is that request's own round trip.
  - J2 ran one question at a time, with all 30 candidates in parallel at concurrency ≤ 10.
  - J2's single-request p50 was 320 ms.
- **T8 spend.** $0.0481 by the own ledger (smoke call included), against the $0.25 cap. The key's usage delta over the same window was $0.0481 and includes a negligible amount from the parallel general-test agent.

## B11 verdict (rerank part)
B11 predicted that relevance scoring and reranking are among Jev's most promising uses for HLMemo. Its falsifier was "a poor showing on relevance".

**Supported, with a qualification.**
- **Relevance showing.** One J1 request per question reaches gold@1 35 and gold@4 44, against the LLM's 40 and 47. That is 77–80% of the LLM's gain over the current order, 7× faster, at about 60% of the cost. The gap to the LLM is not significant at n = 50 but points the same way: J1 is worse on 12 questions and better on 5, mostly in multihop questions.
- **Scoring pattern.** The per-pair Noul pattern (J2) is clearly weaker, so for this task one request with many Score questions over a shared state works better than isolated pairwise judgements.
- **Cost.** The cost advantage over a cheap reasoning LLM is small, because Jev's billed token count is inflated. The speed advantage is large.
- **Escalation.** The top score's confidence is informative enough (AUC 0.80 for gold@4) for a cascade: Jev first, and the LLM only for questions with a low top score.
- **Not tested here.** Supersession, the other half of B11. All gold versions here were current, and J1 matched the LLM on the "temporal" category (9/11 on both metrics).
