You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Your consult 42 (docs/consults/42-sol-review-w15.md) said DO-NOT-MERGE for W1.5. The fixes 0afc34a, f69630d and e0a785e are now merged into main (merge 0c0c013). Review ONLY the fix delta: `git diff af7daa6 e0a785e` (run it in the main repo). Also read DECISIONS D-072/D-073.
STATIC REVIEW ONLY. For each of your 6 findings: FIXED / PARTIAL / NOT FIXED (one line with file:line). Try to break the new pieces:
- the online unique index: is the "open validity" predicate the same notion of "current" the write path uses? What about a duplicate that exists before the index build (the CONCURRENTLY build fails → INVALID index → the retry path)?
- Item.close: authz, bi-temporal correctness, replay, the mass-close guard;
- the export origin mapping: spoofing via `origin:` naming another project;
- section re-map by similarity: can it wrongly merge two different sections?
- librarian_priority: can a client set it to jump the queue (it should be server-derived)?
OUTPUT (≤ 350 words): ## Verdict (OK / FIX-NEEDED); ## Per-finding status; ## New findings (table, max 5: severity | file:line | issue | fix).
