# 10 — Jev-Lab iteration round 1: `relevance` (ranking a question's candidate excerpts)

Run 2026-09-28. Aggregates only: no question or candidate text. The designs, predictions, response cache and the
analysis JSON are private (`docs/private/jev-lab/rack/`: `designs/relevance_iter.py`,
`runs/relevance/*`, `runs/relevance/_relevance_iter-analysis.json`). The rules are in `09-JEV-PLAYBOOK.md`, and the
starting point is `05-TEST-RESULTS-T8.md`.

## Setup
- **Data.** `relevance.jsonl`, HLMemo run A. Each question has ≤ 30 candidates (handle, title, first 300 chars), with
  its V0 rank and luna-rerank position. The split is by question: **train 30 questions (29 with a gold candidate in
  the pool) and dev 10**. For relevance the question split is 30/10/10, not 37/12/11, because C covers only the 50
  answerable questions. **Test was never read**: there is no `TEST-ACCESS.log` entry, and every loader call refuses test.
- **Metric.** The position of the first gold candidate in each question's ordering, reported as gold@1/@4/@8 (counts
  of questions). Also AUC of the candidate scores, pooled and as a within-question mean. Ties keep V0 order.
- **Model.** `typesafe/jev-1.13` through the rack (the OpenRouter Decisions API). Every design is **one request per
  question**, with all candidates in the state and one question per candidate. The only exceptions are the second
  stage of R4 and the extra question in R6.
- **Baselines on the same questions.**
  - V0 order: train 9/21/25, dev 2/6/10.
  - LLM rerank (luna low, run A trace): train **24/29/29**, dev **9/10/10**; p50 2.9 s; $0.00061–0.00064 per question.
  - T8's J1 answers (offline): train 22/27/27, dev 7/8/9.
- **Selection rule.** Pick using train and dev, and prefer changes that help on both. With 29 and 10 questions, one
  question is 3.4 and 10 points.
- **Noise floor (measured).** R0 re-sent T8's J1 requests byte-for-byte (40/40 identical bodies). Between the two
  runs, **2/29 train and 1/10 dev questions flipped at gold@1; 0 flipped at gold@4**. The first-gold position changed
  on 4/29 and 2/10 questions, and the median of the largest per-candidate score change was 0.03 (on a 0–1 scale).
  **A difference of ≤ 2 (train) or ≤ 1 (dev) at gold@1 is within repeat noise.**
- **Rack change.** I added a minimal, backward-compatible group-design hook:
  - `Design.group`, `build_group` and `parse_group` in `designs/base.py`.
  - A group branch in `evaluate.run()`: one request per `_group`, with the cost split evenly over the group's rows.
    Every row carries the request latency. `--limit` counts groups. A sequential earlier stage can add its latency
    and cost.
  - Per-item designs are untouched. All 30 existing unit tests pass, plus 2 new ones (`tests/test_group.py`).
  - Budget: guarded on the own per-call sum (`BudgetGuard(key_fn=None)`), with a round cap of $0.20 summed over
    `SPEND.jsonl`. The key delta is not used, because other agents share the key.

## Iterations (one change each vs the current best; train | dev)

