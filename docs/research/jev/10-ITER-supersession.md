# 10 — Jev-Lab iteration round 1: `supersession` (run 2026-09-28)

Aggregates only: no memory item text appears here. The designs are in `docs/private/jev-lab/rack/designs/supersession_iter.py`. Everything else is private:
- the predictions (`rack/runs/supersession/<design>/`);
- the analysis in `rack/runs/supersession/_ss_iter/` (`analysis-all.json`, `final-tables.txt`, the scripts and the spend ledger);
- the per-run log, `rack/ITERATIONS.md`, rows `ss-r1`.

**The test split was never read.** `TEST-ACCESS.log` does not exist.

## Setup
- **Task.** Given an (older, newer) memory-item pair, does the newer item make a statement of the older item no longer current? The labels are `supersedes` and `not_supersedes`. Precision on `supersedes` matters most: a false link marks a true statement as outdated.
- **Data** (`SPLITS.md`, 287 pairs):
  - Train: 168 pairs, 95 supersedes / 73 not.
  - Dev: 59 pairs, 31 / 28.
  - Sub-labels of the negatives across train and dev: refines 49, unrelated 28, still_true 13, different_subject 8, wrong_direction 3.
- **Three slices with different styles.** Dev metrics are reported per slice.

  | Slice | supersedes / not (train+dev) | In dev | State the model sees |
  |---|---|---|---|
  | Queue-labelled (2 readers) | 5 / 90 | 0 / 25 | full bodies (≤ 1,500 chars), no spans |
  | Curated + oracle (+1 curated∩auto) | 104 / 3 | 25 / 2 | curated spans; the 3 negatives are span-less oracle records |
  | Auto-linker sample (SPLITS' "fair sub-benchmark", 1 reader) | 17 / 8 | 6 / 1 | spans |

  All 31 dev positives use the span style, and 25 of the 28 dev negatives use the body style. **Dev AUC therefore largely measures that split.**
- **Models.**
  - Jev: `typesafe/jev-1.13`.
  - `llm-v0`: `openai/gpt-6-luna` at low effort, with v0's state and question. It was run once on train and dev and is cached.
  - `llm-it3`: the same LLM with the best design's state and Choice, for parity. It was run on dev only, for the cascade.
- **Protocol.**
  - Platt calibration is fitted on train predictions.
  - The learned combiner is fitted on train. Its train predictions are out-of-fold (GroupKFold by subject group).
  - Thresholds and the design choice are made on dev.
  - **Honest reads.** (a) Thresholds chosen on train, applied to dev. (b) Dev-chosen thresholds applied to train; train is held out from the threshold choice, and the single-question designs fit nothing on it.
- **Metrics.**
  - **sup@95**: true supersedes auto-linked (P(sup) ≥ t) at the largest coverage whose precision is ≥ .95.
  - **not@95**: the same for `not_supersedes` (P(sup) ≤ t).
  - **ECE**: positive class, 10 bins, raw and after the train-fitted Platt.
- **Spend.** $0.105 by per-call sum, against a hard cap of $0.20. There were 1,540 new calls, 0 failed and 0 retries.

  | Run | Spend |
  |---|---|
  | v0 | $0.0069 |
  | llm-v0 | $0.0208 |
  | it1 | $0.0087 |
  | it2 | $0.0107 |
  | it3 | $0.0130 |
  | it4 | $0.0161 (it4-sig: $0, same request) |
  | it5 | $0.0152 |
  | llm-it3 | $0.0139 |

  The rack's guard also counts the key-usage delta, which includes the parallel agents on the shared key, and that delta stopped one run on their spend. The later runs used a driver (`_ss_iter/ssrun.py`) that guards on this round's own per-call sum and a round ledger. No rack file was changed.

## Iterations
Each row changes one thing relative to its parent. "dev sup@95" is in-sample: the threshold is chosen and read on dev. "sup@95 at train t" is the honest read. "$/dec" is Jev's billed $ per decision.

| # | Design (parent) | Change → hypothesis (playbook) | train AUC | dev AUC | dev ECE raw / Platt | dev sup@95 (FP) | dev sup@95 at train t (FP) | dev sup@90 | dev not@95 (FN) | train sup@95 | $/dec | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | v0 | baseline: one Noul; raw date strings; span, or else a 1,500-char body | .845 | .953 | .152 / .134 | 19/31 (1) | 0/31 | 22/31 | 23/28 (1) | 1/95 | 3.9e-5 | – |
| 1 | it1-time (v0) | S4: ISO dates plus a code-computed `time_order`; the raw date strings are removed because their format leaks the label source. Hypothesis: Jev stops reading provenance and rejects pairs that are not in time order | .849 | .961 | .226 / .129 | 20/31 (1) | 0/31 | 29/31 | 24/28 (1) | 2/95 | 4.0e-5 | kept as hygiene; scores shift down, ranking unchanged |
| 2 | it2-crit (it1) | W2/W12: the Noul criteria define both branches. **True**: a different value or decision, an explicit withdrawal, or an open item closed, even in part. **False**: refines; confirms or implements a standing decision; a prompt followed by its results; another subject or context; a proposal only; not later | .876 | .948 | .145 / .136 | 23/31 (1) | 12/31 (0) | 24/31 | 21/28 (1) | 40/95 | 4.8e-5 | kept: opens the high-precision band on train |
| 3 | **it3-choice** (it2) | P7/P6/W3: the same definitions as ONE Choice over 7 relations (`replaces`, `closes_open_item`, `refines`, `confirms_or_implements`, `different_subject`, `not_in_force`, `unclear`). P(sup) = replaces + closes_open_item + ½ unclear | **.902** | **.980** | .136 / .119 | **27/31 (1)** | **16/31 (0)** | 28/31 | 24/28 (1) | 41/95 | 5.9e-5 | **selected** |
| 4a | it4-sig-rel (it3) | D1/D2: add 5 signal Nouls (same thing, different value, explicit change, only adds, open item closed) and the code time sign, combined by an LR fitted on train | .906 | .983 | **.099 / .097** | 26/31 (1) | 13/31 (0) | 30/31 | 25/28 (1) | 43/95 | 7.2e-5 | ties on ranking, better calibrated, +24% cost |
| 4b | it4-sig (same request) | ablation: the LR on the signals and time only, without the Choice | .894 | .965 | .146 / .146 | 23/31 (1) | 16/31 (0) | 31/31 | 25/28 (1) | 46/95 | 7.2e-5 | the signals ≈ one good Choice (D3) |
| 5 | it5-body (it3) | S1: item bodies for every row (a 1,500-char window centred on the span) instead of spans; this also removes the span/body style difference | .867 | .941 | .148 / .103 | 24/31 (1) | 0/31 | 26/31 | 9/28 (0) | 0/95 | 7.5e-5 | worse: the minimal span state wins |
| 6 | llm-it3 (it3, LLM side) | cascade: the LLM gets it3's state and Choice (parity), so the middle band is judged with the same definitions | – | .934 | – | 7/31 | – | 23/31 | – | – | 2.4e-4 | ranks better than llm-v0 (.732), but answers `unclear` on 14/31 positives |

There was also an offline check at $0: a two-framing ensemble of the it2 Noul and the it3 Choice (mean logit, or min). It reached dev AUC .971 / .978, and train sup@95 fell to 0/95. No gain.

## The best design: `it3-choice`
**Request.** One request per pair:
- `state = {older: {title, date, text}, newer: {title, date, text}, time_order}`.
- `date` is an ISO date or "not dated".
- `text` is the curated span or quote, or else the item body truncated to 1,500 chars.
- One Choice. Each option has `what` and `not_for`. The instruction is: "What does `newer.text` do to the claims in `older.text`? `time_order` is computed in code from the dates; trust it over the field names."

**Cost and latency.**

| Model | $/decision | p50 | p95 |
|---|---|---|---|
| Jev it3 | $5.9e-5 | 374 ms | 772 ms |
| llm-v0 | $1.13e-4 | 2.5 s | 4.3 s |
| llm-it3 | $2.35e-4 | 3.5 s | 6.5 s |

**Dev metrics.** Train is in brackets.

| Metric | Value |
|---|---|
| AUC | .980 [.902] |
| ECE, raw / Platt (fitted on train) | .136 / .119 [.137 / .049] |
| acc at P ≥ .5 | .847 [.798]; at .5 the supersedes precision / recall is 1.00 / .71 |
| **sup@95** | 27/31 at P ≥ .315, 1 FP [41/95 at P ≥ .81, 2 FP] |
| sup@90 | 28/31, 3 FP |
| not@95 | 24/28 at P ≤ .16, 1 FN [28/73 at P ≤ .035, 1 FN] |
| By language at the dev t | en (49 rows): 23/27 linked, 1 FP; tr (10 rows): 4/4, 0 FP |
| Queue slice (25 negatives) | mean P .07; 0 FP at .5; 1 FP at the dev t (different_subject) |
| Curated/auto slice (31 positives / 3 negatives) | mean P .71 / .10; 9 FN at .5 |

**Honest reads.**
- **Dev thresholds applied to train.**
  - P ≥ .315 links 89 train pairs at precision **.90** (80/89).
  - P ≤ .16 rejects 74 at **.84** (62/74).
  - Even dev's zero-error threshold (P ≥ .435) gives .92 on train.
  - **Dev's 95% does not transfer.** Dev cannot certify 95%: with 0 errors, a one-sided 95% Wilson bound needs ≥ 52 links, and dev has 31 positives.
- **Train thresholds applied to dev.** P ≥ .81 gives 16/16 supersedes and P ≤ .035 gives 14/14 not, so 30 of the 59 dev pairs are decided at 100%.
- **Operating point that holds on both splits.** Auto-link at P(sup) ≥ ~.8: about 45–50% of the true supersessions at ≥ .95 precision on train and dev. Auto-reject at P ≤ ~.035. About half of all pairs are escalated.

## Style check: queue vs curated/auto, and the fair sub-benchmark
Train and dev are pooled. AUC is P(sup) vs gold within each slice. FP is the count of negatives at P ≥ .5.

| Design | Auto-linker sample, 17 / 8: AUC (FP) | Curated + oracle, 104 / 3: AUC | Queue, 5 / 90: AUC (FP) |
|---|---|---|---|
| v0 | .515 (5) | .994 | .812 (15) |
| it1-time | .474 (2) | .989 | .774 (5) |
| it2-crit | .493 (5) | .958 | .801 (5) |
| it3-choice | .507 (5) | .994 | .889 (2) |
| it4-sig-rel | .566 (6) | .990 | .829 (8) |
| it5-body | .460 (4) | .982 | .807 (2) |
| llm-v0 | .618 (5) | .796 | .690 (16) |

- **On the fair slice, every Jev design is at chance.** This slice is the automatic pairwise linker's own proposals. At it3's dev threshold, 14 of 19 links are correct (.74), which is the slice's base rate (17/25 = .68; the linker's precision was .69). At the train threshold (.81), 4 of 5 are correct.
  - **A pairwise Jev judge does not remove that linker's false links.** They are the same failure modes that made the linker propose them.
