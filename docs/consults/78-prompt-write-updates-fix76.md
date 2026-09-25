# Consult 78 — the single verification (D-125 cap), astra-low: the review-76 fixes on D-118 write-time supersession

Your cwd is a clean export of 8919355. `FIX76.patch` = 8b54800..8919355. Review 76 is in docs/consults/76-*review*.md. Read-only.
**Scope rule (D-125):** check ONLY whether the 5 listed findings are closed, and whether the fix introduced a regression in the same code. A new finding counts as HIGH only if it comes with a concrete, reproducing scenario (inputs → wrong state).

## Claims
1. **Cut selection:** the cut is chosen against the quoted head only; historical segments are never cut by a supersede of a later head.
2. **Duplicate link:** a carrier that declares the same `supersedes` link is rejected per update as `duplicate_link`, so revert owns every link.
3. **Target version:** the target is the version valid now, not the highest version id (a later middle correction).
4. **Overlapping edge:** an existing edge that only overlaps a backdated update is a conflict, never a gap.
5. **Revert time:** it includes the link's recorded_at, so it survives a clock regression.
Regression tests: tests/integration/test_write_updates_r76.py (7 tests, failing before and passing after).

## Output (≤ 15 lines)
- `## Verdict (OK | FIX-NEEDED)`
- Per claim: FIXED, PARTIAL or OPEN, with file:line.
- New findings, only with a reproducing scenario: severity | file:line | scenario | fix.
