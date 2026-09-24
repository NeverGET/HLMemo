# Consult 68 — orchestrator position (written BEFORE reading the codex outputs)

**Lean: A first (atomic statements, per research-report L1), then B (statement-level reconsolidation) as the Phase 3 mechanism. C stays only as an honest interim display, not as a gate change. D is out, E is the fallback.**

- **Why A:** every measured failure traces to granularity.
  - The model correctly says "part", so the whole-scope close can never fire on a multi-statement item.
  - The research report already specifies L1 = atomic facts with bi-temporal validity. Our multi-statement items are an unlogged deviation, so A realigns with the source of truth.
- **Who splits:**
  - Primary: a deterministic splitter (headings, bullets, sentences) at import.
  - The client LLM supplies statement boundaries at write time, following the D-082 pattern (the client has the context), and the server validates them.
  - The librarian only backfills.
  - The parent item stays as provenance and for display; retrieval ranks statements and can roll up to the parent.
- **Risks:**
  - The retrieval budget, and G3/G-L3, will shift. More, smaller items change BM25/DF and vector recall.
  - The existing multi-statement items need a reversible migration: parent kept, children derived, replayable.
- **Falsification (≤ 1 day):**
  1. On the A hold-out, split only the 8 stale-first items' neighbourhood (or the whole A corpus) deterministically into statements.
  2. Re-run the librarian C2 (TEMPORAL + CLOSE) with approve-all.
  3. If stale-first does not drop below 8/15 even when the relevant statement-level closes are applied, A is falsified.
  4. As a cheaper ceiling check first: simulate a perfect close at statement level on the split corpus. The earlier ceiling was 8 → 4.
- **Gate:** keep G-E-TEMP as it is. A clue-aware "answer-correct in the top-3" can be ADDED as a second metric, but it must not replace stale-first without the owner's decision.
- **Phase 5:** migrate owner projects as split statements from day one, with the parent kept. The librarian's assistant role then proposes whole-scope closes that the owner approves.
