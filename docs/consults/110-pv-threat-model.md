# PV-1..PV-5 write-path validations: threat model and severity rubric (before review)

Written 2026-10-07, before the implementation is reviewed (CLAUDE.md: one-way doors get the threat model and the
rubric first). Scope: the five checks of protocol §5.2 on `memory.write`, `memory.call_the_day`,
`memory.register_lesson` and the MCP handlers (`server/tools/handlers.py`, `server/tools/risk.py`).

## What is protected
- **Integrity of an append-only store.** A refused write must leave no trace: no item, no event, no request-ledger row
  that holds the refused payload.
- **Secrecy.** A secret value must not be stored and must not be echoed (error message, `details`, server log, event,
  idempotency record).
- **Availability of legitimate writes.** Writers must not be blocked by a check that legitimate text trips. The cost
  of a false reject is a writer that routes around the rule, or a lost lesson.
- **Replay determinism.** Historic events must replay byte-identically. `db/replay.py` must not run the new checks.
- **The error contract.** Only codes from the closed list (`auth/errors.py`), with `details.reason`. The HTTP mapping
  and the spec stay unchanged.

## Failure modes to look for
1. **False reject** of a legitimate write class: placeholders (`sk-...`, `<token>`), JWT-shaped example strings in
   docs, code identifiers, long hex digests (sha256 is not a secret), Turkish or other non-ASCII text, NBSP handling in
   PV-3, tag case variants in PV-5. This is measured on real items before review (MEASURE.md, private).
2. **Bypass through an unchecked field:** tags, `card_update.body`, `notes`, `decisions[]`, register_lesson
   `context`, `updates[].replacement`, link targets, `title`; the `$i` and integer targets in PV-2; `project_ids` in
   PV-4.
3. **Persistence before validation:** the request ledger, the idempotency key or an event written before the check
   runs, so a refused secret is still stored; or a replayed `request_id` that skips the check.
4. **Echo:** the value inside `message`, `details`, an exception string or an INFO/ERROR log line.
5. **Replay divergence:** any check reachable from replay, or a migration of historic data.
6. **Exemption abuse:** PV-2 exempts items with `source`. A writer could add a fake `source` to raw-link anyway. Is
   that acceptable, given that imports must stay exempt?
7. **Contract drift:** a new code, a changed HTTP status, `retryable` set wrongly, or a different envelope shape.

## Severity rubric
- **HIGH:** a measured false-reject class of legitimate writes; a secret value persisted or echoed anywhere; replay
  divergence; a check bypassed through a supported field with ordinary input. A HIGH needs a reproducing test.
- **MEDIUM:** a bypass that needs unusual input (zero-width splitting, encodings); an inconsistent error shape; a
  missing test for a listed case.
- **LOW:** documentation, naming or test-structure gaps.

## Review plan
Codex `gpt-6-astra` low and `gpt-5.6-sol` xhigh in parallel on the branch diff plus this file and MEASURE's counts
(no private content). At most 2 rounds. Every HIGH gets a reproducing test. The owner decides any residual risk,
for example failure mode 6, then deploys.
