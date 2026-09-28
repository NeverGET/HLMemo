# 10 — Jev-Lab iteration round 1: `fact_presence` (run 2026-09-28)

Aggregates only: no question, answer or fact text appears here. The designs are in `docs/private/jev-lab/rack/designs/fact_iter.py`. The predictions, the analysis JSON (`rack/runs/fact_presence/_fact_iter/analysis-all.json`) and the per-run log (`rack/ITERATIONS.md`, rows `fp-iter*`) are private. **The test split was never read.**

## Setup
- **Task.** Given (question, answer, one must_mention fact), is the fact present, partial or absent in the answer? The data is described in `SPLITS.md`: train has 386 items over 36 questions, dev has 143 items over 11 questions. Dev holds 101 present, 19 partial and 23 absent items; 78 are English and 65 Turkish (47 tr, 18 tr-ascii).
- **Models.**
  - Jev: `typesafe/jev-1.13`.
  - LLM baseline: `llm-v0`, i.e. `openai/gpt-6-luna` at low effort, with the same state and question as v0. It was run once on train and dev and is cached.
  - Judge v1: the attached verdicts, known for 92 dev and 219 train items.
- **Protocol.**
  - Platt calibration of P(present) and the learned combiners are fitted on train only. The combiners' train predictions are out-of-fold, grouped by question.
  - Thresholds and the design choice are made on dev only.
  - **Honest variant.** Because dev is small, every cascade is also reported with thresholds chosen on train and applied to dev. Dev-chosen thresholds evaluated on dev are optimistic.
- **Cascade.** Jev decides alone when its calibrated P(present) is ≥ t_hi (auto-present) or ≤ t_lo (auto-not-present).
  - Each side is chosen for ≥ 95% precision with at least 10 items, and t_hi ≥ .5 > t_lo. Thresholds are either global or per language family (en / tr + tr-ascii).
  - The remaining items go to `llm-v0`. An escalated item pays Jev and the LLM, sequentially.
  - The 3-class result uses Jev's partial/absent argmax on the auto-not-present side.
- **Instructions language.** Instructions and criteria stay in English and the answer stays in its original language (L2). This was held constant; no Turkish-instruction arm was run.
- **Spend.** $0.139 by per-call sum, against a cap of $0.30:

  | Run | Spend |
  |---|---|
  | v0 | $0.0136 |
  | llm-v0 | $0.0416 |
  | fi1 | $0.0278 |
  | fi3 | $0.0261 |
  | fi2 | $0.0159 |
  | fi5 | $0.0139 |
  | fi4, fi6 | $0 (cache) |

  3,094 new calls; 0 failed and 0 retries.

## Iterations
Metric definitions:
- **dev AUC**: P(present) vs rest.
- **acc@.5**: binary present-vs-rest accuracy at the train-Platt-calibrated 0.5.
- **Honest cascade**: thresholds chosen on train and applied to dev. It shows the share of dev items Jev decides alone and the precision of those decisions, first with global thresholds, then with per-language thresholds.
- **Train columns**: out-of-fold for the learned designs.

| # | Design (parent) | Hypothesis (playbook) | dev AUC | dev 3-cls acc / F1 | dev acc@.5 | train AUC / F1 | Honest cascade, global | Honest cascade, per language | Jev $/dec | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | v0 | baseline: one Choice present/partial/absent | .969 | .888 / .812 | .902 | .975 / .767 | 90.2% @ .938 | 90.2% @ .938 | 2.8e-5 | misses 95% |
| 1 | fi1-crit (v0) | W2/W3: structured criteria (what / not_for / examples) name the exceptions: paraphrase, other language, id prefix and number format count as present; partial = one component missing | .960 | .867 / .801 | .895 | .975 / .778 | 82.5% @ .958 | 76.9% @ .964 | 5.3e-5 | no gain, 2× tokens |
| 2 | fi3-sig (v0) | D1/D2: 4 signal Nouls in one request (stated whole / main point / contradicted / addressed) plus a learned multinomial LR | .955 | .846 / .706 | .895 | .975 / .769 | 86.7% @ .968 | 86.0% @ .976 | 5.0e-5 | worse: partial collapses into absent |
| 3 | fi4-siglit (fi3) | S4: plus code-side literal features in the LR (hash prefix, ids, numbers across formats, paths, word coverage, language, empty answer) | .964 | .874 / .742 | .923 | .961 / .744 | 81.8% @ .983 | 77.6% @ .982 | 5.0e-5 | binary ↑, 3-class ↓, not significant |
| 4 | **fi2-esc** (v0) | P6: explicit escape option. v0's options are unchanged; `absent` is split into `not_addressed` and `contradicted`, and both map back to absent | .963 | **.909 / .835** | **.930** | .974 / .768 | **87.4% @ .952** | **85.3% @ .967** | 3.0e-5 | **selected** (about equal to v0) |
| 5 | fi5-min (v0) | S1: minimal state {fact, answer}, question dropped | .944 | .874 / .771 | .909 | .974 / .800 | 90.2% @ .923 | 90.2% @ .922 | 2.6e-5 | dev worse (train better) |
| 6 | fi6-stack (fi4) | D2: plus the fi2-esc Choice's 4 option probabilities as LR features (stacking) | .965 | .860 / .725 | .937 | .959 / .754 | 79.0% @ .982 | 79.0% @ .982 | 8.0e-5* | no gain |

