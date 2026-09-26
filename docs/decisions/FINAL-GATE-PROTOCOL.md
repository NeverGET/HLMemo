# Final gate protocol — memory.ask R4 candidate (frozen 2026-09-26, D-187)

This protocol is frozen BEFORE the run (D-158a item 6). There is one run with no best-of-N. A change after a failed final needs a new hold-out.

## Candidate under test
- Code: branch `wf-memory-ask` @ **fd83f95**. It contains the temporal layer (da41a94 + the explicit-links merge dd07588) and the luna-med profile, which this run does not use.
- Configuration (env, on top of `deploy/llm.env.example`):
  | Setting | Value |
  |---|---|
  | `HLM_RESEARCH_ENABLED` | true |
  | `HLM_MAP_SUMMARY_ENABLED` | true |
  | `HLM_RESEARCH_ANSWER_MODE` | `prose` (prompt research/v3.1) |
  | `HLM_RESEARCH_ATTRIBUTION` | `llm` |
  | `HLM_RESEARCH_WRITER_PROFILE` | unset: the writer is the task profile `openrouter-gpt6-luna`, reasoning effort low (D-186) |
  | `HLM_RESEARCH_SELECT`, `HLM_RESEARCH_EXPAND` | off |
  | `HLM_RESEARCH_MAX_USD` | 0.01 (the R4 manifest value; luna fits) |
  | `HLM_RESEARCH_MAX_TOKENS` | 100000 |
  | Deadline | 25 s |
- Item fusion and read-time excerpt status and context are on (code defaults in prose mode).
- memory.ask `token_budget` = 6000 for the measurement, so claims are not trimmed.

## Memory under test
| Set | DB | Project | Contents |
|---|---|---|---|
| **sealed B** | `hlm_research_b_tl` | `hb-eval` | corpus B @82200ae (91 items) plus 4 explicit supersedes links (event 13243, `hlm links explicit`) |
| **PR set** | `hlm_research_cur_tl` | `hlmemo` | a copy of `hlm_research_cur` (the current-tree replica) with `hlm links explicit` applied; its dry-run proposal list is recorded before the apply |

## Question sets (hashes; contents stay sealed)
| Set | Questions | sha256[:16] |
|---|---|---|
| PR set `docs/private/realdata-hlmemo/pr-gate/questions.jsonl` | 60 | `24313393c6178215` (its own must_mention) |
| sealed B `docs/private/realdata-hlmemo/corpus-b-sealed.jsonl` | 50 | `b08a49dfc67ec048` |
| sealed B frozen key facts `…/gate-v1/must_mention.sealed.jsonl` | – | `f76fe319c340a30a` |
Every question stays in the denominator.

## Measurement
- **Scorer:** wf-research-proto @ **6f9ab6d**, `score.py --gate v1`.
  - Judge profile `openrouter-deepseek-v4-pro` (pinned).
  - Rubric sha256[:12]: correct `26082cadcabb`, abstain `643831ee0908`, entail `69cd4c0b5291`, restate `7c1739b7ff84`, derive `2c04555cb776`.
  - det check v2; `--faithful-scope item` (title plus a 6,000-char item window); entail in batches of 6.
  - A judge runaway gets one retry at twice the output cap, then goes to adjudication; a malformed verdict goes to adjudication.
- **Generation guard:** if more than 2 questions end in error or a budget stop, the run is INVALID and not judged. A rerun is allowed only for an infrastructure cause (spend cap, provider outage), documented in DECISIONS.
- **Adjudication:** codex `gpt-6-astra` low, blind (keys outside the packet folders), seeds 168 and 165.
  - D-168 correctness: all judged-incorrect questions, 20% (min 3) of the judged-correct, all answered negatives.
  - D-165 faithfulness: all rejections plus 20% of accepts; the false-accept rate is extrapolated per stratum.
- **C1 rater:** a fresh Claude subagent per set, rubric `consumer_v1.md` (sha `9b40039f9318`, drill rule included), `consumer_rate.py build --normalise`, seed 158; the slot key is kept out of the rater's reach.
- **Reference (NotebookLM):**
  - sealed B → notebook `0a2e5f63-704c-4b78-83f9-91d4971a4b12` (corpus B, the same 91 items).
  - PR set → a new notebook holding the SAME items as `hlm_research_cur` (secret-scanned), with a fresh `nlm query` process per question.
  - The reference is judged, adjudicated and rated exactly as memory.ask is. If the NotebookLM daily query quota blocks part of a set, the reference covers the queried subset, and that is reported.

## Pass criteria (D-158 + D-158a): PR set and sealed B must pass SEPARATELY
| Criterion | Pass |
|---|---|
| C1 utility | mean U ≥ 1.6 on answerable, U=0 share ≤ .10, mean U ≥ NLM − .05 |
| C2 correct (adjudicated) | ≥ .80 and ≥ NLM − .05 |
| C3 faithful (adjudicated, item scope) | ≥ .95; answer-level contradiction ≤ .04 |
| C4 abstain (adjudicated, premise corrections included) | ≥ .90 |
| C5 source recall | ≥ .85 |
| C6 | p95 ≤ 20 s; error/timeout ≤ .02; ≤ $0.01/q (reported: p99, response tokens median/p95) |
| C7 zero scope leakage | review-79 T1 isolation tests green at fd83f95, plus a cross-project probe on the R4 rehearsal |

Paired bootstrap CIs are reported for the reference comparisons; the point estimate gates.

## Outcome
- **Both sets pass:** memory.ask self-certifies (D-130). Then R4: the merge, the D-118 minimal port, the R4 manifest pinning this configuration, the VM rehearsal, prod, and the explicit-links backfill on prod, reviewed first as a data change.
- **Any criterion fails:** the residual goes to the owner with the numbers. No tuning on these sets.
