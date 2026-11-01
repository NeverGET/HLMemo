# 128: D-275 an account is an isolation group: threat model and rubric

Written before the review (D-266). The owner decided on 2026-11-01, after review 127 S3: "keep the guest's data out
of operator access; if the guest asks for it, we will look at it then".

## The change
- **Before.** One rule (`candidates.relation_allowed`) decided whether two sides may be related. It was used for the
  librarian's candidates, the apply-time recheck, cross-project `updates`, `risk_check`, the `memory.ask` gates and
  the memory map. It isolated only projects with `policy.librarian_cross_project = exclude`. Every other project
  the triggering device could read was fair game. Device 1 reads every project, so its writes, `risk_check`s and
  `memory.ask`s could pull a guest account's items into operator-side work.
- **After.** Every project has an isolation group (`librarian_queries.isolation_key`):
  - `x:<id>` for an `exclude` project, which is stricter than an account;
  - `a:<account>` for a project whose `policy.account` is set (D-274);
  - `""` otherwise.

  Two sides may be related only when every project they touch is in one group.
  - `isolated_scope` keeps the allowed projects of the subject's group.
  - `risk_queries` computes the same group in SQL, so the risk universe is the home's group.
  - `privacy.py` builds the groups inline from the same key function.
- **Unchanged.**
  - Explicit reads: device 1 may still `memory.query` an account project by name. That is operator access the
    owner keeps for support and deletion.
  - Grants, schema and replay. `policy.account` is not a replayed key and is kept by replay.

## Assets and threats
- **T1.** A guest item reaches operator-side librarian work: a candidate, a prompt, a proposal, a risk warning, an
  ask answer or a map summary. It does so through any path that uses the isolation rule.
- **T2.** An operator or another account's item reaches a guest's work.
- **T3.** Regression: an `exclude` project is no longer isolated, or loses its own self-review.
- **T4.** Regression: the default group (the owner's projects, `hlm-global`) can no longer relate across its own
  projects.
- **T5.** Regression: an account's own projects (`<account>-global` and `<account>-app`) can no longer relate. For
  example, `risk_check` in `maher-app` must still warn with a `maher-global` lesson.
- **T6.** Mismatch: the Python rule and the SQL risk universe disagree. Examples: a NULL home, a home that does not
  exist, a project that is both `exclude` and an account, an empty account string.
- **T7.** A path that decides isolation without this rule, and so still leaks.
- **T8.** Locking: the apply-time recheck (`lock=True`) must still lock every touched project row, so that a
  concurrent `ops account set` or `policy set` is ordered against it.

## Rubric
- **HIGH:** a reachable leak across groups (T1, T2, T7), or a broken `exclude` isolation (T3). It needs a reproducing
  test.
- **MEDIUM:** a regression that silently disables same-group relations (T4, T5), a Python/SQL mismatch on a
  reachable input (T6), or a lost lock (T8).
- **LOW:** docs, naming, audit field semantics, unreachable edges.
