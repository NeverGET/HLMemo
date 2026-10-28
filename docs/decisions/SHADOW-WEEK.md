# The librarian shadow week: finding and removing promotion blockers (D-272)

Owner (2026-10-28): the shadow week answers more than "can it be promoted". Every condition that blocks a promotion is logged and reported. Each one is then traced to its root cause and fixed precisely, so the next measurement can pass.

## What every job records (`request.shadow` in its librarian event)
| Field | Use |
|---|---|
| `asked` | Every pair `[subject, candidate, cross]` the shadow judged. With the immutable versions this rebuilds the exact input. |
| `calls[]` | Each relate / relate_verify call: profile, prompt version, input digest, the model's full answer (redacted). |
| `judgements[]` | Every non-`none` judgement after the guards: relation, direction, scope, tier, flags, the verifier's verdict. |
| `proposals[]` | What the shadow would have proposed (kind, clues, relation, direction, scope, auto class, verifier agreement). |
| `guards` | Guard counters (quote unverified, action → question, historical kind, verifier rejected, …). |
| `attempts[]` | Every provider attempt: outcome, latency, tokens (in / cached / out), cost; for a failed one, the error and the raw answer (redacted, 2000 characters). |
| `seconds` | Time per stage (relate, verify, finalize). |
| `error` | Where it stopped and why: `{stage, type, message}`. |
| `skipped` | `day_cap` when the shadow's daily spend cap was reached. |
| `lineage` | Joins the ledger (`llm_calls`) for every shadow call. |

The live side of the same job records its own `judgements`, `guards`, `candidates`, `rules` and `calls`.

## The tools (read-only)
- `hlm_ops.sh librarian shadow [--project P] [--since DATE] [--json] [--with-texts]`: the blocker report.
- `hlm_ops.sh librarian shadow --pair vA,vB`: one pair in detail. It shows the exact relate input of each job that asked it, both answers with reasons and quotes, both judgements with flags and verifier verdicts, and the shadow's error and timings.

## Blocker categories and how each is diagnosed
| Category | Where it shows | First diagnostic step |
|---|---|---|
| Shadow failures | `shadow_errors` (stage/type with examples), `attempts.*.outcomes` | The stage and message; for `schema_fail`, the raw answer; for a timeout, `seconds` and attempt latency |
| Truncation / malformed JSON | `attempts.*.failures` | The raw answer: reasoning used up max_tokens, a JSON shape problem, or a prompt problem |
| Guard downgrades | `flags.shadow`, `guards.shadow` | `--pair`: the quote and the text it should match, the kind mismatch, the verifier's reason |
| Missed supersession (live-only) | `live_only` | `--pair`: was the pair asked; what the shadow answered and why; label it |
| Extra supersession (shadow-only) | `shadow_only` | `--pair` plus the items' texts; label it: a real find (recall gain) or a false invalidation |
| Latency / cost | `attempts` p50/p95, tokens, cost; `ledger` | Against the per-job wall (180 s) and the daily cap (1 USD) |
| Candidate never reached the model | Not visible here (it is the same for both sides) | The live `candidates` audit; the D-089/D-117 candidate-selection limit |

Every blocker found gets a lesson. A fix goes through the usual review. A prompt or guard change is re-measured on the labelled fresh set (D-270) before it ships.

## Labelling
- Every live-only and shadow-only supersession is labelled blind by two labellers on different models, from `--with-texts`, using the written supersession policy (D-268/D-271).
- The operator settles splits from the texts.
- Labels and texts stay private (`docs/private/librarian-model-2026-10/shadow/`).

## Promotion criteria (unchanged from the plan, measured on the shadow's live proposals)
- At least 150 labelled proposals over at least 2 projects.
- Precision ≥ .90 and false invalidation ≤ .02 for the class to be promoted ("a newer item replaces part of a current fact").
- No open blocker of the categories above that affects that class.
- Then the owner decides; promotion is one class at a time (V1-PLAN ladder).

## Cadence
- A daily check of the report: errors and failures first, so a broken shadow is fixed on day 1, not found on day 7.
- Labelling in batches.
- At the end of the week, a summary with the blocker list, fixes and the criteria for the owner.
