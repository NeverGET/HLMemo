## Round-1 closure

Astra F-1 | CLOSED | `src/hlmemo/core/research_service.py:1897-1931,2062-2064`; `test_as_of_excludes_revoked_view_item`, `test_as_of_excludes_superseded_item` | —

Astra F-2 | PARTIAL | `src/hlmemo/librarian/tasks/research.py:2453-2487`; date/prose-boundary tests pass | Different indentation or delimiter does not end a list: after item 1 drops, `2. kept` followed by `3) separate` is still renumbered across the boundary.

Astra F-3 | CLOSED | `src/hlmemo/importers/common.py:169-193`; `test_non_secret_setting_is_not_a_hit`, `test_a_file_with_a_non_secret_setting_is_read` | The false-positive is closed; the new false-negative regression is N-1.

Astra F-4 | CLOSED | `src/hlmemo/librarian/provider.py:128-168`; `test_rate_limits_are_not_billing` | —

Astra F-5 | CLOSED | `src/hlmemo/librarian/tasks/research.py:2660-2670,2688-2709`; first-sentence and first-code-block truncation tests | —

Sol F-1 | PARTIAL | `src/hlmemo/importers/common.py:178-193`; `test_generic_key_assignment_is_blocked` covers `HMAC_KEY` | Genuine names including `PRIMARY_API_KEY`, `CACHE_API_KEY`, `APIToken`, and `JWTToken` remain undetected.

Sol F-2 | CLOSED | `alembic/versions/0010_billing_outcome.py:39-60`; `test_migration_0010_final_constraint_name_and_rerun_after_a_leftover` | The original long `ACCESS EXCLUSIVE` validation is removed; the downgrade-specific regression is N-2.

Sol F-3 | PARTIAL | `src/hlmemo/librarian/tasks/research.py:2453-2487` | Different indentation/delimiter and semantic step identifiers can still be renumbered across list boundaries.

Sol F-4 | CLOSED | `src/hlmemo/core/research_service.py:1897-1931,2062-2064`; `test_as_of_excludes_revoked_view_item` | —

Sol F-5 | CLOSED | `src/hlmemo/importers/common.py:169-193`; negative regressions for `TOKENIZER`, `TOKEN_BUDGET`, and `MAX_TOKENS` | —

## New findings

### N-1 — CRITICAL — T2 — `src/hlmemo/importers/common.py:173-193`

**Scenario:** The component-based fix suppresses genuine credential names. Files containing `PRIMARY_API_KEY=Abcd1234!`, `CACHE_API_KEY=...`, `ACCESS_TOKEN_PROD=...`, `APIToken=...`, or `JWTToken=...` pass `secret_hit`, can be imported into memory, and can later reach a provider. These cases were caught at `0821f98`; they return `None` at `2cad563`.

> `_NOT_KEY_COMPONENTS = frozenset({"public", "sort", "primary", "foreign", "partition", "cache", "routing"})`  
> `return bool(parts) and parts[-1] == "key" and not _NOT_KEY_COMPONENTS.intersection(parts)`

**Minimal fix:** Give strong composites such as `api + key` precedence; recognize `token` components regardless of trailing environment qualifiers; support acronym-to-word camel boundaries; restrict non-credential exemptions to exact names such as `PUBLIC_KEY` rather than any matching component.

**Reproducer:**

```python
@pytest.mark.parametrize("name", [
    "PRIMARY_API_KEY",
    "CACHE_API_KEY",
    "ACCESS_TOKEN_PROD",
    "APIToken",
    "JWTToken",
])
def test_real_credential_names_are_blocked(name):
    assert common.secret_hit(f"{name}=Abcd1234!") == "env-secret-assignment"
```

### N-2 — MEDIUM — T6 — `alembic/versions/0010_billing_outcome.py:45-49,63-68`

**Scenario:** Downgrade commits `billing_or_quota → http_error` before installing the OLD staged constraint. An active R4.1 writer can insert another `billing_or_quota` row in that gap; `ADD ... NOT VALID` accepts the pre-existing row, then `VALIDATE` fails. Death after the update also leaves `alembic_version=0010` while historical classifications are already rewritten.

> `"UPDATE llm_calls SET outcome = 'http_error' WHERE outcome = 'billing_or_quota'"`  
> `_swap(OLD)`

**Minimal fix:** For downgrade, add the OLD constraint `NOT VALID` first so it immediately blocks new incompatible writes; then transform existing rows, validate in a separate transaction, and atomically drop/rename. Require interrupted downgrade to be rerun or restored before service restart.

A temporary PostgreSQL reproducer produced `CheckViolation` with both `http_error` and the raced `billing_or_quota` row present.

## Migration 0010

- **Upgrade staging: PASS.** `_swap(NEW)` adds `_v2` as `NOT VALID`, validates it in a separate autocommit transaction, then executes drop/rename as one PostgreSQL implicit transaction. `lock_timeout=3s` covers final lock acquisition.
- **Upgrade death/re-run: PASS by inspection and staged-leftover test.** Death after add or validate leaves the old constraint plus `_v2`; rerun drops and recreates `_v2`. Death after the final swap but before Alembic stamps 0010 leaves version 0009 with the new canonical constraint; rerun remains idempotent.
- **Final swap atomicity: PASS.** A live PostgreSQL fault test made the rename fail after the drop statement; the old constraint remained, proving the two-command string rolled back atomically.
- **Downgrade symmetry: PARTIAL.** The constraint swap itself is symmetric and rerunnable, but the separately committed data update creates N-2. With writers quiesced, rerunning reaches the intended state.
- **R4 rollback to `5025db5`: PASS for the documented path.** `deploy.sh --rollback` stops writers, selects the previous code/image, and restores the recorded pre-upgrade dump before starting R4 (`deploy/RUNBOOK.md:968-989`). This restores schema 0009 and pre-upgrade rows. A code-only checkout is not an equivalent rollback.
- **Verification:** 166 related unit tests passed; both live PostgreSQL 0010 integration tests passed; affected-file Ruff checks passed. No production rollback or kill-at-every-boundary drill was performed.

## Residual risks

R-1 | ACCEPT / REJECT | List renumbering can still cross indentation/delimiter boundaries or alter semantic step identifiers.

R-2 | ACCEPT / REJECT | An interrupted/manual downgrade can leave version 0010 with billing history already normalized; the production dump rollback avoids this path.

R-3 | ACCEPT / REJECT | `VALIDATE` and the downgrade normalization update have no total-duration timeout; maintenance time can grow with the ledger.

R-4 | ACCEPT / REJECT | Full rollback to `5025db5` intentionally discards writes made after the pre-upgrade dump.

R-5 | ACCEPT / REJECT | Crash recovery was inspected and partially fault-tested, but no OS-level kill was injected at every migration boundary.

## Verdict

**NO-GO — N-1 is a CRITICAL secret-exposure regression introduced by the fix commits.** Additionally, Astra F-2, Sol F-1, and Sol F-3 remain PARTIAL, and N-2 leaves downgrade symmetry incomplete under concurrency or interruption.