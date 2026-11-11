# 130: D-279 the owner account (an owner device creates unprefixed projects): threat model and rubric

Written before the review (D-266). The owner asked on 2026-11-11, after his second device joined (D-278): that
device should create projects itself, without a prefix, and its projects should sit "at the same level" as the ones
made from the first machine: the same isolation group, no account wall. Today only the operator creates such
projects, over SSH, and the second device holds no SSH key.

## The change
- **The owner account.** One root project, `owner-global`, whose `projects.policy` carries `owner_root: true` and
  `project_quota: N`.
  - Only the operator sets these: `python -m hlmemo.ops account set owner [--quota N]` over SSH. The root must
    exist first (`ops project create owner-global`). `--claim` is refused for it.
  - `owner` stays a reserved account name for every other purpose: no slug prefix maps to it (`account_of`).
- **Membership.** A device is a member while it holds a live `write` or `admin` grant on `owner-global`, exactly as
  for a D-274 account (the request's `AuthContext`, resolved from the bearer; device 1 never counts).
- **Routing in `hlm.project_create {slug, name}`** (the tool, its schema and the CLI are unchanged):
  1. If the slug is `<account>-<rest>` and a project row `<account>-global` exists, the D-274 path runs unchanged
     (membership of that account, or the one refusal). An owner member has no say in an account's namespace.
  2. Otherwise the owner path runs: the caller must be a member of the owner account, else the same single refusal
     as D-274 (`_NOT_MEMBER`, same code, message and details).
- **Slug rules on the owner path** (checked after membership, so only a member sees them): refused are the
  reserved projects, a slug whose first segment is a reserved account name (`hlm`, `owner`, `admin`, `librarian`,
  `mig`, `system`, `root`), and a slug ending in `-global` (roots are the operator's to make).
- **Effects.** As D-274: the root row is locked `FOR UPDATE`, the quota is checked under the lock, the project is
  inserted, device 1 gets `admin` and every live member gets `write`, the events are `project_created` and one
  `grant_added` per device, and the skeleton card is written.
- **The tag.** The new project gets `policy.owner_created: true`, which the quota counts. It gets **no**
  `policy.account` key, so the isolation code (`isolation_key`, the `risk_queries` SQL, the privacy gate) is not
  touched and reads the project as an ordinary default-group project, like one made by `ops project create`.
- **Re-creating.** An existing slug with `owner_created` is an idempotent no-op (`created: false`, no grant
  change). Any other existing slug is refused ("ask the operator"), including the owner's operator-made projects.
- **`ops account set <name>` for another account** now refuses, even with `--claim`, to claim a project that carries
  `owner_created`.
- **No schema change, no isolation change.** New keys in the existing `projects.policy` jsonb; replay keeps
  `projects` and resets only `REPLAYED_POLICY_KEYS`.

## Assets
- Guest accounts' memory and namespaces (D-274, D-275): an owner device must gain nothing there, and a guest device
  must gain nothing from the new path.
- The integrity of grants and of the isolation groups.
- The system namespace (`hlm-*`, roots) and the operator's role.
- Server resources and LLM spend: every project feeds the librarian queue.

## Failure modes (what the review must try to break)
| # | Failure | Severity if real |
|---|---|---|
| T1 | A device that is not an owner member creates a project through the new path: a guest member, a device with grants on owner projects but not on the root, a `read` grant on the root, a revoked or expired device, a revoked grant, device 1, the librarian system device | HIGH |
| T2 | The new path reaches into an account's namespace: an owner member creates or is granted `<account>-...` while `<account>-global` exists (root, forged root or archived), or the D-274 path's behaviour changes for any caller | HIGH |
| T3 | A project that is not `owner-global`, or a forged `owner_root` key (a string, another slug, an archived root), acts as the owner root. Only `ops account set owner` may make it | HIGH |
| T4 | The new project is granted to devices that are not live owner members, or the caller gains a grant on an existing project (an operator-made one, a guest's, a reserved one) | HIGH |
| T5 | Isolation drift: an owner-created project lands outside the default group, or any existing project's group changes; a guest project becomes visible to default-group work | HIGH |
| T6 | A reserved or system slug is created (`hlm-...`, `owner-...`, `...-global`, a reserved project), or a later `ops account set` silently claims an owner-created project | MEDIUM |
| T7 | Quota bypass: a concurrent race, the wrong counted set, an idempotent re-create counted as new | MEDIUM |
| T8 | A new oracle for non-members: the refusal differs between "no owner root", "not a member" and "bad slug", in code, message, details or lock timing (a non-member must never take the root lock) | MEDIUM |
| T9 | Agent surface: the tool appears on `tools/list`, or the protocol digest starts prompting agents to create projects | MEDIUM |
| T10 | Rollback or replay: G6 replay changes the new keys or the grants; an older release misreads the keys | HIGH if data changes; MEDIUM otherwise |
| T11 | Event or card gaps, or a partially created project left by an error | MEDIUM |

## Accepted by design (state, do not re-litigate)
- An owner member learns whether an unprefixed slug exists (the refusal for an existing slug differs from success).
  Those projects are the owner's or the operator's own; guest slugs go through path 1 and give the one refusal.
- An owner member can create `<name>-<rest>` while no `<name>-global` row exists. A later `ops account set <name>`
  then stops on that project (it carries `owner_created`) until the operator resolves it.
- Membership of the owner account is a write grant on a project that holds no memory. It is separate from
  `hlm-global`, so the right to create projects and the right to write global lessons stay separate grants.

## Rubric
- **HIGH:** any path that widens read or write access, creates a project for a non-member, touches an account's
  namespace, or changes an isolation group. Needs a reproducing test and must be fixed before release.
- **MEDIUM:** a bypass of a limit, a reserved slug created, a new oracle, a contract-surface leak, a replay
  difference without data change. Fixed before release, or accepted by the owner with a reason.
- **LOW:** clarity, docs, cheap hardening.

## Out of scope
- The pre-import dump of a migration (operator, SSH) and every other operator command.
- Archiving or deleting projects.
