# Live gate rubric (CC-5)

The live gates run the production librarian path (versioned prompts under
`src/hlmemo/librarian/prompts/`, the redactor, the provider with schema validation, retry, backoff and
the atomic spend reservation) against the real provider. They are **release-blocking**: required
before R2, R3, R4 and after any change of model, profile or prompt version. Cassettes (CI) prove
parsing, application, idempotency and replay; only this gate says anything about model quality.

Runner: `eval/live/run.py` (`make gate-live PROFILE=… FALLBACK=… REPS=3 MAX_USD=5`). Results are
written to `eval/live/<date>-<profile>/{results.json,SUMMARY.md}` (redacted model outputs, scores,
error counts, ledger outcomes, latency, cost; never a raw provider response).

## Procedure

1. Fixtures are pinned by sha256 in `eval/live/run.py` (`FIXTURES`). A mismatch aborts the run.
2. Every case of every task runs `REPS` times on the default profile, then `REPS` times on the
   fallback profile, **each profile alone** (the gate measures each model; the production fallback
   chain is exercised by G-L1).
3. `--max-usd` (MAX_USD) is a runaway guard shared by the whole run, enforced by the same atomic
   worst-case reservation as production. A refused reservation aborts the run: **FAIL, never skip**.
4. A schema failure after the one retry scores 0 (`json_fail`). A provider failure after all
   retries scores 0 (`infra_error`); it is not excluded from the score.

## Scoring (the D-019 bench scoring, implemented once in `src/hlmemo/bench/v1.py`, shared with `hlm bench`)

| Task | Fixture | Case score |
|---|---|---|
| G-LIVE-A T1 placement | `src/hlmemo/bench/tasks/v1/t1_placement.json` (10 cases) | 1 if `layer` and `topic_id` are exact and `|importance − gold| ≤ 2`, else 0 (`stability` recorded, not scored) |
| G-LIVE-A T2 contradiction | `src/hlmemo/bench/tasks/v1/t2_contradiction.json` (10) | 1 if `contradicts` and `supersedes` are both exact, else 0 |
| G-LIVE-A T3 summary | `src/hlmemo/bench/tasks/v1/t3_summarization.json` (5) | 0.7 × Jaccard(`clue_ids`, gold) + 0.3 × [summary ≤ 120 words] |
| G-LIVE-A T4 risk | `src/hlmemo/bench/tasks/v1/t4_risk_check.json` (8) | 0.5 × [`warn` exact] + 0.5 × Jaccard(`matched_lesson_ids`, gold) |

Task-level validity (part of the schema check, retried once): a placement `topic_id` must be one
of the case's candidates; summary `clue_ids` and risk `matched_lesson_ids` must name known ids.

## Pass rule

Per profile and task: the **minimum over reps** of the per-rep mean case score must reach the
threshold; the mean over reps is reported next to it. Per profile: JSON-fail rate ≤ 2%.

| Task | Threshold (min over reps) |
|---|---|
| placement | 0.90 |
| contradiction | 0.90 |
| summary | 0.80 |
| risk | 0.85 |

A gate run passes only when every task passes on **both** profiles.
