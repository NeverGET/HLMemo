# 127: D-274 account self-service project creation: threat model and rubric

Written before the review (D-266). The owner asked for this on 2026-11-01: "a person should be able to create projects
inside their own account; it is their private space".

## The change
- **Account.** An account is a root project `<account>-global`.
  - The root's `projects.policy` carries `account: "<account>"`, `account_root: true` and `project_quota: N`.
  - Only the operator sets these, with `python -m hlmemo.ops account set <account> [--quota N] [--claim]` over SSH.
  - An account name matches `^[a-z0-9]{2,32}$`. It has no hyphen, so one account's prefix can never contain
    another's.
  - Reserved names are refused: `hlm`, `owner`, `admin`, `librarian`, `mig`, `system`, `root`.
- **Membership.** A device is a member of an account while it holds a live grant of `write` or `admin` on the root.
  - "Live" means: the grant is not revoked, and the device is trusted, not revoked and not expired.
  - The request's `AuthContext` decides it. That context is resolved from the bearer, never from the payload.
- **Project creation.** The `hlm.project_create {slug, name}` client tool.
  - It is dispatched over MCP but never advertised on `tools/list`, so the agent surface does not grow.
  - The CLI `hlm project create` calls it unless `--admin` is given.
  - The slug must be `<account>-<rest>`, with `rest` non-empty, and must match the project slug pattern.
  - The account is derived from the slug's prefix, never from a field the caller supplies.
- **Effects.**
  - The root project row is locked (`FOR UPDATE`) and the quota is checked under that lock.
  - The project is inserted with `policy.account = <account>`.
  - Every live member device gets `write`, and device 1 gets `admin`, as with `ops project create`.
  - Events: `project_created` and one `grant_added` per device.
  - The skeleton card is written by the operator context, as `ops project create` does.
- **Re-creating an existing slug.** If the slug already belongs to the account, the call is idempotent:
  `created: false` and no grant change. If it exists outside the account, the call is refused.
- **No schema change.** The keys live in the existing `projects.policy` jsonb. Replay keeps `projects` and resets
  only `REPLAYED_POLICY_KEYS`. Old code reads policy keys by name and ignores these.

## Assets
- Other people's memory, starting with the owner's projects and `hlm-global`.
- The integrity of grants: who can read or write which project.
- Server resources and LLM spend: every project feeds the librarian queue.

## Failure modes (what the review must try to break)
| # | Failure | Severity if real |
|---|---|---|
| T1 | A caller gains a grant on a project that is not their account's: an existing slug, another prefix, a reserved project, the root of another account, or a slug that was created by the operator with the account's prefix but never claimed | HIGH |
| T2 | Membership spoofing: a device without a live root grant creates projects. Variants: a revoked or expired device, a revoked grant, a `read` grant, device 1, the librarian system device, a stale `AuthContext` | HIGH |
| T3 | A non-root project, or a project carrying a forged `account` key, acts as a root. Only `ops account set` may make a root | HIGH |
| T4 | The new project is granted to devices outside the account, or to a revoked or expired member | HIGH |
| T5 | Quota bypass by a concurrent race, by counting the wrong set, or by an idempotent re-create that counts as a new one | MEDIUM |
| T6 | An existence oracle: refusals reveal whether projects outside the caller's account exist | MEDIUM (LOW inside one's own prefix) |
| T7 | Agent surface: the tool appears on `tools/list` or in the protocol digest, so any agent is prompted to create projects | MEDIUM |
| T8 | Rollback or replay breaks: G6 replay changes the policy keys or the grants, or an older release misreads the keys | HIGH if data changes; MEDIUM otherwise |
| T9 | `ops account set` claims projects of another person by its prefix (the `--claim` path), or silently drops existing grants | HIGH |
| T10 | Event or card gaps: a created project without a `project_created` event or a skeleton card, or a partially created project left behind by an error | MEDIUM |

## Rubric
- **HIGH:** any path that widens read or write access beyond the caller's account, or that changes stored memory or
  grants unexpectedly. Needs a reproducing test and must be fixed before release.
- **MEDIUM:** a bypass of a limit, a misleading result, an oracle across accounts, or a contract-surface leak. Fixed
  before release, or accepted by the owner with a reason.
- **LOW:** clarity, docs, cheap hardening.

## Out of scope
- The pre-import dump. It stays with the operator, because it needs SSH.
- Archiving or deleting projects.
- A per-account LLM budget. The spend caps stay global.
- Moving devices between accounts other than by grants on the root.
