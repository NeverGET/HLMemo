# 130: D-279 the owner account: review record

Threat model and rubric: `130-d279-threat-model.md`. Two fresh-context review agents ran in parallel on the diff
(D-266): Fable on correctness and regressions, Sonnet on security, privacy and contracts. Neither wrote the change,
both worked read-only, and neither ran a database test (the integration suite had the one test database).

## Round 1

### Security, privacy and contracts (Sonnet): nothing above LOW
Examined and found sound: T1, T3 (membership, the root, no client path to the new keys), T2 (routing for guests
unchanged), T4 (grants only on the new row, to device 1 and the live members), T5 (the three isolation computations
read only `policy.account` and `librarian_cross_project`), T6, T7, T8 (one refusal, reads depend on the slug's shape
only, no lock for a non-member), T9 (still unlisted; no agent-facing text changed), T11.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| S1 | LOW | A race: the operator commits `account set <x>` after the owner create read `<x>-global` as missing | Narrowed: the owner path re-reads the root under its lock. The rest is documented (C2) |
| S2 | LOW | An owner member learns whether `<x>-global` exists from the refusal | Accepted: only a device the owner trusts can ask |
| S3 | LOW | Rollback: an older release's `account set <name> --claim` would claim owner-created projects | Documented in USAGE |
| S4 | note | Membership is not rechecked after the root lock, as in D-274 | Inherited, unchanged |

### Correctness and regressions (Fable): one finding above LOW
The D-274 path was compared line by line with `main`: same order of checks, same locks, same results; the only
difference is one extra unlocked read of `owner-global`. Replay tolerates the new event. No new deadlock cycle.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| C1 | HIGH by the rubric (T2); no access or isolation effect | `maher-` (an empty rest) escaped the namespace check: `account_of` returned None, so an owner member could create it inside a live account's namespace, and later `ops account set maher` runs failed | **Fixed.** `namespace_of` routes on the first segment alone; a root row with a slug no account creates gives the one refusal; the owner path also refuses a trailing hyphen. Reproducing test: `(mac, "maher-")` created the project on the pre-fix code and is refused now |
| C2 | LOW | The same end state by a race with the operator | Narrowed by the re-read under the lock; the comment now says what remains |
| C3 | LOW | "Until the operator resolves it" has no ops command | Documented in USAGE; BACKLOG |
| C4 | LOW | `account set owner` succeeded on an archived `owner-global` | **Fixed**, with tests for the archived, account-tagged and reserved cases |
| C5 | LOW | A plain `<x>-global` closes the prefix with a "needs membership" refusal | Documented in USAGE |
| C6 | LOW | As S3 | Documented in USAGE |
| C7 | LOW | A unique violation gives the generic database error | Same on `main`; unchanged |

Tests added on the reviewer's list: `maher-`, `nobody-`, the `_owner_account_set` refusals, a quota lowered below
`used`, a member who joins later.

## Round 2
One fresh-context agent (Sonnet) on the fixes, read-only, pure helper calls only.

- **C1: closed.** `namespace_of` and the operator's `starts_with(slug, '<account>-')` are equivalent in both
  directions, because an account name holds no hyphen. Enumerated: `maher-`, `maher--`, `maher-global-`, `12-ab`
  agree; a one-character head, a 33-character head and the reserved heads give no namespace on either side.
- **C2, C3, C5, C6: closed as documented. C4: closed.**
- **The D-274 path against `main`:** a non-member gets the same refusal for every shape walked (root exists, root
  archived or forged, the root's own slug, `<account>-`, a reserved head, device 1). A guest member behaves as on
  `main`. One input differs, for an owner member only: `<account>-global` now gives the member-only "-global"
  message instead of the general refusal.
- **Oracles, locks, deadlocks:** none new. The re-read after the lock is reached by members only.
- **New, LOW:** re-creating an existing owner project `foo-bar` gives the general refusal, not `created: false`,
  once a project `foo-global` exists. It follows from C5 and is covered by the USAGE sentence on a closed prefix.

Nothing at MEDIUM or above remains. No residual risk needs an owner decision.
