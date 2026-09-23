# bench v2 gold adjudication (adj-1, 2026-09-24)

D-067 asked for a gold adjudication of the 11 cases that the ceiling model (`openai/gpt-6-sol`, calibration
run `bench/results/20260923-190032-v2`) missed (score < 0.8), before any threshold is fixed on bench v2.
This file records both judgments per case, the decision, and the re-scored numbers. It is public-safe: case ids,
verdicts and reasons only. No private-pack text (`T7P-*`, `T8P-*` come from `docs/private/bench-v2/`) is quoted.

The decisions are machine-applied by the gold overlay `src/hlmemo/bench/adjudication_v2.json` (version
`adj-1`, content hash in every report: `adj-1+565f9d71`). `hlm bench` scores with the adjusted gold by default;
`--gold raw` reproduces the D-066 numbers exactly. The pack files themselves are unchanged (version 1).

## Protocol

1. **First judgment** (implementer): the model-visible input, the rules the model was given, the gold with its
   rationale, the ceiling model's output, and the outputs of the other 4 models (13 runs per case).
2. **Second, blind judgment** (a fresh agent, separate context): only the rules plus the model-visible input.
   Stage A: it derived its own answer and wrote it down before it saw anything else. Stage B: it rated
   anonymised candidates in random order, one of them the gold-conforming answer and one the ceiling model's
   answer, without being told which was which. For query rewrites, stage B also showed the hidden source passage and the scoring key. It never saw the
   gold rationale, the case id, the model identity, or the first judgment.
3. **Verdicts.** *gold correct*: the output violates the rules the model was given, so it is a model error.
   *gold too narrow*: the output is correct under those rules, and the key rejects it for a reason the rules do
   not state (morphology, an equally valid lesson set, a legitimate hypothesis counted as a distractor). The
   fix is to widen the gold. *ambiguous*: the model-visible input underdetermines the answer. The case is
   either dropped, or the underdetermined component is marked optional. When the judgments disagree the case
   is ambiguous (DESIGN §3).
4. Adjustments apply to every model's results, not only the ceiling model's. A rule-level ambiguity applies
   to the whole family.

## The 11 cases

