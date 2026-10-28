# 126: D-272 shadow pipeline: review record

Threat model and rubric: `126-d272-threat-model.md`. There were two fresh-context reviewers in parallel (D-266). Neither wrote the change.
- **Correctness and regressions:** Fable.
- **Spend, privacy and deploy:** Sonnet.

## Round 1 findings and fixes

| # | Severity | Finding | Fix | Test |
|---|---|---|---|---|
| 1 | HIGH (Fable) | Shadow calls had no deadline and used the background retry policy. A slow shadow endpoint could hold a job for about 5 × 180 s per call and stall the live queue. | Every shadow call runs with `attempt_policy="latency"` and a job-level deadline (`HLM_LIBRARIAN_SHADOW_WALL_S`, default 180 s). | `test_a_stalled_shadow_model_ends_within_its_wall_and_the_live_result_stands`: the shadow host never answers; the live question stands and the jobs end within the wall. |
| 2 | MEDIUM (Sonnet) | No shadow sub-limit. Shadow spend could use up the shared hour/day caps and defer live work or memory.ask. | The shadow stops for the UTC day once its own spend reaches `HLM_LIBRARIAN_SHADOW_DAY_USD` (default 1 USD). The verifier's max_tokens went from 4000 to 2000, which halves the worst-case reservation. | `test_the_shadow_stops_at_its_day_cap` |
| 3 | MEDIUM (Sonnet) | The new profiles had no `price_valid_until`. | Both profiles: `price_valid_until = "2026-12-31"`. | profile load |
| 4 | MEDIUM (Fable) | The report's spend counted every relate call per profile, not just shadow calls. | Filtered to `job_id IS NULL` (shadow calls carry no job id). | `test_shadow_runs_beside_the_live_pipeline_and_only_audits` (report block) |
| 5 | LOW | Test gap: the live result was not compared field by field. | Not changed. The tests check the question kinds, the link rels, the question count and that no shadow call reaches the live lineage. | — |
| 6 | LOW | `named_profile` re-reads the TOML on every job. | Accepted (harmless). | — |
| 7 | LOW | The template turns the shadow on. | Intended for D-272. The template comment says how to turn it off. | — |

## Found by the author's own tests before the review
Each of these was caught because a test asserted that the shadow block holds no `error`; the broad `except` had hidden them.
- The shadow lineage must be a uuid: `llm_lineage_calls` rejected `shadow:…`.
- Wrong `Proposal` field names (`subject_clues`, `auto_ok`).
- The report used a column that does not exist (`events.received_at`, not `recorded_at`).
- A config comment named a task, against the provider-agnostic rule (`test_no_model_id_outside_profiles`).

## Checked clean by the reviewers
- **Live-plan isolation:** separate Judgement and PairText objects, separate counters and `calls`; the live audit shape is unchanged.
- **Provider state:** contextvars are reset, and the ledger and budget use their own autocommit pool, never the job's transaction.
- **Privacy:** the precheck runs before every shadow attempt.
- **Replay:** it reads `resolved`, never the request's shadow block.
- **v3 schema** is identical to v2.
- **`cache_system`** never changes live bodies.
- **Release state:** the manifest check passes with the new keys; the fingerprint covers them; rollback to an env without them turns the shadow off; an unknown profile only records an error in the audit.

Round 2 is not needed: no HIGH is open and each fix has a test. The owner accepted the shadow week (2026-10-28).

## Diagnostics delta (owner, 2026-10-28)
Owner: "the shadow mode must log every condition that blocks promotion, so we can find it with a surgeon's precision and fix it". One fresh-context review (Sonnet) of the delta. It found no HIGH, 7 MEDIUM and 4 LOW; all were addressed.

| # | Finding | Fix |
|---|---|---|
| M1 | The raw text of a failed attempt's schema error was not redacted. | It is now redacted. |
| M2 | Exception text was stored, against the worker's `error_code` rule. | The block stores `{stage, type, code}` (content-free) plus `remaining_s`. |
| M3 | `attempts` had no size cap. | At most 40 attempts (the rest counted as `attempts_dropped`) and at most 8 raw answers of 2000 characters. |
| M4 | A report answer was matched by candidate id only. | Shadow call audits name their subject and candidates (verifier calls: their item → pair map). Live answers are aligned with the expected chunk sequence, and a failed call leaves its chunk unmatched. |
| M5 | Pairs were keyed without the job. | Keys are `(event_id, pair)`; "both" is computed per job. |
| M6 | An unanswered shadow pair looked like "none". | `unanswered` lists the pairs never judged; each side carries `status` (judged / none / not_answered). |
| M7 | `pair()` did not rebuild the exact input. | It rebuilds the call's own chunk (8 per call) in order with the cross flags, names the rules by clue and the prompts by version, and lists missing versions instead of shifting. |
| L1 | A failure inside the audit handler could escape. | A last-resort `try` around the whole shadow step records `{"error": {"stage": "audit"}}`. |
| L2 | The ledger query mixed in other calls. | It is filtered by the reported runs' lineages and adds p95 latency per outcome. |
| L3 | Transient errors and timeouts are not observed (no parsed answer). | They are covered by the ledger rows of the lineage (outcome, latency). |
| L4 | The prompt text sent was not recorded. | `system_sha256` per attempt (drift check). |

The live-supersession filter was checked: relation contradicts, tier not abstain, with a direction. It equals `_proposals`' `raised` contradiction branch, so both sides use the same rule.
