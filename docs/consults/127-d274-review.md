# 127: D-274 account self-service project creation: review record

Threat model and rubric: `127-d274-threat-model.md`. There were two fresh-context reviewers in parallel (D-266).
Neither wrote the change. Reviewers did not run integration tests; the author ran them.
- **Correctness and regressions:** Fable.
- **Security, privacy and contracts:** Sonnet.

Neither reviewer found a HIGH. The security reviewer could not break T1, T2, T3, T7 or T9.

## Findings and fixes
| # | Severity | Finding | Fix | Test |
|---|---|---|---|---|
| S1 | MEDIUM | `live_members` was a plain SELECT. A member revoked between that read and the grants still got `write` on the new project. | The read now locks with `FOR SHARE OF g, d`, so a revoke waits or goes first. | `test_a_concurrent_member_removal_waits_for_the_create` (two connections) |
| S2 / C3 | MEDIUM / LOW | The root was locked `FOR UPDATE` before the membership check. A non-member could hold another account's root and time its existence. | The root is read unlocked, then `is_root` and the caller's grant are checked, then it is locked and `is_root` is re-checked. | existing refusal tests |
| S3 | MEDIUM, owner's call | Guest-written items can enter the librarian's cross-project candidates of writes made by device 1 (the operator path), which holds admin on every project. | Not changed. The owner's own devices hold no grant on account projects, so their writes never see them. This was already true of operator-created guest projects. Recorded in D-274. | — |
| S4 | LOW | `SLUG_RE` and `ACCOUNT_RE` accepted a trailing newline (`$` with `re.match`). | `fullmatch` in the service and in `ops account set`. | `test_input_edges` |
| S5 | LOW | The idempotent path returned the project's id and name, and `granted` listed member device ids. | The idempotent path returns only the slug; `granted` is a count. | `test_input_edges` |
| S6 | LOW | Removing a member does not cascade to the account's projects. | Documented in USAGE: `device revoke` removes every grant; revoking only the root grant keeps the others. | — |
| S7 | LOW | Any agent on a member device can call the tool by name. | Accepted: guests must be able to call it, and the quota bounds it. | — |
| C1 | MEDIUM | The rollback after a partial insert was unproven. | Test added. | `test_a_failure_after_the_insert_leaves_nothing` |
| C2 | MEDIUM | Untested: device 1, an archived root, `account set --quota` on an existing root. | Tests added. The CLI branch has no unit test. | `test_device_one_an_archived_root_and_a_quota_change` |
| C4 | LOW | Archived projects count against the quota. | Kept on purpose (archiving frees nothing) and documented. | — |
| C5 | LOW | USAGE still said `create` needs device 1. | Fixed. | — |
| C6 | LOW | The CLI said "nothing changed" for a project the caller may hold no grant on. | The message now says to ask the operator for a grant. | — |

## Author's own checks
- Mutation runs:
  - With the membership check removed, 2 tests fail.
  - With the root lock removed, the concurrent quota test fails.
- My first race test hung for minutes: a rollback undid its `SET lock_timeout`. Fixed, and recorded as lesson
  v4200.

Round 2 is not needed: no HIGH is open and each fix has a test. The owner decides S3.