| case | family / tier / pack | ceiling raw → adj | first judgment | second (blind) judgment | agree | decision (overlay) |
|---|---|---|---|---|---|---|
| T9-01 | risk_check / easy / public | 0.65 → 1.0 | gold too narrow: the plan drives the whole deploy through an attached `ssh … bash -s` pipe, so the detached-deploy lesson L16 is a legitimate second lesson | stage A picked {L01, L16} itself; stage B: both {L01} and {L01, L16} acceptable, with L01 required and L16 optional | yes | **gold too narrow**: also accept {L01, L16} |
| T9-03 | risk_check / medium / public | 0.65 → 1.0 | gold too narrow (lean): L20 names "test runs", and a fresh terminal lacks every per-run env var; the extra lesson is conditional, not wrong | stage A {L03, L09}; stage B: {L03} acceptable, {L03, L20} borderline-acceptable ("an over-warn, not a wrong warn"); L09 an equally good second id | partial | **gold too narrow**: L03 stays required; also accept {L03, L20}, {L03, L09} and {L03, L09, L20} |
| T9-15 | risk_check / medium / public | 0.65 → 0.65 | model error: L24 (migration head) matches only the word "migrations"; the plan shows no upgrade command | stage A {L25}; stage B: {L25, L24} borderline, leaning not acceptable (keyword overlap) | yes | **gold correct** |
| T9-25 | risk_check / easy / public | 0.0 → 0.0 | model error: a false warning (L14, pipeline exit status) triggered by the word "piped"; the action is a CLI output-format change | stage A: warn false, []; stage B: only the silent answer is acceptable | yes | **gold correct** |
| T10-07 | answer_or_abstain / medium / public | 0.0 → dropped | gold correct under the literal contract (the asked date is not in the snippets → `answer = null`); but the output is a grounded "no date is set yet", a contract violation rather than an invented fact | stage A null, calling a grounded "no date yet" defensible; stage B: null acceptable, the model's answer borderline-acceptable, "the gold should accept both" (false-premise question) | **no** | **ambiguous**: dropped; rewrite it (or make the false-premise rule explicit) before re-adding |
| T12-10 | extract_review / hard / public | 0.7829 → 0.8 | the `counts` object contradicts the model's own findings list, a genuine model error; the 3 omitted files are named only as "the fix also touched", which fits a defensible reading of "files = file paths named for that finding" | stage A counts = gold exactly; files: included them but "both readings should be accepted"; stage B: files both acceptable, counts gold only | yes | **gold correct** for counts; the file-list rule is too narrow, so both readings are accepted **for every T12 case** (family rule) |
| T7-16 | query_rewrite / hard / public | 0.675 → 1.0 | gold too narrow: the substring key `retry` does not match "retries", and `dedupe_window` is a legitimate hypothesis for duplicate retried requests given only the question | stage A used `dedupe_window` as clearly relevant; stage B: key too narrow (retry group misses "retries", duplicate group misses "double"; `dedupe_window` and `X-Request-Id` are legitimate hypotheses, `webhook_secret` a fair distractor) | yes | **gold too narrow**: add variants retries/retrying/resent/repeat and double; remove 2 distractors |
| T7-18 | query_rewrite / hard / public | 0.5 → 1.0 | ambiguous: the symptom has several equally good root causes; the passage's identifier is one of them, and SELinux is a legitimate hypothesis rather than a distractor | stage A listed 4 hypotheses (including the gold identifier and SELinux); stage B: the identifier is only "partly" expected; SELinux is a strong hypothesis, and read_only / fsGroup are plausible, so the key is too narrow | yes | **gold too narrow**: identifier optional; remove 3 distractors |
| T7P-08 | query_rewrite / medium / private | 0.7 → 1.0 | ambiguous: the gold identifier cannot be reached from the question and vocabulary (no model found it in 14 runs) | stage A: no vocabulary item clearly relevant; stage B: the identifier cannot be recovered ("hindsight"); two term groups miss the natural renderings; distractors fair | yes | **ambiguous**: identifier optional; widen 2 term groups (caption; show/appear/display) |
| T7P-09 | query_rewrite / easy / private | 0.7 → 1.0 | ambiguous: the gold identifier is a plausible hypothesis but cannot be derived (no model found it in 14 runs) | stage A named it as one of two hypotheses; stage B: "partly to yes", it should be optional, not required; the failure group misses "breaking"; distractors fair | yes | **ambiguous**: identifier optional; add "break" to the failure group |
| T8P-02 | consolidation / hard / private | 0.7833 → 0.9 | gold too narrow: one fact key demands the superseded value, although the rule says "keep the current value"; another key rejects a faithful paraphrase | stage A covers both facts with the current value; stage B: the current value alone is complete, the paraphrase expresses the fact, and the two hyphenated "hallucinations" are ordinary paraphrase | yes | **gold too narrow**: drop the prior-value key; accept the paraphrase |

Tally: **3 model errors** (T9-15, T9-25, and the counts part of T12-10), **5 gold too narrow** (T9-01, T9-03,
T7-16, T7-18, T8P-02, plus the family-level T12 file rule), **3 ambiguous** (T10-07 dropped; T7P-08 and T7P-09
with the identifier made optional). The two judgments fully agreed on 9 of 11 cases, partly on 1 (T9-03) and
disagreed on 1 (T10-07).

What this changes in D-067:
- The "confident 0.96 false answer" (T10-07) is a false-premise case. The output was grounded and true, but
  it broke the null contract. The case is dropped until it is rewritten. The contract point still stands:
  abstention must be structural (`answer = null`), because downstream code reads the field, not the prose.
- The "false warning" (T9-25) is a genuine error, and so is one partial lesson set (T9-15). The other two
  partial lesson sets were valid alternative answers.
