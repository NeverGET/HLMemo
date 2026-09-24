# 45 — W2b + W2c (implementer B): shared hunks, deviations, proposed decisions

> Renumbered at the integration merge: on the W2b branch these consults were 41-44, which main
> already used for W2d/W1.5. W2b's 41 → 44, 42 (this file) → 45, 43 → 46, 44 → 47; the "Sol NN"
> references and `test_solNN_*` names in W2b code were renumbered the same way. Commit messages
> on the W2b branch keep the old numbers ("Sol 41 fixes" = consult 44, "Sol 43" = 46, "Sol 44" = 47).

Branch `worktree-agent-a06aaf3159b7fb21f` (from main a77179a). Owned paths: `librarian/tasks/{write_review,
apply_batch,pair_check}.py`, `librarian/{guards,candidates,trigger,questions}.py`, `db/librarian_queries.py`,
`server/tools/answer.py`, `ops/librarian.py`, prompts `place|relate|relate_verify/v1`, `alembic/versions/0008_librarian_tasks.py`,
`eval/live/run_w2b.py`, `eval/realdata/run_librarian_eval.py`, tests `test_w2b_*`, `test_w2c_*`, `test_gp1_*`,
`test_gp3_*`, `test_ops_librarian.py`, `test_migration_0008.py`, fixtures `tests/fixtures/w2b/`, cassettes `tests/cassettes/w2b/`.

## Shared hunks (hot files; the orchestrator merges)
| File | Hunk |
|---|---|
| `alembic/versions/0008_librarian_tasks.py` | `down_revision = "0006_librarian"` **TEMPORARY** — relink to `0007_import` at merge (D-069). |
| `core/write_service.py` | `WriteDeps.librarian_enqueue` / `librarian_delay_s` (from `HLM_LIBRARIAN_ENABLED`, `HLM_LIBRARIAN_REVIEW_DELAY_S`); `_librarian_jobs()` called after id allocation; descriptors recorded in `resolved.librarian_jobs`; rows inserted after the event. **W1.5 import events** (agent A) should call `librarian.trigger.plan_jobs(..., kind="import")` the same way (priority 6). |
| `core/read_service.py` | `query/2`: `contract_version` always; D-057 `superseded_among` filter after dedupe; `newer_first_on_ties` on the fetched head; `librarian` block (count + ≤ 3 template notices) ≤ 10 % of the budget. |
| `db/replay.py` | `PROJECTION_TABLES` += `librarian_batches`, `version_signals`; `_replay_write` inserts `resolved.librarian_jobs`; `_replay_system` applies `resolved.batches`. |
| `config.py` | `librarian_embed_wait_s` (300), `librarian_review_delay_s` (3), `librarian_verifier` (cross|self). |
| `server/tools/__init__.py` | one `ToolSpec` line for `memory.answer` (+ import). Agent C adds its two tools the same way. |
| `ops/cli.py` | `librarian.add_parser(sub)` + `if group == "librarian": return await librarian.dispatch(conn, args)`. |
| `librarian/{actor,worker,roles,jobs,errors,memory,prompts/__init__,tasks/__init__}.py` | W2a files extended (proposal groups, version_close/signal_upsert, batches + decision rounds, NotReady, expiry sweep, delay_s, MAX_TOKENS/JOB_NAMES, `memory.readable_rules`). |
| `tests/conftest.py` | TRUNCATE list += `librarian_batches, version_signals`. |
| `tests/integration/test_g2_wire.py` | advertised tool set += `memory.answer`. |
| `tests/integration/test_migration_0006.py` | asserts `main@head` instead of `0006_librarian`; the failed-downgrade test pins `0006_librarian`. |
| `tests/integration/test_gl3_llm_down.py` | api with `HLM_LIBRARIAN_ENABLED=true` (write-path enqueue), librarian `HLM_LIBRARIAN_EMBED_WAIT_S=0`; asserts the 100 write-path jobs complete once each. |
| `tests/integration/{test_gl8_roles,test_librarian_cassettes}.py` | proposal shape `actions[]`; approved-and-applied questions are `applied`. |
| `tests/integration/test_librarian_privacy_memory.py` | the working-memory rule references a MAIN item the triggering device can read (was the literal `v1`, which rule-ref filtering now drops); the librarian-device write assertion excludes that item's write. |

