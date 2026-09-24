# Consult 68 — STRATEGY (dual, D-085): the librarian's pair-level approach cannot fix stale-first. What next?

Your cwd is the HLMemo repo at main. Read-only; do not modify files.
Read first:
- docs/decisions/DECISIONS.md rows D-051, D-058, D-067, D-072, D-076, D-087, D-089, D-090, D-100, D-101 and **D-103**.
- docs/research/00-deep-research-report.md (Turkish; the design source of truth). Focus on §D (layers L0–L4: **"L1 - Atomik gerçekler/epizodlar"**, i.e. atomic facts/episodes with bi-temporal validity, Graphiti-style) and §D.5 (consolidation, "sleep").
- docs/decisions/PHASE2-4-ROADMAP.md §3: W3a topics, W3b consolidation, W3c decay.

## Facts (measured; do not re-derive)
- **G-E-TEMP stale-first on corpus A:** an outdated memory ranks above the current one in the L2 top-3. The gate is ≤4/15.
  - Baseline 8/15. Librarian v1 8/15; v2 8/15; v3 in every ablation (TEMPORAL, +CLOSE, +section quota/doc↔episode, +24/8 caps) 8/15.
  - Across the v3 ablation: 0 closes, 0 pairs fixed, 0 broken. In G-E-W2b, v2/v3 are at best exactly neutral.
- **Why (deterministic diagnosis, D-089, plus the v3 export):**
  - The stale items are MULTI-STATEMENT memories: imported markdown sections, auto-memory files, multi-fact notes. Only one statement is outdated, so the model marks supersedes `scope=part`, and the whole-scope close never fires (1 whole-scope judgement in C2–C4, and it was dropped).
  - Under the prod temporal rule (D-072), few items have an evidenced valid_from, so direction is often undecidable.
  - On A, the current item sits OUTSIDE the top-3 in all 8 stale-first cases (rank 4–28). Reordering a pair fixes nothing; only removing or closing the stale item, or getting the current item into the top-K, can.
  - v3 proposal precision is strict .175: mostly real updates proposed as bare "contradicts". v2 was .48.
- **What works:** the query-side English rewrite (D-090) gave +8.3 pts overall and temporal L2 on A .40 → .60, but stale-first stays 8/15. G3 retrieval is 0.980.
- **Product state:** production runs the librarian in OBSERVER only. It is safe (0 mutations), but it does not yet deliver the "memory keeps itself current" promise. Phase 5 (migrating the owner's projects under supervision) and Phase 3 (consolidation) are ahead.

## Options (evaluate, add others if better)
- **A. Atomic statements (L1 per the research report).** Split multi-statement memories into statement-level items at write/import time, keeping the original as provenance and a parent. Supersession then becomes whole-scope, so the existing owner-approved close fires. Cost: a data-model/import change, the migration of existing items, retrieval granularity and budget effects.
- **B. Statement-level consolidation (reconsolidation, W3b-style).** The librarian proposes a revised version of the multi-statement memory in which the outdated statement is updated or removed, owner-approved and bi-temporal (the old version stays in history). Retrieval then sees the revised item.
- **C. Read-side supersession clue.** When a hit has an APPLIED partial-supersede link and the query matches the superseded statement, show an inline, non-reordering clue ("superseded by <id>: <short current value>"). Measure answer-correctness in the top-3 including clues. This would need a gate definition change, which only the owner can approve; argue honestly whether the current gate is the right measure for multi-statement items.
- **D. Retrieval.** A recency/topic-cluster prior so the current item enters the top-K without librarian links (e.g. near-duplicate topical clusters ordered by evidenced time). Neutrality risk.
- **E. Stop investing in stale-first for now.** Keep the observer as a question generator (contradiction questions to the owner), ship Phase 3/5 with owner-approved actions, and revisit later.

## Questions
1. **Ranking:** rank the options by expected stale-first gain / risk / effort, and by fit with the research report. Name the combination you would do and in what order.
2. **Option A:** what granularity is right (sentence, claim, bullet)? Who splits (a deterministic splitter, the client LLM at write time as in D-082, or the librarian)? How are existing items migrated reversibly? What happens to retrieval budget and G3/G4/G-L3?
3. **Option C:** is "stale-first in the top-3" the right measure when a correct clue is attached? Propose a gate that is honest and not easier by construction.
4. **Measurement:** what is the smallest experiment that would falsify your recommended option in ≤1 day on the existing hold-out (corpus A/B, prod-rule import)?
5. **Phase 5:** how does the recommendation change the Phase 5 migration plan (observer → assistant → autonomous)?

## Output (≤ 45 lines)
- `## Recommendation`: the ordered steps, each with the expected A stale-first delta and effort in days.
- `## Why not the others`: one line each.
- `## Gate`: keep it as is, or the exact new definition, with the reason.
- `## Falsification experiment`: ≤ 8 lines.
- `## Phase 5 impact`: ≤ 5 lines.