- **The high dev AUC is mostly curated span-style positives against queue body-style negatives.** The curated slice's AUC of .99 rests on 3 span-less negatives.
- **it5 removes the format difference and gets worse.** On the non-queue train rows (curated, oracle and auto; 90 / 8), AUC falls from .633 to .534. The curated span names the judged claim, and bodies dilute it. So Jev does use what the span carries, but these data cannot separate "the right claim is in view" from "span style".
- **Queue only.** At P < .5, it3 rejects 88 of the 90 queue negatives (v0: 75) and keeps 3 of the 5 queue positives. With 5 positives, this is weak evidence.

## Errors by sub-label
False `supersedes` at P ≥ .5, train and dev pooled:

| Sub-label (n) | v0 | it1 | it2 | it3 | it4-sig-rel | it5 |
|---|---|---|---|---|---|---|
| refines (49) | **11** | 4 | 3 | 2 | 5 | 2 |
| different_subject (8) | 5 | 1 | 3 | 1 | 3 | 0 |
| still_true (13) | 4 | 2 | 3 | **3** | 4 | 4 |
| wrong_direction (3) | 0 | 0 | 1 | 1 | 1 | 0 |
| unrelated (28) | 0 | 0 | 0 | 0 | 1 | 0 |

- **Refines were v0's main false positives** (11/49). The criteria and the Choice cut them to 2.
- **After it3, the remaining high-harm false links are still_true** (3/13). These are older claims that are current again because the newer item was itself reversed later, or that the newer item only restates or implements.
  - A pairwise state cannot show a later reversal (S7).
  - These pairs also score highest: the top-ranked train negative is a chain-reversal pair at P = 1.00. They set the ceiling of the 95% band.