\* Two cached requests per item. A production version would merge them into one, so this cost is an upper bound.

**Order and rebasing.** Criteria were tried first because the playbook ranks them as the main lever (W2). Iteration 1 did not beat v0, so the later Choice iterations (4 and 5) were rebased on v0. Each row therefore changes one thing relative to its parent.

## The best design: `fi2-esc`
It is v0's Choice with an explicit escape option: `not_addressed` (the answer says nothing about the fact) next to `contradicted` (a different value or outcome). The state is unchanged: {question, fact, answer}. It was selected on dev as the best single design: the best 3-class result, the best calibration, and the cheapest cost after v0. **It does not significantly beat v0.** In a paired bootstrap by question on dev, 3-class accuracy moves by +.020 [.000, .049] and F1 by +.020 [−.032, +.073]. On train, the discordant items split 6 vs 5.

**Dev metrics.** Train is shown in brackets.

| Metric | Overall | en (n=78) | tr + tr-ascii (n=65) |
|---|---|---|---|
| (a) AUC, present vs rest | .963 [.974] | .952 [.969] | .987 [.978] |
| (a) ECE of P(present): raw / Platt, train-fitted (overall: global fit; slices: per-language fit) | .030 / .028 [.057 / .037] | .084 / .091 | .067 / .076 |
| (a) accuracy at calibrated .5 | .930 [.920] | .936 | .923 |
| (b) 3-class accuracy / macro-F1 | .909 / .835 [.868 / .768] | .897 / .829 | .923 / .819 |
| (e) Jev $/decision; p50 / p95 latency | 3.0e-5; 360 / 504 ms | | |

- The raw-language 3-class accuracy is en .897, tr .936 and tr-ascii .889 (n = 78 / 47 / 18).
- The ECE noise floor is a few hundredths at these sizes (C3 puts it at ≈ .045 for a perfect model at n = 60), so none of the ECE differences here is meaningful. A per-language Platt refit did not improve on the global one (.038 vs .028 on dev), consistent with C4.

## (c) Cascade: `fi2-esc` → `llm-v0` on dev, vs LLM-only and judge v1
| Variant (dev, n=143) | Auto share (en / tr) | Auto precision | Binary acc (en / tr) | 3-class acc / F1 | $/decision | p50 / p95 ms |
|---|---|---|---|---|---|---|
| Dev-chosen, per language (the protocol; optimistic) | 88.8% (89.7% / 87.7%) | .984 (91/93 present, 34/34 not present) | **.972** (.962 / .985) | **.951 / .910** | 4.1e-5 | 370 / 3119 |
| Dev-chosen, global | 88.1% | .952 | .958 | .930 / .871 | 4.3e-5 | 366 / 3106 |
| **Honest: train-chosen, per language** | 85.3% (84.6% / 86.2%) | .967 (en .939 / tr 1.00) | **.965** (.949 / .985) | .944 / .898 | 4.5e-5 | 370 / 3159 |
| Honest: train-chosen, global | 87.4% | .952 | .958 | .930 / .875 | 4.3e-5 | 370 / 3100 |
| Jev alone (fi2-esc) | 100% | – | .930 | .909 / .835 | 3.0e-5 | 360 / 504 |
| LLM only (llm-v0) | 0% | – | .965 (.949 / .985) | .937 / .885 | 8.8e-5 | 2501 / 3765 |

- **Judge v1 comparison on the same 92 dev items** (binary accuracy):

  | System | Accuracy |
  |---|---|
  | Judge v1 | .859 (en .804 / tr .927) |
  | LLM only | .957 |
  | Cascade, honest per language | .957 |
  | Jev alone | .924 |

  On the 219 train items with a verdict, judge v1 scores .785 (en .811 / tr .759).
