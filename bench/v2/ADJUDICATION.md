# bench v2 gold adjudication (adj-2, 2026-09-24)

D-067 asked for a gold adjudication of the 11 cases that the ceiling model (`openai/gpt-6-sol`, calibration
run `bench/results/20260923-190032-v2`) missed (score < 0.8), before any threshold is fixed on bench v2.
This file records both judgments per case, the final ruling, and the re-scored numbers. It is public-safe:
case ids, verdicts and reasons only. No private-pack text (`T7P-*`, `T8P-*` come from `docs/private/bench-v2/`)
is quoted.

The rulings are machine-applied by the gold overlay `src/hlmemo/bench/adjudication_v2.json` (version
`adj-2`, content hash in every report: `adj-2+4c0651b4`). `hlm bench` scores with the adjusted gold by default;
`--gold raw` reproduces the D-066 numbers exactly. The pack files themselves are unchanged (version 1).

**History.** adj-1 (commit bb03629) credited three cases to the gold: it dropped T10-07 and accepted extra
lesson sets for T9-01 and T9-03. Sol's review (`docs/consults/40-sol-review-w2f.md`, DO-NOT-MERGE) and the
orchestrator ruled all three **strictly**. adj-2 applies that ruling. A lesson counts only when the task text
explicitly supports it, and the `answer = null` contract is binding.

## Protocol

1. **First judgment** (implementer). Inputs: the model-visible input, the rules the model was given, the gold
   with its rationale, the ceiling model's output, and the outputs of the other 4 models (14 runs per case).
