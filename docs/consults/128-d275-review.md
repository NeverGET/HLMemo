# 128: D-275 review (round 1)

Two fresh-context reviewers ran in parallel on the threat model (`128-d275-threat-model.md`) and the diff. Sonnet
took security, privacy and contracts; Fable took correctness and regressions. Neither found a HIGH. One round was
enough, because every MEDIUM got a test.

| # | Severity | Finding | Resolution | Test |
|---|---|---|---|---|
| S-1 | MEDIUM | The `memory.query` synthesis (`synthesis_service`) had no isolation check. The admin device could put a multi-project `[home, guest]` item's excerpt into a home-project prompt. This was true before D-275 for `exclude` too. | `_excerpts` drops an excerpt outside the home's group before the LLM; the recheck's `authz` applies the rule; the caps carry `isolation_home`, so the privacy gate rechecks it. | `test_excerpts_outside_the_home_isolation_group_never_reach_the_prompt` (fails without the fix) |
| S-2 | MEDIUM (latent) | `pair_check` passed no `isolation_home` to the privacy gate. No production path enqueues it. | The plan sets `isolation_home` to the subject's project, so every gate and precheck applies the rule. | — (no production enqueuer) |
| S-3 / C-1 | LOW | SQL and Python disagreed on an empty-string account. | The SQL uses `NULLIF(…, '')`, like Python's falsy rule. | — |
| C-5 | LOW | The SQL treated an unknown project id as the default group. | The SQL now states the rule positively: `project_ids <@ same_ids` (the home group's projects), which is exactly the Python rule. | existing risk tests |
| S-5 / C-2 | LOW | A stale type on `_candidates`. | Now `lq.Groups`. | — |
| S-5 / C-3 | LOW | The audit field `cross_project_excluded` now also lists account projects. | Kept: it lists every project outside the default group. Documented in D-275. | `test_test_project_is_isolated_in_both_directions[account]` |
| S-4 | LOW | Librarian rule texts (`librarian-rule` facts: clue ids and relation names, no content) of an account can reach operator prompts. | Accepted. They carry no item content. | — |
| S-6 | LOW | Stale docstrings that said "exclude only". | Updated in privacy, actor, risk_service and memory_map. | — |
| C-4 | LOW | Account variants are untested for the ask gate, the map, `updates` and `policy_blocked`. | These share `relation_allowed`, which is covered by the unit test and the write_review and risk_check integration tests. The `exclude` variants of those paths still run. | — |

## Proofs from the correctness review
- The old `exclude` semantics hold exactly in Python, checked exhaustively over every excluded subset of four
  projects. They also hold in SQL, by derivation.
- A NULL or unknown home falls into the default group in both Python and SQL.
- `exclude` combined with an account resolves to `x:` in both.
- The locks are unchanged: `FOR SHARE` on every touched row.
- `ops account set` locks its rows `FOR UPDATE`.

## Author's checks
Each new test fails without its fix:
- the unit test of the account group;
- `[account]` of both librarian tests;
- the risk_check account steps;
- the synthesis excerpt test.