- **Reading.** With per-language thresholds, Jev decides about 85–89% of fact checks alone at ≥ 95% precision. The cascade matches LLM-only binary accuracy (.965 honest) and beats it on 3-class (F1 .898–.910 vs .885), at about half the cost (4.1–4.5e-5 vs 8.8e-5) and with a 370 ms vs 2.5 s median. The escalated 12–15% keep p95 near the LLM's.
- **Thresholds**, on per-language Platt P(present), as t_hi / t_lo:

  | Choice | en | tr |
  |---|---|---|
  | Dev-chosen | .887 / .499 | .551 / .005 |
  | Train-chosen (honest) | .733 / .209 | .587 / .063 |
- **English is the bottleneck.** With honest thresholds, the English auto band reaches .939 precision, below the target, while the Turkish band reaches 1.00. v0 shows the same pattern: its honest cascade reaches only .938 overall.

## The judge v1 failure modes, revisited
- **Under-scoring correct answers is a judge problem, not a Jev problem.** The table gives present recall on the items where judge v1 has a verdict:

  | Slice | Judge v1 | v0 | fi2-esc | llm-v0 |
  |---|---|---|---|---|
  | train en | 52/73 | 67/73 | 69/73 | 62/73 |
  | train tr | **39/62** | 60/62 | 59/62 | 54/62 |
  | dev en | 21/31 | 31/31 | 31/31 | 31/31 |
  | dev tr | 25/27 | 24/27 | 26/27 | 27/27 |

  Jev does not lose Turkish facts checked against English facts.
- **Jev's own error runs the other way: it over-credits.** It reads partial or absent facts as present. On train Turkish, v0 and fi2-esc make 15–16/46 such false accepts, against 1/46 for the LLM. Most of these sit at moderate confidence, so the cascade routes them. The judge's false-accept rate (3/46) is low partly by construction: the verify and verifyO rows were selected because the judge said "missing".
- **Contradiction.** This is measured per answer, on the same 97 train / 37 dev answers that judge v1 scored, against reader labels (17 / 5 true).
  - Judge v1 flags 42 train answers (15 true positives) and 18 dev answers (5 true positives): 2.5× and 3.6× over-flagging.
  - fi3's `contradicted` Noul at ≥ .5 on any must_mention fact flags 22 train (11 TP) and 11 dev (4 TP) answers: 1.3× and 2.2×.
  - The fi2-esc argmax `contradicted` under-flags: 7 train (3 TP) and 7 dev (2 TP).
  - Caveats: the threshold is untuned, and Jev sees only the must_mention facts while the readers judged the whole answer. Treat this as a lead for the contradiction decision, not a result.

## What did NOT help
1. **Criteria that name the exceptions (fi1, W2/W3).** Jev was not under-scoring paraphrases or Turkish (v0 dev present recall 96/101), so the "these count as present" exceptions had nothing to fix. The stricter `partial` moved errors instead of removing them: train partial→present fell from 24 to 17, but dev present→partial rose from 5 to 10. Dev AUC dropped (.969 → .960), mostly in English (.963 → .928), and the tokens doubled.
2. **Signal decomposition plus a learned combiner (fi3, D1/D2).** The out-of-fold train AUC equals v0 (.975). On dev the LR folds partial into absent (F1 .706). This is a case of D3: presence is one judgment, not independent factors.
3. **Code literal features (fi4, S4).** Dev binary accuracy improved (.923 Platt, .937 raw), but the out-of-fold train AUC fell (.975 → .961). Literal hits are fact-specific and do not transfer across question folds. The cascade difference vs v0 is not significant (+.001 [−.017, +.023]).
4. **Minimal state (fi5, S1).** Dropping the question helped on train (F1 .800) and hurt on dev (AUC .944, honest cascade .923 precision). Keep the question in the state.
5. **Stacking the Choice with the signals and literals (fi6, D2).** It did not beat the single Choice on any dev metric that matters, at up to 2.6× the cost.
6. **Per-language calibration.** It gave no ECE gain over one global Platt fit. Per-language *thresholds* do matter: the Turkish band can be looser, and English needs a stricter t_hi. But the Turkish auto-not-present side rests on 16 dev items.

## Caveats
- **Dev is small**: 143 items, 11 questions, 42 not-present. Several designs were compared on it, so the "selected" design carries selection optimism. The honest (train-chosen) cascade rows are the better estimate, and the test read is the real one.
- **`partial` is the noisy class.** Reader conflicts sit on the present↔partial and partial↔absent borders, so 3-class F1 has a label-noise ceiling.
- **Selection bias.** The set is judge-disputed-heavy (70% present), so live traffic will differ. Thresholds must be re-measured on traffic (G5).