2. **Second, blind judgment** (a fresh agent, separate context). Inputs: only the rules and the model-visible
   input. Stage A: it derived its own answer and wrote it down before seeing anything else. Stage B: it rated
   anonymised candidates in random order (the gold-conforming answer and the ceiling model's answer, unlabelled).
   For query rewrites, stage B also showed the hidden source passage and the scoring key. It never saw the gold
   rationale, the case id, the model identity, or the first judgment.
3. **Review ruling** (Sol 40 + orchestrator). It is binding where it differs from the first two judgments.
   The strict rules: a lesson "clearly applies" only if the task text states the condition the lesson is
   about, and the abstention contract is structural.
4. **Verdicts.** *gold correct*: the output violates the rules the model was given, so it is a model error.
   *gold too narrow*: the output is correct under those rules, but the key rejects it for a reason the rules
   do not state (morphology, a legitimate hypothesis counted as a distractor, a demand for the superseded
   value). *ambiguous*: the model-visible input underdetermines one component, and that component is made
   optional. No case is dropped.
5. Adjustments apply to every model's results. A rule-level ambiguity (the T12 file-list reading) applies to
   the whole family.

## The 11 cases

| case | family / tier / pack | ceiling raw → adj | first judgment | second (blind) judgment | final ruling (overlay) |
|---|---|---|---|---|---|
| T9-01 | risk_check / easy / public | 0.65 → 0.65 | L16 (detached long deploys) is a legitimate second lesson for a client-driven `ssh … bash -s` deploy | stage A {L01, L16}; stage B both acceptable, L16 "borderline only because 'long' is not stated" | **gold correct**: strict re-evaluation. L16's condition is a *long* deploy, and the task text never says the deploy is long. Original gold {L01} |
| T9-03 | risk_check / medium / public | 0.65 → 0.65 | lean gold too narrow (L20 names "test runs") | stage A {L03, L09}; stage B {L03} acceptable, {L03, L20} borderline | **gold correct**: only {L03}. L20 needs the embedder to load in that pytest run, and L09 needs a shared DB; the task states neither |
| T9-15 | risk_check / medium / public | 0.65 → 0.65 | model error: L24 matches only the word "migrations" | stage B: {L25, L24} leaning not acceptable | **gold correct** |
| T9-25 | risk_check / easy / public | 0.0 → 0.0 | model error: a false warning (L14) on the word "piped" | only the silent answer acceptable | **gold correct** |
| T10-07 | answer_or_abstain / medium / public | 0.0 → 0.0 | literal contract: the date is absent, so `answer = null`; the prose "no date yet" breaks it | null acceptable; the prose answer borderline | **gold correct**: the contract is `answer = null` when the asked value is absent. A prose abstention is a model error (a confident 0.96 non-null answer). Case kept with its original gold |
| T12-10 | extract_review / hard / public | 0.7829 → 0.8 | the counts contradict the model's own list (model error); the files named only as "the fix also touched" fit a defensible reading of the rule | counts: gold only; files: both readings acceptable | **gold correct** for the counts. The file-list reading is accepted both ways **for every T12 case** (family rule) |
| T7-16 | query_rewrite / hard / public | 0.675 → 1.0 | the key `retry` misses "retries"; `dedupe_window` is a legitimate hypothesis | key too narrow (also "double"; `X-Request-Id` legitimate, `webhook_secret` a fair distractor) | **gold too narrow**: add retries/retrying/resent/repeat and double; remove 2 distractors |
| T7-18 | query_rewrite / hard / public | 0.5 → 1.0 | the identifier is one of several equally good root causes; SELinux is a legitimate hypothesis | the identifier only "partly" expected; SELinux strong; read_only and fsGroup plausible | **gold too narrow**: identifier optional; remove 3 distractors |
| T7P-08 | query_rewrite / medium / private | 0.7 → 1.0 | the gold identifier cannot be reached from the question and vocabulary (no model found it in 14 runs) | cannot be recovered ("hindsight"); 2 term groups too narrow | **ambiguous**: identifier optional; widen 2 term groups |
| T7P-09 | query_rewrite / easy / private | 0.7 → 1.0 | the identifier is plausible but not derivable (no model found it in 14 runs) | "partly to yes": should be optional; the failure group misses "breaking" | **ambiguous**: identifier optional; add "break" |
| T8P-02 | consolidation / hard / private | 0.7833 → 0.9 | one fact key demands the superseded value although the rule says "keep the current value"; another rejects a faithful paraphrase | the current value alone is complete; the paraphrase expresses the fact | **gold too narrow**: drop the prior-value key; accept the paraphrase |

**Tally (final):** 6 model errors (T9-01, T9-03, T9-15, T9-25, T10-07, and the counts part of T12-10).
3 gold too narrow (T7-16, T7-18, T8P-02), plus the family-level T12 file rule. 2 ambiguous components made
optional (T7P-08, T7P-09). Agreement before the ruling: the first and second judgments agreed fully on 9 of 11
cases, partly on T9-03 and not on T10-07. The review ruling overturned both judgments on T9-01 and the
lenient readings on T9-03 and T10-07.

What this confirms in D-067:
- The confident false answer (T10-07) and the false warning (T9-25) are genuine errors. So are three extra
  lessons (T9-01, T9-03, T9-15): the ceiling model over-warns with lessons whose conditions the task does not
  state. Keyword and condition discipline is a prompt/guard target for W-O.
- Three of the four T7 misses came from the key (morphology, legitimate hypotheses) or from an identifier
  that cannot be derived. They were not rewrite failures.
- The long-JSON miss (T12-10) is a genuine error: the counts disagree with the model's own list.
- Adjusted, the ceiling model scores **96.6% (195/200 correct)**. Its remaining misses are T9-01, T9-03,
  T9-15, T9-25 and T10-07. T12-10 now scores exactly 0.8, because the counts error still costs it 0.2.

## Re-scored v2 results (offline, from the saved JSON; no LLM call)

The runs, all from 2026-09-23:
- calibration: `184519` (luna) and `190032` (sol), 1 rep each;
- 4-model final: `194459` (luna, luna-pro, gemini-flash-lite) and `201313` (deepseek), 3 reps each;
- port check: the `hlm bench` production-path re-run of luna, 1 rep.

The raw column reproduces the D-066 reports exactly: `tests/unit/test_bench_scorers.py` re-scores all 2,796
scored calls call-for-call. The score is the macro mean of the per-family means. "Correct" means a score of at
least 0.8. No case is dropped, so n is unchanged.

| model | run | reps | raw | adjusted | Δ | correct raw → adjusted | families changed (raw → adjusted) |
|---|---|---|---|---|---|---|---|
| openai/gpt-6-sol | calibration | 1 | 95.8 | **96.6** | +0.7 | 94.5% (189/200) → 97.5% (195/200) | T7 89.9→94.7, T8 95.2→95.6, T12 97.3→98.0 |
| openai/gpt-6-luna-pro | final | 3 | 95.1 | **95.6** | +0.5 | 93.8% (563/600) → 95.2% (571/600) | T7 85.6→89.6, T8 92.2→92.3, T12 97.1→97.3 |
| openai/gpt-6-luna | calibration | 1 | 94.7 | **95.4** | +0.7 | 90.5% (181/200) → 92.5% (185/200) | T7 82.0→87.2, T12 99.5→100 |
| openai/gpt-6-luna | final | 3 | 94.2 | **94.9** | +0.6 | 92.0% (552/600) → 94.2% (565/600) | T7 83.2→87.8, T8 92.6→92.8, T12 94.3→94.7 |
| openai/gpt-6-luna | hlm-bench port check | 1 | 93.8 | **94.4** | +0.6 | 89.5% (179/200) → 91.5% (183/200) | T7 82.8→87.5, T8 90.5→90.7, T12 95.5→95.7 |
| deepseek/deepseek-v4.1-flash | final | 3 | 89.3 | **89.8** | +0.5 | 83.8% (503/600) → 85.8% (515/600) | T7 86.8→90.1, T12 84.7→85.3 |
| google/gemini-3.1-flash-lite | final | 3 | 79.9 | **80.5** | +0.6 | 77.3% (463/599) → 79.1% (474/599) | T7 79.6→84.0, T12 88.9→89.4 (not ranked: 1 infra error) |

The ranking and the D-066 tiers do not change. The leaderboard (`eval/results/LEADERBOARD.md`) holds the
per-task error rates, $/correct and error metrics for both gold versions. It ranks only complete runs, so the
gemini final (1 infra error) is recorded but not ranked.

Reproduce (owner machine; the private pack and the raw result JSON are gitignored):

```bash
hlm bench rescore bench/results/20260923-190032-v2.json --gold raw      --pack docs/private/bench-v2
hlm bench rescore bench/results/20260923-190032-v2.json --gold adjusted --pack docs/private/bench-v2
hlm bench rescore 'bench/results/20260923-194459-v2.json#openai/gpt-6-luna' --pack docs/private/bench-v2
```

## Follow-ups (not applied here)

- **T8 hallucination heuristic:** an inner hyphen makes an ordinary English compound ("render-only",
  "script-based") a "specific token". Both judgments agree that such compounds are not hallucinated
  specifics. This is a scorer change for every T8 case, so it belongs in a scorer version (v2.1), not in the
  gold overlay.
- **T7 authoring rule (DESIGN §3):** a gold identifier must be derivable from the question and the vocabulary,
  the same test the key terms already have. If it is not, mark it optional when authoring.
- **T9 / T10 prompt guards (W-O):** the strict ruling makes conditional lessons and prose abstentions model
  errors. The prompt and a deterministic guard (non-null answer without a matching snippet, lessons whose
  condition is not in the task) are the fix, not the gold.
