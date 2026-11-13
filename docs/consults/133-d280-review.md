# 133: D-280 a background share of the spend caps: review record

Threat model and rubric: `133-d280-threat-model.md`. Two fresh-context review agents in parallel (D-266): Fable on
correctness, accounting and regressions; Sonnet on spend safety, availability, deployment and contracts. Both
read-only; neither ran a database test.

## Round 1

### Spend safety, availability, deployment, contracts (Sonnet): nothing above LOW
Found sound: the share only tightens the bound on the same counters; share 1 keeps the old statement; the row's
cap is written in full; settle and sweep never see the share; every other budget keeps share 1. The goal is met:
background reservations in flight count inside the 75%, and the remaining 0.75 USD of an hour covers six or more
worst-case `memory.ask` questions. A deploy with the old env passes (key absent); the installer writes the
template's value and recreates both services; an older release ignores the line.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| S1 | LOW | RUNBOOK: "raise the share and reinstall" was misleading, because an install writes the template's value | **Fixed** in the RUNBOOK and the template comment |
| S2 | LOW | "the last quarter stays theirs" over-promised: it is no floor | **Reworded** |
| S3 | LOW | The month window's effect was not named | **Added** to the RUNBOOK and D-280 |
| S4 | LOW | `remote_gates.sh --librarian` times out while the librarian waits at its share | **Noted** in the RUNBOOK |
| S5 | LOW | A hand edit of a bad value on the host stops both services at start | Existing pattern; the installer and the manifest catch it |

### Correctness, accounting, regressions (Fable): one MEDIUM
| # | Severity | Finding | Resolution |
|---|---|---|---|
| C1 | MEDIUM (T4) | A job waiting at a bound is redone from its first call on every hand-back and kept its call-ceiling slots; after about seven rounds it was failed with `E_CALL_CAP`. Present before D-280 at a full cap; the share makes waiting routine. Production on the import day: one lineage at 14 of 20 | **Fixed**: the slots a deferred run claimed are given back (`lineage_scope` counts them per lineage, `Provider.refund_claims`, `Ledger.refund`). Reproducing test: without the refund the job is failed at round 21; with it 30 rounds leave it queued with a counter of 0 |
| C2 | LOW, but a red test | `ops/service.py` read the setting from a test's `SimpleNamespace` settings | **Fixed** with `getattr` |
| C3 | LOW | As S1 | Fixed |
| C4 | LOW | "`ops status` shows `breaker=budget` with `ready` above 0" is not always true | **Reworded** |
| C5 | LOW | `DbBudget` raised `InvalidOperation`, not `ValueError`, for "abc" or NaN | **Fixed**, with a test |
| C6 | LOW | No sanity floor: a share so small that one call's worst case never fits is accepted | Left; BACKLOG |
| C7 | LOW | The startup line named a share with the guard disabled; the manifest cannot tell that an env lacks the line; an owner's custom-answer re-check also waits at the share | First fixed; the others recorded |

Tests added on the reviewer's list: the worker-level hand-back at the share, the factory with the default float
share, the bad values.

## Round 2
One fresh-context agent (Sonnet) on the C1 fix, read-only.

- **C1: closed.** The only `raise BudgetDeferred` in `src` follows a refused reservation, so no plan can earn free
  slots; every redone call reserves against the caps again, so money stays bounded; follow-up jobs are enqueued
  only after a successful plan; each scope refunds only its own claims and the decrement is atomic;
  `lineage_scope` has one caller and no nesting. The test goes through the worker with a real ledger and guard
  and cannot pass for a wrong reason.
- **The shadow pipeline's slots are refunded too** when the live path defers (it has its own lineage inside the
  same scope). Its own limit is its day cap in dollars, checked at every run, so the call ceiling no longer bounds
  a shadow that keeps waiting; the day cap and the spend caps do. Accepted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| R1 | LOW | A refund that raises inside the `except` branch would leave the job leased until its lease expires | **Fixed**: the refund is wrapped, the hand-back always runs |
| R2 | LOW | The same slot leak exists on a `ProviderUnavailable` hand-back (before D-280): bounded per outage round, stops at an open breaker | BACKLOG |
| R3 | LOW | RUNBOOK: the "narrow band" sentence was too narrow | **Reworded** |

Nothing at MEDIUM or above remains. No residual risk needs a decision.