## Next
1. Read test once, with `fi2-esc` plus per-language thresholds. Both are fixed now from dev, and the honest train-chosen set is the fallback. This is the owner's call.
2. **Untested next hypothesis.** The residual error is over-crediting multi-part facts in which one component is missing. Split the fact into its components in code and ask one Noul per component, so that "every component stated" is computed in code (S4 applied to the fact's structure).
3. Add a disagreement check on the confident English band (G7). The LLM cannot fix Jev's confident errors.

## Final test read (2026-09-28, run once, pre-registered)
**Protocol.**
- The configuration was frozen before any test call, in `rack/runs/fact_presence/_fact_iter/prereg-test.json` (sha256 `4cd35f7052ab…`), and recorded in the ITERATIONS notes at 18:30Z:
  - Jev: `fi2-esc@1.0`.
  - Calibration: Platt of P(present) per language family, fitted on train.
  - Primary thresholds: per language, chosen on **train**.
  - Secondary thresholds: per language, chosen on dev.
  - Uncertain band: `llm-v0`.
- The frozen evaluator was dry-run on dev and reproduced the dev numbers above exactly.
- Test was then read once: one `--final` run each for `fi2-esc` and `llm-v0`, then one evaluation. `TEST-ACCESS.log` shows 3 entries.
- Nothing was changed after test.
- Cost: $0.0153 by per-call sum ($0.0038 Jev + $0.0115 LLM), 268 calls, 0 failures.

**Test set.** 134 items over 11 questions: 95 present, 15 partial, 24 absent. By language family: en 68, tr 66. Test contains no tr-ascii items. Judge v1 verdicts are known for 80 items.

| Test (n = 134) | Auto share (en / tr) | Auto precision | Binary acc (en / tr) | 3-class acc / macro-F1 | $/decision | p50 / p95 ms |
|---|---|---|---|---|---|---|
| Jev alone (`fi2-esc`) | 100% | – | **.940** (.941 / .939) | .881 / .748 | 2.8e-5 | 379 / 464 |
| **Cascade, primary** (train thresholds) | 81.3% (75.0% / 87.9%) | **.972** (present 80/81, not present 26/28) | .925 (.897 / .955) | .888 / .803 | 4.7e-5 | 394 / 3570 |
| Cascade, secondary (dev thresholds) | 79.1% (73.5% / 84.8%) | .991 (74/74, 31/32) | .948 (.926 / .970) | .896 / .815 | 4.8e-5 | 393 / 3810 |
| LLM only (`llm-v0`) | 0% | – | .873 (.882 / .864) | .813 / .735 | 8.6e-5 | 2514 / 3900 |

- **Jev alone, detail.**
  - AUC .981 (en .976 / tr .987).
  - ECE of P(present): raw .089, calibrated .075 (en .075 / tr .091).
  - Top-label ECE: .070.
  - Binary acc: .940 at the calibrated .5 and .925 at the raw argmax.
  - 3-class F1: en .764 / tr .727.
- **Auto band.** Jev decides 81% of test items alone at .972 precision (106/109), so the 95% target held on test in both languages: en 50/51, tr 56/58.
- **The cascade did not beat Jev alone on test.** The LLM was the weaker system on test. It read 15 of the 95 present facts as partial, the same under-scoring as judge v1. On the 25 escalated items, the LLM was right on 18 and Jev on 20.
- **Cascade vs LLM-only.** The primary cascade beats LLM-only by +.052 in binary accuracy, 95% CI [−.009, +.122] (bootstrap by question, 11 questions). The cascade's own binary accuracy has CI [.880, .974].
- **Judge v1 on the same 80 items** (binary accuracy):

  | System | Binary acc | 3-class F1 |
  |---|---|---|
  | Judge v1 | .788 (en .929 / tr **.632**); present recall 31/47, false accepts 1/33 | – |
  | Jev alone | .938 | .784 |
  | Cascade, primary | .925 | .844 |
  | Cascade, secondary | .963 | .853 |
  | LLM only | .838 | .728 |

  Turkish is again where judge v1 under-scores.
- **Dev vs test.**
  - Jev: AUC .963 → .981, binary .930 → .940, 3-class F1 .835 → .748. Partial remains the weak class: on test, 6 partial facts read as present and 3 as absent, out of 15.
  - The LLM dropped from .965 to .873 binary. With a single read on 11 questions, the LLM's dev advantage did not transfer.

**Verdict.** For "is this fact fully present", `fi2-esc` with train-fitted per-language calibration is at least as good as the LLM baseline on held-out questions, at a third of the cost and about 7× faster:
- Binary accuracy is .940 vs .873, and 3-class F1 is .748 vs .735.
- Its confident band met the 95% precision target on test in both languages (.972 overall).
- Escalating the uncertain ~19% to `llm-v0` did not improve accuracy on test (.925 vs .940).

The escalation target therefore needs a stronger or differently framed LLM prompt before a cascade pays off (G7). The 3-class `partial` boundary is the remaining weakness for both systems.