- In the auto band (dev t95 applied to train, 89 links), the false links are still_true 3, refines 3, different_subject 2 and wrong_direction 1.

## Cascade
Three bands: Jev auto-links at P ≥ t_hi, auto-rejects at P ≤ t_lo, and the middle goes to the named reader. The data is dev (n = 59, 31 supersedes). Recall counts over all 31. "Curator" means pending: those items are counted as not linked.

| Jev design, thresholds | Middle band → | Jev decides | Escalated | sup precision | sup recall | $/dec | p50 |
|---|---|---|---|---|---|---|---|
| it3, dev t (.315 / .16) | llm-v0 | 53 | 6 | .933 | .903 | 7.2e-5 | 377 ms |
| it3, dev t | llm-it3 | 53 | 6 | .964 | .871 | 8.3e-5 | 377 ms |
| it3, dev t | curator | 53 | 6 | .964 | .871 | 5.9e-5 | 373 ms |
| **it3, train t (.81 / .035)** | llm-v0 | 30 | 29 | .840 | .677 | 1.2e-4 | 865 ms |
| **it3, train t** | llm-it3 | 30 | 29 | **1.000** | .581 | 1.8e-4 | 865 ms |
| **it3, train t** | curator | 30 | 29 | **1.000** | .516 | 5.9e-5 | 383 ms |
| it4-sig-rel, dev t | llm-v0 | 53 | 6 | .933 | .903 | 8.7e-5 | 392 ms |
| v0, dev t | llm-v0 | 44 | 15 | .880 | .710 | 6.9e-5 | 381 ms |
| LLM only | llm-v0 | – | 59 | .750 | .484 | 1.1e-4 | 2,527 ms |
| LLM only | llm-it3 | – | 59 | .889 | .258 | 2.4e-4 | 3,457 ms |

