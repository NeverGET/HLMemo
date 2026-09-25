# Consult 69 — routine check (D-085, astra-low): the review-66 fixes on librarian v3

Your cwd is a clean export of e9b5508. `FIX66.patch` = 0757c4b..e9b5508 (9 fix commits; cassettes, eval/live and the large golden are excluded). Review 66: docs/consults/66-astra-review-v3.md and 66-sol56-review-v3.md. The rules D-100/D-101 are in D100-D101.txt. Read-only.

## Claims to verify (each has a regression test)
1. **D-101:** either the quoted date or the evidenced valid_from can veto; neither overrides or flips the other.
2. **Close linearization:** an approval close is re-materialized at the definitive T after the final lock and the TTL check, for open, finite and mixed segments.
3. **Future cut:** the supersedes link starts at the actual close cut, so the old value stays current until the cut (query and drilldown).
4. **Reversal:** every mutation is tagged with its question_id. Reverting the dependent question undoes only its own links; reverting the owner of the close is refused while the dependent question is applied.
5. **Provenance:** CLOSE and REOPEN keep `source` and `code_refs`, both live and on replay.
6. **Export:** cross-project text is replaced by labels plus a hash; `--include-cross-project` exports it and records an `export_cross_project` event.
7. **`--out`:** created with O_CREAT|O_EXCL|O_NOFOLLOW and mode 0600; replacing a file needs `--overwrite`.
8. **Budget hint:** only incomplete pairs get the failure hint.
9. **Section pool:** the limit is applied after the global cosine ordering.

## Output (≤ 20 lines)
- `## Verdict (OK | FIX-NEEDED)`
- Per claim: FIXED, PARTIAL or OPEN, with file:line.
- New findings, real defects only: severity | file:line | trigger | fix.
