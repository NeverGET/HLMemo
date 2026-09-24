# Consult 58 — CRITICAL dual review (D-085): English retrieval pivot, branch L

You are reviewing branch `worktree-agent-a143819e825766fb8` (your cwd is its worktree). The diff range is `8b692b2..d3ce0ac`, two commits:
- `d5c8209`: slice 1 — per-source top-5 cap + guarded English query rewrite, behind flags.
- `d3ce0ac`: slice 2 — English index renditions, migration `0009_language_pivot` with down_revision `0008_librarian_tasks`, behind a flag.

Design background: docs/decisions/DECISIONS.md rows D-081 and D-082, and docs/consults/53-sol-review-language-pivot.md.
The rules:
- The original text stays authoritative.
- The client LLM may supply `index_en`; the server validates it.
- The librarian backfills renditions.
- Non-English queries are rewritten asynchronously through a cache and never block.

Production is at migration 0008, and 0009 will ship in release R3. It is a static review; do not modify files.

## Implementer's claims (verify, don't trust)
- Three flags, all default off: HLM_RETRIEVAL_SOURCE_CAP, HLM_QUERY_REWRITE, HLM_RENDITIONS. With the flags off, behaviour is byte-identical to today.
- query_rewrite:
  - It runs in the API process with an 8 s deadline.
  - The privacy/policy gate runs before every attempt, and a query containing a secret is never sent.
  - It uses the spend guard.
  - A guard rejects any changed number, identifier, path, command or quoted string.
  - The cache is in memory, per (device, token generation, project), 30 min.
- index_en is validated in the write transaction: verbatim protected tokens in both directions, length ratio, English. A rejected rendition is recorded with a reason and backfilled by the `translate` task (priority 7). Device-scoped items are never sent to a translator.
- Renditions are rebuildable tables keyed to the original version_id. Replay applies the recorded bytes and never calls a translator (G6).
- G5: renditions never leak across project, device scope or class.
- Fusion: within each leg, original, rewrite and rendition matches are merged before RRF, with at most one vote per chunk per leg.
- An English preview is labelled `translation:true`; drilldown and raw return the original. DF counts original units only.
- Renditions are written even in the OBSERVER librarian role. D-074 says the observer never mutates user data; the implementer argues this is derived index data.
- Known limits the implementer admits:
  - The validator can't see negation, tense or modality flips (4 of 4 such false renditions were accepted).
  - Worst-case G-L3 at 100% rendition coverage gives p95 541/515 ms against a 500 ms limit. The owner's corpora are mostly Turkish, so real coverage can be high.
  - The cap only acts on items with `source`.
  - The rewrite cache is lost on restart.
  - There is no trigram leg over renditions.

## Focus (in priority order)
1. **Migration 0009 safety:**
   - Is it online and lock-safe on a live 0008 database?
   - Is the down path correct?
   - Does anything conflict with W0a one-way-door release tooling or the replay/export paths?
2. **Privacy and isolation:**
   - Anything that could send device-scoped, excluded-project (`librarian_cross_project=exclude`) or secret-bearing text to a provider, via either translate or query_rewrite.
   - Cache-key leakage across devices, tokens or projects.
   - Rendition rows or previews crossing project, device scope or class.
3. **Correctness:**
   - The fusion single-vote rule.
   - DF invalidation.
   - The rewrite guard.
   - Whether showing an English preview is safe, given that the validator accepts negation flips. Should previews always show the original, with the translation only as a hint?
   - Whether the flag-off byte-identical claim holds.
4. **D-074/D-086 compliance:**
   - Does writing renditions in observer mode violate the observer contract? Take a position.
   - Job/event policy for the translate task (one terminal event per job, ≤4 defer events; systemic hand-backs are non-authoritative in replay).
5. **Latency risk at high rendition coverage:** Name the concrete hot spots (file:line) and the cheapest mitigation.

## Output contract (≤ 45 lines, any language)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one-paragraph reasoning.
- `## Findings`: a table with the columns severity | file:line | trigger (concrete scenario) | fix. Include only real, reproducible-by-reasoning defects; no style notes.
- `## Positions`: one line each on (a) English previews, (b) observer renditions, (c) the latency mitigation.
- `## Decision-log text`: ≤ 5 lines suitable for an ADR row.
