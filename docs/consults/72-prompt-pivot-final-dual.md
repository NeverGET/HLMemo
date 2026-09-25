# Consult 72 — CRITICAL dual review (D-085), RELEASE-GATING for R3 (D-099 criterion 3): the final query rewrite (mask-and-translate), pivot-s1-r3 @ eb0bed1

Your cwd is a clean export of eb0bed1 (no .git, no secrets).
- `D106.patch`: the commits since review 70 (COMMITS.txt, first-parent, no merges).
- `FULL-VS-MAIN.patch`: everything R3 adds under src/tests/deploy/profiles.
- `RULES.txt`: the rows D-097/D-102/D-106/D-107/D-108.
- Review 70 is in docs/consults/70-*review*.md.
Read-only. R3 ships this with HLM_QUERY_REWRITE ON.

## Claims (verify; attack the invariants)
1. **Mask-and-translate:**
   - A token is translatable only if it is a TR/DE dictionary word (ASCII-folded, TR suffix stripping) AND NOT common English, a CLI head, code-ish, a number, punctuation/operator, or quote/backtick content.
   - Every other token becomes ⟦Pn⟧: one placeholder per contiguous protected run, per quote span and per whole command span.
   - ONLY the masked text is sent. The output is accepted only if every placeholder appears exactly once and in order and the rest is plain English; the protected text is then restored byte-exact. The old free-output guard is removed.
   - Property tests over 2,500 generated queries, plus 29 review counterexamples.
2. **Send gate in depth:**
   - a credential lexicon incl. DE stems, normalised for leetspeak and spaced letters;
   - Unicode and quoted assignment keys;
   - PIN, Luhn cards, IBAN, URLs, high-entropy tokens;
   - result: zero calls.
3. **Burst:**
   - admission control returns `unavailable` early when the queue wait would exceed the deadline;
   - concurrency defaults to 4;
   - only a caller's OWN local queue wait (`queue_wait_s`) is exempt from breaker accounting; real provider timeouts still count.
4. **`HLM_QUERY_REWRITE_BRANCH_WEIGHT`:** default 1.0, identical to earlier behaviour.
5. **Release wiring:** `HLM_QUERY_REWRITE=true` in deploy/llm.env.example and install_llm_env, and asserted after every cutover (D-108 order).
6. **Earlier fixes still intact:** quotas, the 20% budget share, lineage, the enqueue deadline, the demotion union, latency-mode schema fallthrough, the per-task fallback hook, and flag-off byte identity.
7. **Gates:**
   - unit: 778;
   - gate-release off/on: G3 0.980, G4 p95 249/254 ms, G-L3 p95 ≤ 380 ms;
   - live acceptance: TR 90%, TR without diacritics 90%, DE 100%, 0 contract failures.

## Focus
- **Invariant A (payload):** find any input where the provider payload contains a non-dictionary token, a protected token, a secret, or a substring of one. Consider:
  - dictionary words that are ALSO secrets or identifiers;
  - tokens split across Unicode or zero-width characters;
  - homoglyphs;
  - RTL marks;
  - mixed scripts;
  - placeholder-like text in the user query (e.g. the user typing "⟦P1⟧");
  - very long inputs.
- **Invariant B (restore):**
  - Can the model's output make the restored query contain a protected token in a different role, duplicate one, or inject text through a placeholder?
  - Can a user-supplied fake placeholder confuse restoration?
- **Breaker accounting:** can the `queue_wait_s` exemption be abused to hide real provider failures?
- **Wiring:** is the flag truly on after the D-108 cutover, and does a rollback leave the R2 llm.env consistent?
- Treat the dictionary-word-that-is-an-identifier translation (e.g. `worker`) as the documented residual, not a finding, unless it can cause data exposure.

## Output contract (≤ 40 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Per-claim`: claims 1–6, each OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table with severity | file:line | trigger | fix, marking each as in-scope or policy extension. Real defects only.
- `## Ship-with-flag-ON (R3)`: yes or no, plus the reason.