On train, the cascade (it3 with dev thresholds, middle → llm-v0) reaches P .889 / R .842, against .733 / .463 for llm-v0 alone.

- **Every cascade beats LLM-only by a wide margin.** On this task the LLM baseline is weaker than Jev: it says "no" to most curated part-scope links.
- **llm-v0 in the middle band lowers precision.** With the honest thresholds, it goes from 1.00 for the Jev band to .84. The LLM adds its own false links rather than fixing Jev's (G7).
- **llm-it3 keeps precision at 1.00 but adds only 2 links over the curator route**, at 3× the cost and ~9× the latency on escalated items. Its `unclear` answers are a usable "send to a curator" signal. It is not a decider.
- **Recommended shape (untested on test).** it3-choice as a fast, cheap first pass:
  - auto-link at P ≥ ~.8;
  - auto-reject at P ≤ ~.035;
  - send the middle to a curator, optionally pre-sorted by llm-it3.
  - Do **not** auto-link pairs proposed by an automatic linker on Jev alone until a chain check exists (see the next list).

## What did not help
1. **`time_order` from the offline dump (it1).**
   - Only decision-log rows have firm dates and numbers. A document's "cites dates up to X" can be a planned future date, and the file-change dates are missing from the dump. Using the row-level date strings instead would re-introduce the label-source leak.
   - So `time_order` is set for 62/227 pairs (27%), always by decision number. It never says "newer is earlier" and cannot catch the 3 wrong-direction pairs.
   - It moved scores down (dev acc .864 → .797) with no ranking gain. It is kept as hygiene only.
2. **Decomposition plus a learned LR (it4).**
   - It ties the single Choice on ranking (train AUC .906 vs .902; the signals alone reach .894). Its gain is calibration: dev ECE .099 vs .136, train OOF .043.
   - Some LR weights have the wrong sign for their meaning (`same_thing` −0.8, `only_adds` +0.7), so the LR is fitting quirks of this dataset.
   - It costs +24% for no ranking gain, so it was not selected (D3).
3. **Full bodies instead of spans (it5).** Train AUC −.035, train sup@95 0/95, dev not@95 9/28 (vs 24).
4. **Two-framing ensemble** (offline): no gain.
5. **llm-v0 as the middle-band decider**: it lowers `supersedes` precision.
6. **Dev-chosen 95% thresholds**: they give .90 on train.

## Next
- **Chain check before any link (S7).** Put into the state the items that later cite or reverse the newer item, or run it as a code check. Chain reversals are the top-scoring false links and are invisible to a pair.
- **Firm dates for documents.** Add the file-change and commit dates to the offline dump so that `time_order` covers documents, not only decision rows.
- **More labelled negatives from the automatic linker.** The fair slice has 8, and this dev split cannot certify 95%.