## Deviations (proposed decision text)
> **D-0xx (proposed)** | W2b/W2c implementation choices (implementer B, consult 44):
> (1) **Invalidation is the actor's bi-temporal close** (`version_close`: supersede every current row overlapping `[cut, ∞)`, re-insert the part before the cut with the same content/chunks, embed jobs recorded; nothing after the cut, nothing deleted), not "through the write service": a write-service correction re-creates a survivor after the cut. `widen_scope` accept does go through the write service (a revision widening `project_ids`, same content, same `valid_from`).
> (2) **Proposal-time capability is `question(P)` only**; `annotate`/`correct` are checked where a mutation is applied (autonomous direct apply, `apply_batch` per question under THAT question's capabilities, `memory.answer` against the answering device). W2a checked `annotate` at proposal time, which would turn every cross-project proposal from a read-only project into `authority_lost`.
> (3) **Batches**: one `open` batch per project collects ≤ 25 open questions (then `ready`); a decision closes it (`decided`), `apply_batch` marks it `applied`. Question status `applied` added (accepted AND applied). `widen_scope` is never applied by the librarian, even from an approved batch (D-058 propose-only): it stays `approved` until `memory.answer` by a writer on both projects.
> (4) **`query/2`**: `contract_version: "query/2"` on every response; the optional `librarian` block (pending count + ≤ 3 deterministic notices, never model text) is packed AFTER the hits into what they left, at most 10 % of `token_budget` (notices dropped from the end, then the block). Consequence: with a full hit list the block is often absent; the preflight (W2d) should query with room to spare or show questions another way.
> (5) **Candidates**: the drop rule (cosine < 0.80 and no lexical hit) is applied before the top-8/top-5 cut; a lexical hit = ≥ 2 shared distinctive terms (pool-local DF ≤ 30 %, no stop lists) or 1 shared identifier. Candidates are restricted to projects the device reads NOW ∩ the enqueue-time `question` set ∩ policy on, with a visible non-device scope (the privacy gate's rule, applied in SQL).
> (6) **Tasks/prompts**: W2b uses new task names `place`, `relate` (≤ 8 candidates per call), `relate_verify` (v1), leaving the bench tasks `placement`/`contradiction` v1 to G-LIVE-A; `max_tokens` 700/1400/700 (batched, reasoning included).
> (7) **Verifier**: production = the OTHER profile of the chain (`HLM_LIBRARIAN_VERIFIER=cross`); G-LIVE-B measures each profile alone (verifier = same profile, different prompt).
> (8) **Enqueue**: gated by `HLM_LIBRARIAN_ENABLED` in the api process (R2 mounts llm.env into api + librarian); jobs ≤ 4 versions; relation reviews start 3 s after the write and wait up to 300 s for the embed worker, then run lexical-only.
> (9) W2a fix: `actor.recheck` now treats an EXPIRED device as untrusted at apply time (the privacy gate already did).
> (10) Custom answers re-plan with a `write_review` job of the still-current subjects under the answering device's capabilities (priority 4). Every answer is a `librarian-rule` fact built only from the structured decision (template text + clue refs); the free-text note stays in the question's `answer` and reaches only that project's re-plan job (as a one-job rule). Rules whose clue refs are not all readable by the triggering device are not loaded into its prompts.
> (11) One pending question per (kind, subject versions): a pair reviewed again from its other side is counted as `duplicate_proposals`, not asked twice.
> (12) **TTL**: the 30-day `expires_at` bounds every not-yet-applied proposal (open OR approved): an approved question past it is `expired` at apply time, by the sweep, or on `memory.answer`.
> (13) **Decision rounds**: an observer hand-back reopens a batch; the next decision is round n (= prior `apply_batch` events of the batch) and suffixes `:r<n>` to the answer request ids and the apply job key/lineage.
> (14) **Lock order** (librarian = request): device-access locks (sorted) → question rows → logical items (sorted, once per transaction) → batch row.

## D-067 guard design (5 lines)
1. Reference guard: only ids from the call's candidate set count (unknown/duplicate dropped, missing = abstention).
2. Evidence guard: a raised relation must quote 2+ words of each side (normalized substring), else it is never an action.
3. Temporal guard: a supersession must point to the later `t_valid`; the calibrated tier table makes low confidence at most a question, weak link guesses an abstention.
4. High-impact (contradicts+supersedes, cross-project dup/refine → widen) needs an independent verifier call (decomposed same_subject/conflict/current, other profile) to agree before anything is raised; disagreement downgrades or drops, both calls audited.
5. Abstention is the prompts' default ("none" when unsure), counted in the audit and scored in G-LIVE-B.

## Consult 44 (gpt-6-sol, verdict NO) — resolution (commit 78b25d5 + 6d2c247)
| # | Finding | Resolution |
|---|---|---|
| 1 | answer note leaks into global working memory | rule text is template-only; note only in `answer` + the re-plan job; `readable_rules` filters rules by ref readability (`test_sol44_rules_with_unreadable_refs_are_not_loaded`, `test_gq1_*`) |
| 2 | expired questions approvable | `batch_questions` never returns an expired open question; `memory.answer` checks expiry for every status (`test_sol44_expired_questions_cannot_be_approved`, `test_sol44_approved_widen_expires_too`) |
| 3 | conflicting approved questions abort the batch | shared `planned` state in `materialize`; a question whose subject an earlier one closed is `superseded` (`test_sol44_conflicting_batch_questions`) |
| 4 | role demotion race | `lock_role_order`: apply holds advisory (4,0) SHARED, `set_role` EXCLUSIVE (`test_sol44_role_decision_waits_for_an_apply_in_flight`) |
| 5 | equal `valid_from` with supersedes=old | only the NEW item may replace on a tie (unit test) |
| 6 | verifier blind to scope; no-answer kept as a question | verifier items carry `project` same/other + prompt rule; no answer → dropped (`verifier_no_answer`) |
| 7 | early approval stranded under observer | `role_denied` hands approvals back (questions open, batch ready) (`test_sol44_observer_hands_approvals_back`) |
| B | G-LIVE-B dominated by `none` | per-class worst-over-reps bars: positive recall ≥ .85, positive precision ≥ .90, direction ≥ .90, false cross raise ≤ .02; plus the production chain (luna + deepseek verifier) |
| C1 | answer close skips actor checks | `memory.answer` materializes with the answering device's ctx + capability set (check_link/check_correct) |
| C2 | notices before hits | now after hits (deviation 4 above) |

## Consult 46 (gpt-6-sol delta review, verdict NO) — resolution
| # | Finding | Resolution |
|---|---|---|
| 1 | `readable_rules` checked projects only; `pair_check` loaded rules unfiltered | `librarian/memory.py:readable_rules` judges every ref with the privacy gate (trusted device, `device_scope` visible to its class, never `device:`, grant/admin, enqueue-time `question` set, policy not `off`, status active; an older version by its own scope); used by BOTH `write_review` and `pair_check` (`test_sol46_rule_refs_are_judged_by_device_scope_on_every_path`) |
| 2 | approved question past TTL still applied by `apply_batch` | definitive rule: the 30-day TTL bounds every NOT-YET-APPLIED proposal. `apply_batch` re-reads status + `expires_at` under row lock (`worker._lock_approved`) → `expired`, nothing applied; the sweep expires `open` and `approved` (`test_sol46_approved_question_past_ttl_expires_at_apply`) |
| 7 | re-approval after hand-back collided (same answer `request_id`, used apply job key) | decision rounds: `roles.decision_round` = prior `apply_batch` events of the batch; round n>0 suffixes `:r<n>` to the answer `request_id`, the apply job key and its lineage (`test_sol46_handed_back_approvals_can_be_decided_again`: same owner re-approves, promoted worker applies, keys `librarian_apply:B`, `librarian_apply:B:r1`) |
| new | `apply_batch` took item locks before the device lock; per-question item locks not globally sorted | one order everywhere: device-access locks of every proposing device (sorted) → question rows `FOR UPDATE` (by id) → every touched logical item once (sorted) → batch row; the proposal path also locks the union of all proposals' items once, sorted (`test_sol46_apply_batch_takes_the_device_lock_before_item_locks`; a mutant with items first fails it) |
| new | duplicate-question check not atomic | it already ran under the subjects' item locks (held to commit, READ COMMITTED re-reads after the wait); now explicit (union lock before the loop) and tested with two workers in flight together (`test_sol46_concurrent_reviews_of_one_pair_ask_once`) |
| B | pooled positive bar could hide a lost small class; summaries lacked mode/provider evidence | every positive class (contra_new/old/none, duplicate, refines) must be present with worst-over-reps recall ≥ 0.66; `SUMMARY.md`/`results.json` record the run mode and ledger calls per (mode, task, profile, model, outcome); G-LIVE-B re-run live |
| GP1 | set comparison missed duplicate rows; accuracy only printed | multiset + list equality after rebuild; recorded pipeline exact ≥ 0.90 asserted |

## Consult 47 (gpt-6-sol second delta, verdict NO: 3 items) — resolution
| # | Finding | Resolution |
|---|---|---|
| 1 | rule refs filtered once at plan time, not before each provider attempt | `memory.rules_still_readable` runs inside the precheck of EVERY attempt (write_review and pair_check); a lost ref raises `PrivacyDenied` (call skipped, audited as a denial) (`test_sol47_rule_ref_lost_mid_job_stops_the_next_calls`) |
| 2 | TTL compared with the clock read before the lock waits | `_lock_approved` reads the clock AFTER the device/question locks and returns it as the job's `T` (`test_sol47_ttl_is_checked_after_the_lock_wait`: expires during a 1.5 s device-lock wait → `expired`; a mutant comparing with the earlier `T` fails it) |
| 3 | fallback + chain artifacts in the old format | G-LIVE-B re-run live for all three configurations with the new runner (mode `live`, ledger calls per profile/model, per-class bar) |
