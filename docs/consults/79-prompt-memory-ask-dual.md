# Consult 79 — dual review (D-085/D-125: read-path scope/privacy + migration), memory.ask @ 9bceb59

Your cwd is a clean export of 9bceb59. `ASK.patch` = main...9bceb59 (src, alembic, tests). Spec: the rows D-130, D-136 and D-140 to D-146 in docs/decisions/DECISIONS.md. Read-only. **At most 2 rounds (D-125). A HIGH finding must come with a concrete reproducing scenario (inputs → leaked or wrong state).**

## Threat model (write findings only against these)
- T1 **Scope leak:** content from another project, a device-scoped item, an excluded project (D-083) or a hidden class reaches the Memory Map, any provider prompt, the answer, primary/related handles or the map summaries.
- T2 **Privacy:** secret or redaction-worthy text is sent to the provider (the precheck must run on every send, including carried text and summaries).
- T3 **Mutation:** memory.ask or map_summary writes any event or mutation of user data, or holds a DB transaction across a provider call.
- T4 **Spend/DoS:** per-call max_tokens, the per-question $/token budget and the 25 s deadline can be bypassed; map_summary can be made to loop or re-summarise endlessly (debounce/back-off).
- T5 **Migration 0009:** it is additive only, rebuildable, and not in replay; the up/down paths are safe on a live 0008 database; it is compatible with the R3 release tooling (llm.env release state, manifest).
- T6 **Honesty of output:** a claim can cite a handle the caller cannot see; a quote can pass the check without being in the cited text.

## Output (≤ 30 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`
- `## Findings`: a table of threat | severity | file:line | reproducing scenario | fix. Only T1–T6.
- `## Safe-to-ship`: yes or no, plus the reason.