| # | Design | Hypothesis → change | gold@1/@4/@8 train | dev | AUC pooled train / dev | paired @1 vs R0 (only new / only R0), train · dev | p50 ms | $/question | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| R0 | `rel-j1` | Baseline: T8 J1 replica (5-level Score per candidate, state `{question, candidates:[{id,title,text}]}`) | 20/27/28 | 6/8/9 | .829 / .852 | – | 502 | 0.000369 | base |
| R1 | `rel-c1` | Criteria that separate "states the answer" from "same topic / overview / index / hub page / a prompt asking someone to review it" → Template C level wording | 18/26/28 | 7/7/9 | .819 / .867 | 0/2 · 1/0 | 585 | 0.000420 | no: worse on train, mixed on dev |
| R2 | `rel-j1qt` | Question type computed in code (procedure / why_or_history / current_state, a regex over EN+TR); a state field `question_type`, referenced by the top level, which names what counts as the answer for that type | 20/26/27 | 5/9/10 | .837 / .866 | 1/1 · 1/2 | 518 | 0.000394 | no: AUC slightly up on both, gold@k mixed |
| R3 | `rel-n1` | One Noul per candidate instead of a Score, in the same one-request shape (true/false mirror J1's level split) | 19/26/27 | 6/8/9 | .818 / .854 | 2/3 · 0/0 | 432 | 0.000325 | tie: equal within noise, 12% cheaper, 14% faster, better escalation signal on train |
| R4 | `rel-2s` | Two-stage: R3 as a cheap filter → a Score (J1 levels) on its top 10 in a second request (smaller state, S1) | 17/26/28 | 6/8/9 | .822 / .858 | 0/3 · 1/1 | 832 | 0.000464 | no: worse on train, 2× latency |
| R5 | `rel-j1path` | State format: candidates keyed by short ids (`c01`…), each question naming `candidates.cNN` by dot path (W4), with no handles | 17/26/27 | 7/8/10 | .822 / .852 | 0/3 · 1/0 | 502 | 0.000350 | no: worse on train |
| R6 | `rel-j1best` | Listwise relative pick (P4): R0 + one `best` Choice over the candidate ids (+ a `none` escape) in the same request; rank by EV + w·P(best), with **w fitted on train** (w = 0.25) | **21/27/27** | **7/8/9** | .832 / .854 | 2/1 · 1/0 | 518 | 0.000411 | **best**: the only change ≥ R0 on both splits, but within noise (see below) |

Sizes on dev: 10 questions × ~29 candidates. Latency is Jev's own round trip per question at concurrency 6. R4
includes its stage-1 request.

## Best design: R6 `rel-j1best` (read with care)
- **Result.**
  - gold@1/@4/@8 on train is 21/27/27 (R0: 20/27/28; LLM: 24/29/29). On dev it is 7/8/9 (R0: 6/8/9; LLM: 9/10/10).
  - AUC is .832/.875 on train and .854/.859 on dev (pooled / within-question).
  - p50 is 518 ms, and the cost is $0.00041 per question (+11% over R0).
- **Why it is only a weak win.**
  - Both gains (+1 train, +1 dev at gold@1) are inside the repeat-noise floor.
  - The train fit of w is flat: gold@1+gold@4 over train was 47–48 for every w from 0 to 4.
  - On dev, the +1 is already present at w = 0. It comes from this request's fresh Score read, not from the Choice.
  - Only on train does the Choice term itself add +1 (20 → 21).
- **Net.** No Jev-shaped single-request design in this round closed the gap to the LLM rerank measurably. The gap is
  4 questions at gold@1 on train and 2–3 on dev, and it is **systematic**. Averaging the scores of three
  already-paid reads (R0 + R1 + R2) did not help: train gold@1 was 18–19 vs 20. On train, J1 showed no position bias
  against late candidates: the mean expected level of gold candidates was flat across V0 positions 1–10, 11–20 and
  21–30. For that reason the order-averaging variant (`rel-j1rev`) was not run.
- **Practical pick.**
  - R6 if the best top-1 is wanted.
  - R3 (`rel-n1`) if cost and latency matter: it is equal within noise, at $0.00033 per question and p50 432 ms.

## Cascade: Jev first, the LLM rerank only when Jev is unsure
Per-question uncertainty signals:
- `top`: the highest candidate score.
- `top_p`: P(top level) of the top candidate.
- `margin`: the difference between the top two scores.
- `neg_entropy`: minus the entropy of the top candidate's level distribution.
- For R6 only: `neg_none` (minus P(none) of the `best` Choice) and `best_max`.

Escalating a question means using the recorded luna order for it. Escalated questions pay Jev + LLM, sequentially.
The thresholds were **chosen on train** (maximise gold@1 + gold@4 at a share ≤ 50%) and **applied unchanged to dev**.
The signal and design were selected by looking at both splits, so the dev row is optimistic. The test read is still
open.

| Setup | Split | Share to LLM | gold@1/@4/@8 | Latency p50 / mean (ms) | $/question |
|---|---|---|---|---|---|
| Jev-only R6 | train · dev | 0 · 0 | 21/27/27 · 7/8/9 | 518 / 555 · 490 / 513 | 0.00041 · 0.00043 |
| LLM-only (luna rerank) | train · dev | 1 · 1 | 24/29/29 · 9/10/10 | 2,883 / 2,975 · 2,876 / 2,786 | 0.00061 · 0.00064 |
| **R6 + `neg_entropy`** (train threshold) | train | 9/29 = 31% | **25/29/29** | 543 / 1,532 | 0.00060 |
| | dev | 3/10 = 30% | **7/9/10** | 555 / 1,605 | 0.00063 |
| R6 + `neg_none` | train · dev | 41% · 40% | 25/29/29 · 7/9/10 | 599 / 1,875 · 629 / 1,902 | 0.00067 · 0.00069 |
| R6 + `top` | train · dev | 28% · 10% | 25/29/29 · 6/8/9 | 543 / 1,433 · 518 / 785 | 0.00058 · 0.00049 |
| R3 + `top` (Noul p) | train · dev | 34% · 30% | 26/28/29 · 6/9/10 | 476 / 1,522 · 526 / 1,599 | 0.00054 · 0.00054 |
| R0 + `top` (T8's signal) | train · dev | 24% · 20% | 24/29/29 · 5/8/9 | 520 / 1,359 · 556 / 1,255 | 0.00053 · 0.00052 |

**R6 + `neg_entropy` at fixed escalation shares** (gold@1/@4):

| Split | 10% | 20% | 30% | 40% | 50% |
|---|---|---|---|---|---|
| train | 24/28 | 24/29 | 25/29 | 25/29 | 25/29 |
| dev | 7/8 | 6/8 | 7/9 | 8/10 | 8/10 |

Signal AUC for "Jev gets gold@1", question level, train / dev:
- R0: `top` .67 / .58; `neg_entropy` .69 / .54. For gold@4, `top` is .91 / .63.
- R3: `top` .87 / .63.
- R6: `neg_entropy` .71 / .62; `top_p` .67 / .55. For gold@4, `neg_entropy` is .91 / .75.

`margin` is uninformative or inverted on dev (.29–.50), which confirms T8.

**Reading.**
- **On train, the cascade closes the gap.** Sending ~30% of the questions to the LLM matches or beats LLM-only at
  gold@1 (25–26 vs 24) and reaches the ceiling at gold@4 (29/29 with R6).
- **On dev (10 questions), a train threshold buys +0 to +1 at gold@1 and +1 at gold@4.** Dev gold@1 stays 2 short of
  the LLM, for two reasons:
  - The signals rank dev questions poorly (AUC .55–.63).
  - The LLM also loses a question that Jev had right. Escalation is not free: R0 + `top` on dev *lost* a gold@1 hit
    (6 → 5).
- **Cost.** At ~30% escalation the cascade costs about what the LLM alone costs ($0.00060–0.00063 vs $0.00061–0.00064
  per question). Jev's inflated token count makes a J1-style request cost 60–67% of a luna rerank.
- **Latency is the gain.** The median stays at Jev's ~0.55 s, and the mean roughly halves (1.5–1.6 s vs 2.8–3.0 s).
  The ~30% of escalated questions pay ~3.4 s.

## What did not help
- Template C / "hub or prompt" criteria (R1): worse on train, mixed on dev. The train misses are mostly an older
  decision on the same topic, or a consult prompt. Naming these cases in the level texts did not move them.
- The question-type field with a type-specific top level (R2): AUC up slightly (+.008/+.014), gold@k unchanged within
  noise.
- A Noul instead of a Score (R3): equal within noise. Unlike T8's J2 (one pair per request, gold@1 24/50), the
  per-candidate Noul inside **one** request is as good as the Score. The one-request shape was what mattered in T8,
  not the primitive.
- Two-stage filter → Score on the top 10 (R4): −3 at gold@1 on train, and twice the latency. The Noul filter kept gold
  in the top 10 for 28/29 train and 9/10 dev questions, but the smaller state did not sharpen the top-1.
- Short keyed ids with dot-path references (R5): −3 on train and +1 on dev at gold@1; no gain.
- Averaging several reads (R0 + R1 + R2, offline, $0): no gain at gold@1, so the misses are not noise. Order
  averaging was not run, because train showed no position bias against later candidates.
- As a cascade signal, a second, differently framed read (R0 vs R3 top-1 disagreement) was weak: train 22/27 at 31%,
  dev 6/8 at 20%, at twice the Jev cost. `margin` and "Choice winner ≠ Score top-1" were weak too.

## Spend and run facts
- **Spend.** $0.0974 by the own per-call sum, against the $0.20 round cap. That is 7 runs × 40 questions = 280 Jev
  calls, with 0 LLM calls (the luna orders come from the data), 0 HTTP errors and 0 retries. Every cost comes from
  `usage.cost`.
- **Where it is logged.** Every run is a row in `rack/ITERATIONS.md` (fingerprints eb7c049352, b37d418ced, 190888fc6f,
  d1e3d22b7d, 75873ea5dd, a1c447b48b, 9ad95a70ca), and every invocation is in `SPEND.jsonl` (`command:
  relevance_iter`).
- **Reproduce** (from `docs/private/jev-lab/`):
  ```
  uv run --no-project --python 3.12 --with numpy --with scikit-learn python -m rack.designs.relevance_iter run --design <d> --splits train dev
  uv run --no-project --python 3.12 --with numpy --with scikit-learn python -m rack.designs.relevance_iter analyze --designs <d...> --cascade <d> [<d>+<other>]
  ```
- **Next.**
  - One final test read of R6 + `neg_entropy`, with the train threshold, next to R0.
  - More questions: dev's 10 cannot separate changes of ±1.
  - A code-side recency or current-state feature for the "older decision on the same topic" miss (S4). It was not
    tried here.
