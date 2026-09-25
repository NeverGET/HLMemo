# Consult 73 — CRITICAL dual review (D-085), RELEASE-GATING for R3 (D-099 criterion 3): the D-111 hardening of mask-and-translate + env-aware rollback, pivot-s1-r3 @ 63bc041

Your cwd is a clean export of 63bc041 (no .git, no secrets).
- `D111.patch`: the fix commits since review 72 (COMMITS.txt).
- `RULES.txt`: the rows D-106/D-108/D-111.
- Review 72 is in docs/consults/72-*review*.md.
Read-only.

## Claims (verify each; try to break the two invariants)
1. **Canonical input:**
   - NFC.
   - Any Cf, zero-width, bidi or soft-hyphen character → send-gate reason "invisible", zero calls.
   - Mixed-script, fullwidth and math-letter tokens are masked.
   - The credential check runs on a fixed-point normalisation (collapse, leet, confusables).
2. **Apostrophes and quotes:**
   - `TR_APOSTROPHE_SUFFIXES` is the only allowed apostrophe tail; anything else masks the whole token, and an output-side `error'qzxv` is rejected.
   - The quote scanner is stateful: multi-line spans, unclosed-to-end and glued tokens are masked whole.
3. **Restore:**
   - Glue to a word is rejected, and adjacency must match the input.
   - A protected token appearing outside its placeholder fails as `protected_repeat`.
   - DEVIATION: start, end and whitespace are treated as one boundary class, because translation moves placeholders.
   - English function words, and cognates of visible words, are not counted as repeats.
4. **Breaker:** a timeout is exempt only if the queue wait caused the short attempt; `queue_wait_s=1e-9` still counts.
5. **Cutover check:**
   - On R3 code, a missing llm.env FAILS.
   - R3 mode (`--release r3`, or an env marked `HLM_ENV_RELEASE=r3`) requires rewrite ON and cap OFF.
   - DEVIATION: an unmarked R2 env passes, labelled as the D-108 interim.
6. **Rollback:**
   - llm.env is snapshotted with the release.
   - rollback renders and restores the previous env before starting the previous image.
   - Failure recovery restores the newer env.
   - Tests cover the failed-step, interrupted re-run, superseded-snapshot and older-runner cases.
7. **Evidence claimed:**
   - property classes (a2) 2,000 hard queries / 8,080 must-hide words; (b2) glue, adjacency, repeat and tail mutations; (c) 2,000 send-gate obfuscation cases;
   - unit 801/800/1; deploy 147; integration 572/14;
   - G3 0.980;
   - live acceptance TR 93%, TR without diacritics 90%, DE 93%.

## Focus
- **Invariant A (payload):** the provider payload holds only source-language dictionary words, placeholders and fixed prompt text, with NO secret or protected substring. Construct a counterexample if one exists. Try combinations the tests may not cover: canonicalization interacting with apostrophes and quotes, NFC-composed versus decomposed Turkish letters, dotted/dotless i case folding (İ/ı), and placeholder-looking user text.
- **Invariant B (restore):** can a model output get accepted that changes, duplicates, re-roles or glues protected text? Is the one-class boundary deviation exploitable (for example, moving a placeholder to change which word it attaches to, in a way that changes meaning for retrieval)? Judge the severity honestly: the original query is always searched too.
- **Rollback and cutover:** walk through the D-108 order end to end: R2 env → R3 image → R3 env → rollback. Is there any state where the running image and the env mismatch? Is the interim PASS safe?
- Do NOT re-report the documented residual (a dictionary word that is also an identifier).

## Output contract (≤ 35 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Per-claim`: claims 1–6, each OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table of severity | file:line | trigger | fix, with in-scope vs policy-extension marked. Real, reproducible defects only.
- `## Ship-with-flag-ON (R3)`: yes or no, plus the reason.
