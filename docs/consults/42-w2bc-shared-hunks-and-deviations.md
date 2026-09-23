# 42 — W2b + W2c (implementer B): shared hunks, deviations, proposed decisions

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
| `librarian/{actor,worker,roles,jobs,errors,prompts/__init__,tasks/__init__}.py` | W2a files extended (proposal groups, version_close/signal_upsert, batches, NotReady, expiry sweep, delay_s, MAX_TOKENS/JOB_NAMES). |
| `tests/conftest.py` | TRUNCATE list += `librarian_batches, version_signals`. |
| `tests/integration/test_g2_wire.py` | advertised tool set += `memory.answer`. |
| `tests/integration/test_migration_0006.py` | asserts `main@head` instead of `0006_librarian`; the failed-downgrade test pins `0006_librarian`. |
| `tests/integration/test_gl3_llm_down.py` | api with `HLM_LIBRARIAN_ENABLED=true` (write-path enqueue), librarian `HLM_LIBRARIAN_EMBED_WAIT_S=0`; asserts the 100 write-path jobs complete once each. |
| `tests/integration/{test_gl8_roles,test_librarian_cassettes}.py` | proposal shape `actions[]`; approved-and-applied questions are `applied`. |

## Deviations (proposed decision text)
> **D-0xx (proposed)** | W2b/W2c implementation choices (implementer B, consult 41):
> (1) **Invalidation is the actor's bi-temporal close** (`version_close`: supersede every current row overlapping `[cut, ∞)`, re-insert the part before the cut with the same content/chunks, embed jobs recorded; nothing after the cut, nothing deleted), not "through the write service": a write-service correction re-creates a survivor after the cut. `widen_scope` accept does go through the write service (a revision widening `project_ids`, same content, same `valid_from`).
> (2) **Proposal-time capability is `question(P)` only**; `annotate`/`correct` are checked where a mutation is applied (autonomous direct apply, `apply_batch` per question under THAT question's capabilities, `memory.answer` against the answering device). W2a checked `annotate` at proposal time, which would turn every cross-project proposal from a read-only project into `authority_lost`.
> (3) **Batches**: one `open` batch per project collects ≤ 25 open questions (then `ready`); a decision closes it (`decided`), `apply_batch` marks it `applied`. Question status `applied` added (accepted AND applied). `widen_scope` is never applied by the librarian, even from an approved batch (D-058 propose-only): it stays `approved` until `memory.answer` by a writer on both projects.
> (4) **`query/2`**: `contract_version: "query/2"` on every response; the optional `librarian` block (pending count + ≤ 3 deterministic notices, never model text) is budgeted to ≤ 10 % of `token_budget` and packed BEFORE the hits (not after), so the preflight reliably shows open questions.
> (5) **Candidates**: the drop rule (cosine < 0.80 and no lexical hit) is applied before the top-8/top-5 cut; a lexical hit = ≥ 2 shared distinctive terms (pool-local DF ≤ 30 %, no stop lists) or 1 shared identifier. Candidates are restricted to projects the device reads NOW ∩ the enqueue-time `question` set ∩ policy on, with a visible non-device scope (the privacy gate's rule, applied in SQL).
> (6) **Tasks/prompts**: W2b uses new task names `place`, `relate` (≤ 8 candidates per call), `relate_verify` (v1), leaving the bench tasks `placement`/`contradiction` v1 to G-LIVE-A; `max_tokens` 700/1400/700 (batched, reasoning included).
> (7) **Verifier**: production = the OTHER profile of the chain (`HLM_LIBRARIAN_VERIFIER=cross`); G-LIVE-B measures each profile alone (verifier = same profile, different prompt).
> (8) **Enqueue**: gated by `HLM_LIBRARIAN_ENABLED` in the api process (R2 mounts llm.env into api + librarian); jobs ≤ 4 versions; relation reviews start 3 s after the write and wait up to 300 s for the embed worker, then run lexical-only.
> (9) W2a fix: `actor.recheck` now treats an EXPIRED device as untrusted at apply time (the privacy gate already did).
> (10) Custom answers re-plan with a `write_review` job of the still-current subjects under the answering device's capabilities (priority 4); every answer is a `librarian-rule` fact (the note is dropped from the rule when it reproduces item text).

## D-067 guard design (5 lines)
1. Reference guard: only ids from the call's candidate set count (unknown/duplicate dropped, missing = abstention).
2. Evidence guard: a raised relation must quote 2+ words of each side (normalized substring), else it is never an action.
3. Temporal guard: a supersession must point to the later `t_valid`; the calibrated tier table makes low confidence at most a question, weak link guesses an abstention.
4. High-impact (contradicts+supersedes, cross-project dup/refine → widen) needs an independent verifier call (decomposed same_subject/conflict/current, other profile) to agree before anything is raised; disagreement downgrades or drops, both calls audited.
5. Abstention is the prompts' default ("none" when unsure), counted in the audit and scored in G-LIVE-B.
