## Checks

| Check | Result | Assessment |
|---|---|---|
| C1 | PARTIAL | 0010 is not strictly online-safe: `lock_timeout` bounds lock acquisition, not the validating table scan. The documented quiesced deploy and dump-restoring rollback are safe; a code-only R4 rollback on schema 0010 would fail exact-head readiness. `alembic/versions/0010_billing_outcome.py:27-36`; `deploy/RUNBOOK.md:516-525,968-989`; `src/hlmemo/server/app.py:74,251`. |
| C2 | NO | Neither line enters validation, claims, or sources: freshness is appended after assembly and the marker during final packing. Both are metered as answer text and cannot produce malformed JSON; insufficient space returns `E_BUDGET_TOO_SMALL`. `src/hlmemo/core/research_service.py:2058-2066,2137-2162`. |
| C3 | YES | The renumberer crosses non-list boundaries and matches any leading `N.`/`N)`, so dates, indented code, separate lists, and semantically significant step identifiers can change. Fenced-code contents are not directly rewritten. `src/hlmemo/librarian/tasks/research.py:2453-2473`. |
| C4 | PARTIAL | The initial view is caller/project-filtered, preventing static cross-project leakage. A concurrent grant, policy, or device-scope revocation can nevertheless leave an unused item in `run.view`, whose timestamp is returned without final reauthorization. `src/hlmemo/core/research_service.py:1895,2061-2062`. |
| C5 | PARTIAL | Broad substrings can misclassify an ordinary 403/429, but classification does not alter retry, fallback, or breaker mechanics. Provider bodies are not copied into logs, flags, or ops output. `src/hlmemo/librarian/provider.py:125-155,767-779,1138-1159`. |
| C6 | PARTIAL | Standard `DB_PASSWORD=...` and `export DB_PASSWORD=...` work, but generic `*_KEY` and standalone YAML `password:` can be missed. Substring matching causes whole-file false positives; skips return an explicit `secret-pattern:<rule>` reason. `src/hlmemo/importers/common.py:160-172,202-224`. |

No `deploy/` or `profiles/` file changed in the permitted diff, so the fingerprint/manifest implementation and `llm.env` remain unchanged. The existing runbook verifies the fingerprint against both API and librarian and preserves the rollback snapshot. `deploy/RUNBOOK.md:891-920`.

## Findings

| ID | Severity | Threat | Location | Summary |
|---|---|---|---|---|
| F-1 | CRITICAL | T2 | `src/hlmemo/importers/common.py:169-172` | The new rule misses the promised generic `*_KEY` format |
| F-2 | MEDIUM | T6 | `alembic/versions/0010_billing_outcome.py:27-36` | CHECK validation holds an uncapped heavyweight lock |
| F-3 | MEDIUM | T8 | `src/hlmemo/librarian/tasks/research.py:2453-2473` | Renumbering crosses semantic and list boundaries |
| F-4 | MEDIUM | T7 | `src/hlmemo/core/research_service.py:1895,2061-2062` | Freshness metadata bypasses the final authorization view |
| F-5 | MEDIUM | T8 | `src/hlmemo/importers/common.py:169-172` | Secret-name substring matching skips valuable files |

### F-1

**Failure scenario:** A curated `.env` contains `export HMAC_KEY=Abcd1234!`. D-213 promises `*_KEY` detection, but the new regex recognizes only `api[_-]?key` plus other named terms; the file can be imported and the credential later sent in a provider prompt.

**Evidence:**

> `r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*(?:secret|password|passwd|token|api[_-]?key)[A-Za-z0-9_]*)"`  
> `if not _NOT_SECRET_NAME_RE.search(name) and not _is_placeholder(value):`

**Minimal fix:** Match `key` as a complete variable-name component or suffix, while retaining the `_FILE/_PATH/_ID/...` exclusions and avoiding arbitrary substrings such as `TOKENIZER`.

**Reproducing test:**

```python
def test_generic_key_assignment_is_blocked() -> None:
    assert (
        common.secret_hit("export HMAC_KEY=Abcd1234!")
        == "env-secret-assignment"
    )
```

### F-2

**Failure scenario:** On a large accumulated `llm_calls` table, 0010 acquires an exclusive lock and scans every existing row while adding the validating CHECK. The three-second timeout limits only waiting for the lock, so the stopped-stack deployment can remain unavailable for the scan’s uncapped duration.

**Evidence:**

> `"ALTER TABLE llm_calls DROP CONSTRAINT IF EXISTS llm_calls_outcome_check;\n"`  
> `f"ALTER TABLE llm_calls ADD CONSTRAINT llm_calls_outcome_check CHECK (outcome IN ({values}));"`

**Minimal fix:** Add a distinctly named replacement constraint as `NOT VALID`, commit, validate it in a separate transaction, then briefly drop the old constraint and rename the replacement.

### F-3

**Failure scenario:** A dropped `1.` item is followed by a surviving `2.` item, prose, and the separate date `5. Ekim`. Because prose does not reset renumbering state, the date becomes `4. Ekim`, changing meaning after literal validation.

**Evidence:**

> `if m is None:`  
> `c.text = f"{m.group(1)}{max(n - lost, 1)}{m.group(3)}{c.text[m.end() :]}"`

**Minimal fix:** Renumber only contiguous list runs with the same indentation and delimiter; reset on every non-list unit and exclude date-like or code-like lines. Preserve gaps when step identifiers may be semantic.

### F-4

**Failure scenario:** An initially readable, newest item is co-owned by projects A and B but is not sent or cited during an ask for A. If B access is revoked before completion, the final authorization pass does not inspect that unused item, yet `memory_as_of` still reveals its timestamp.

**Evidence:**

> `vids = sorted(run.sent | {shown[h].version_id for h in handles if h in shown})`  
> `as_of = memory_as_of(run.view.values())`

**Minimal fix:** Compute `memory_as_of` from a freshly loaded, currently authorized view inside the final authorization transaction, including current project policies and device scopes.

### F-5

**Failure scenario:** A valuable configuration document contains the non-secret assignment `TOKENIZER=cl100k_base`. The unbounded `token` substring matches, causing the entire file to be skipped as secret-bearing.

**Evidence:**

> `r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*(?:secret|password|passwd|token|api[_-]?key)[A-Za-z0-9_]*)"`  
> `return None, f"secret-pattern:{hit}"`

**Minimal fix:** Match secret terms only as complete variable-name components or suffixes and add negative regressions for `TOKENIZER`, `TOKEN_BUDGET`, and `MAX_TOKENS`.

## Verdict

**NO-GO** — F-1 leaves a CRITICAL secret-exposure path in the new D-213 control. Fix F-1 before release; F-2 through F-5 should also be corrected and regression-tested before shipping R4.1.