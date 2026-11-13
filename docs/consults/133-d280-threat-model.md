# 133: D-280 a background share of the spend caps: threat model and rubric

Written before the review (D-266). Decided with Cemal on 2026-11-13: background librarian work stops at a share of
each cap and leaves the rest to the interactive tasks.

## Why
A 1,754-item import on 2026-11-13 used the whole day cap (8 USD) by 09:50 UTC through its write reviews. From then
until midnight UTC every `memory.ask` and every risk judge call was refused with `budget`, for every user. The caps
are one shared runaway guard (D-058); nothing separates work that can wait from work a person is waiting for.

## The change
- **Setting** `llm_budget_background_share` (`HLM_LLM_BUDGET_BACKGROUND_SHARE`), a number in (0, 1], default `1`:
  unset means today's behaviour exactly.
- **`DbBudget.share`.** A reservation succeeds in a window only while
  `spent + reserved + worst <= cap × share`. The stored `cap_usd` of the window row stays the full cap.
- **Who is background.** The librarian worker process: its provider is built by `Provider.from_settings(...,
  background=True)` (the one caller, `librarian/worker.py`), so every task it runs (the write review, its verifier,
  the shadow pipeline, placement, map summaries) reserves with the share. The API process's providers
  (`memory.ask`, the risk judge, synthesis) keep share `1`.
- **All three windows** (hour, day, month) use the same share.
- **What a background job does at its share:** what it does at a cap today: `budget_deferred`, handed back for
  60 seconds, no attempt consumed, nothing lost.
- **Deployment:** `deploy/llm.env.example` sets `0.75`; the release manifest (`check_librarian.py`) accepts the key
  absent or as a number in (0, 1]; `hlmemo.ops status` and the librarian's startup line show it.
- No schema change. `MemoryBudget` and `NoBudget` are untouched.

## Assets
- The caps as a runaway guard: total spend must never exceed a cap because of this change.
- Availability of the interactive tasks during a large import.
- The librarian's liveness: background work must still finish.
- The append-only ledger and the reservation accounting.

## Failure modes (what the review must try to break)
| # | Failure | Severity if real |
|---|---|---|
| T1 | Total spend can exceed a cap: the share is applied as a second budget instead of a tighter bound on the same counters, an interactive reservation ignores background reservations, or a rounding path lets `cap × share` exceed `cap` | HIGH |
| T2 | With the setting unset (share 1) any reservation outcome differs from today's (arithmetic on `numeric`, quantisation of `cap × share`, the comparison at the exact boundary) | MEDIUM |
| T3 | An interactive call is treated as background (it would be refused early), or a background call as interactive (the share would not bind): every construction site of a budget, including benches and tests | MEDIUM |
| T4 | A background job stuck at its share is failed, dropped or burns attempts instead of waiting; or the worker spins (the pause no longer applies) | MEDIUM |
| T5 | A bad value (0, negative, above 1, not a number, an empty line) starts the service with a share that disables the guard or the librarian; the manifest lets it through | MEDIUM |
| T6 | The window row's `cap_usd` is written with the scaled value, so the API reads a smaller cap too | MEDIUM |
| T7 | Settling or sweeping a background reservation mis-accounts (the share must not enter `settle` or `sweep`) | MEDIUM |
| T8 | The env installer drops or rewrites the new line on a normal install, or treats it as an operator-preserved cap | LOW |

## Accepted by design
- Interactive tasks are not reserved a floor of their own: heavy interactive use lowers what is left for
  background work, and background work already spent counts against the interactive tasks' full cap. The share
  only guarantees that background work alone cannot take the last `1 − share` of a window.
- A job at its share is retried every 60 seconds and each try writes a `budget_deferred` ledger row, as at a cap
  today (BACKLOG).

## Rubric
- **HIGH:** spend above a cap, or reservation accounting that loses or double-counts money.
- **MEDIUM:** behaviour drift when unset, a misclassified caller, a background job lost or failed by the share, a
  bad value accepted, the stored cap changed.
- **LOW:** docs, display, naming.
