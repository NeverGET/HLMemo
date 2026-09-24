# Consult 67 — CRITICAL dual review (D-085): pivot-s1-r3 D-097 fixes, before R3 (prod GO depends on it, D-099)

Your cwd is a clean export of 558c87a (no .git, no secrets).
- `D097.patch`: only the fix commits since review 64 (COMMITS.txt, first-parent). One of them is a merge of main that brought in F (per-task fallback hook, D-098).
- `FULL-VS-MAIN.patch`: the whole branch against current main, which is what R3 will contain.
Review 64 is in docs/consults/64-*review*.md. Read the D-097 row in docs/decisions/DECISIONS.md, plus D-088, D-090, D-094 and D-098. Read-only.

## Claimed fixes (verify each; try to break it)
1. **Rewrite guard: ambiguous = protected.**
   - Protected: every slash token, every quote style (1-char included, single/curly), backtick spans, code-ish tokens (/ . _ - : = or digits).
   - Command context: a CLI head from `CLI_HEADS` plus its subcommand/args.
   - The six review cases are rejected: `src/hlmemo`→`src/server`, `src/main`→`src/other`, `pytest`→`tox`, `git status`→`git log`, `'prod'`→`'dev'`, `"x"`→`"y"`.
   - Diacritic-less Turkish ("neden", "nasil") still rewrites.
   - Apostrophe suffixes (e.g. `Redis'in`) are not treated as quotes.
2. **Credentials:** a TR/EN credential keyword followed by a value causes zero provider calls.
3. **Fallback:** the latency attempt policy is used, and the rewrite chain goes through F's per-task hook (`HLM_FALLBACK_PROFILE__QUERY_REWRITE`). With a stalled primary, the task fallback answers inside the 3 s deadline.
4. **Deadline and schedule:** the deadline is absolute from enqueue. A failed schedule returns `unavailable`.
5. **Spend:**
   - Each query has a stable lineage per day.
   - Hourly quotas: 60 per device and 300 per project, claimed atomically together.
   - Rewrites may reserve at most 20% of each budget window (`HLM_QUERY_REWRITE_BUDGET_SHARE=0.2`), so the librarian keeps 80%.
6. **Demotion:** it receives the union of original and English terms (Sol's TTL 60→300 case gives [new, old]).
7. **Test change:** `test_no_model_id_outside_profiles` now scans only the fallback lines of config.py for task names. The model-id half is unchanged.

## Focus
- **Credential detection:**
  - Can a password/token still leak through other phrasings? Try "şifrem hunter2", "token=abc123", "API anahtarı sk-...", "parolası: x", a multi-line query, a URL with a user:pass@ part, an env line `DB_PASSWORD=...`, and mixed case.
  - Does detection run before ANY network attempt, including fallback attempts?
- **Guard:**
  - Construct an accepted rewrite that changes a real identifier, path, command, flag, number or quoted string.
  - Check the CLI_HEADS approach for bypasses (e.g. `./manage.py migrate`, `python -m x`, `npx`, `bun`).
- **Quota/budget:**
  - Races in the atomic claim.
  - Quota bypass by rotating token generations or devices.
  - Correctness of the 20% share under concurrent librarian load.
  - Behaviour at a window boundary.
- **Integration with F:** is the per-task chain used exactly once, with the per-model breakers intact?
- Flag-off byte identity versus main still holds.

## Output contract (≤ 35 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Per-claim`: claims 1–7, each FIXED/OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table of severity | file:line | trigger | fix. Real defects only.
- `## Ship-with-flag-ON`: yes or no, plus the reason.