- Three of the four T7 misses were not recoverable from the model-visible input (the identifier) or were
  morphology misses in the key. The fourth (T7-16) was a key problem too.
- The "long-JSON field miss" (T12-10) is a genuine error: the counts disagree with the model's own list.
- Adjusted, the ceiling model scores **97.7% (197/199 correct)**. Its remaining misses are T9-15 and T9-25.
  T12-10 now scores exactly 0.8: the counts error still costs it 0.2.

## Re-scored v2 results (offline, from the saved JSON; no LLM call)

The 2026-09-23 runs: the calibration (`184519` luna, `190032` sol, 1 rep each) and the 4-model final
(`194459` luna / luna-pro / gemini-flash-lite, `201313` deepseek, 3 reps each). The raw column reproduces
the D-066 reports exactly (tests/unit/test_bench_scorers.py re-scores all 2,796 scored calls call-for-call).
The score is the macro mean of the per-family means. "correct" means a score of at least 0.8. The adjusted n
is 1 per rep lower, because T10-07 is dropped.

| model | run | reps | raw | adjusted | Δ | correct raw → adjusted | families changed (raw → adjusted) |
|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 95.8 | **97.7** | +1.9 | 94.5% (189/200) → 99.0% (197/199) | T7 89.9→94.7, T8 95.2→95.6, T9 91.8→94.6, T10 93.3→100, T12 97.3→98.0 |
| openai/gpt-6-luna-pro | final | 3 | 95.1 | **95.6** | +0.5 | 93.8% (563/600) → 95.1% (568/597) | T7 85.6→89.6, T8 92.2→92.3, T12 97.1→97.3 |
| openai/gpt-6-luna | calibration | 1 | 94.7 | **95.4** | +0.7 | 90.5% (181/200) → 92.5% (184/199) | T7 82.0→87.2, T12 99.5→100 |
| openai/gpt-6-luna | final | 3 | 94.2 | **94.9** | +0.6 | 92.0% (552/600) → 94.1% (562/597) | T7 83.2→87.8, T8 92.6→92.8, T12 94.3→94.7 |
| deepseek/deepseek-v4.1-flash | final | 3 | 89.3 | **90.2** | +0.8 | 83.8% (503/600) → 86.3% (515/597) | T7 86.8→90.1, T9 61.2→64.0, T12 84.7→85.3 |
| google/gemini-3.1-flash-lite | final | 3 | 79.9 | **80.7** | +0.8 | 77.3% (463/599) → 79.5% (474/596) | T7 79.6→84.0, T9 88.3→89.7, T12 88.9→89.4 |

The ranking and the D-066 tiers do not change. Per-task error rates, $/correct and the error metrics for
both gold versions are on the leaderboard (`eval/results/LEADERBOARD.md`).

Reproduce (owner machine; the private pack and the raw result JSON are gitignored):

```bash
hlm bench rescore bench/results/20260923-190032-v2.json --gold raw      --pack docs/private/bench-v2
hlm bench rescore bench/results/20260923-190032-v2.json --gold adjusted --pack docs/private/bench-v2
hlm bench rescore 'bench/results/20260923-194459-v2.json#openai/gpt-6-luna' --pack docs/private/bench-v2
```

## Follow-ups (not applied here)

- **T8 hallucination heuristic:** an inner hyphen makes an ordinary English compound ("render-only",
  "script-based") a "specific token". The second judgment and this one agree that such compounds are not
  hallucinated specifics. This is a scorer change for every T8 case, so it belongs in a scorer version
  (v2.1), not in this gold overlay.
- **T7 authoring rule (DESIGN §3):** the gold identifier must be derivable from the question and the
  vocabulary, the same test the key terms already have. If it is not, mark it optional when authoring.
- **T10-07:** rewrite it with an explicit premise, or add a false-premise rule to the prompt (`answer =
  null` when the asked value does not exist yet), then re-add it as a new case id.
