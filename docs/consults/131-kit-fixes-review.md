# 131: kit fixes after the first second-device migrations: review record

Tools-only change (the importer and the CLI run on the client; the server is unchanged), so one fresh-context
review agent (Sonnet), read-only, no database tests (D-266).

## The change
1. A `changed` re-import of a plain source file keeps the stored item's narrowed `device_scope`
   (`plan.Entry.keep_scope`, `runner._item`). Before, the revision carried no scope and the write's default
   `all` widened an item an operator had re-scoped.
2. `tags: a, b` in frontmatter is read as a list by the importer, as the migration lint already did.
3. `tools/migrate/local_stack.sh` resets its scratch database with the repository's interpreter and psycopg; no
   host `psql`.
4. `hlm project list` shows the calling device's grants when the server has no listing route (404).

## Rubric
HIGH: data written with a wider visibility or to the wrong item, a duplicate or an unexpected close, a secret
exposed, injection. MEDIUM: a wrong classification, an unstated behaviour change, a regression in the scratch
stack. LOW: clarity, a missing test, cheap hardening.

## Findings
| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | MEDIUM | The re-map path (a moved or split file becomes a revision of the old item) did not carry `keep_scope`: the same silent widening | **Fixed** in `plan.remap`, with a test |
| 2 | LOW | The importer's and the lint's reading of a tags string differed for an unclosed bracket and quoted parts | **Fixed**: the same stripping, and the test compares both readers |
| 3 | LOW | A raw traceback when the scratch database cannot be reset | **Fixed**: one line, exit 1 |
| 4 | LOW | Missing tests: a failing `whoami` in the fallback, empty grants, an export record, a card, a kind change | **Added** |

Found sound: the scope is sent only on a `changed` revision of a non-export, non-card record; an export record
states its own scope; an older server that omits the field gives the old behaviour; a revoked `device:<id>` scope
fails loudly on the server, which is better than a silent widening; no injection in the inline Python (the slug
is validated and passes through `sql.Identifier`); only a 404 takes the CLI fallback.

Known and not changed: a device outside the class cannot see a narrowed item, so its import classifies the source
as `new` (BACKLOG).

Tests: unit 1939 passed; `tests/integration/test_w15_import.py` 14 passed; the scratch stack started with no
`psql` on PATH.
