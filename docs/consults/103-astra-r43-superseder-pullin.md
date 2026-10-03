**GO with changes** — accept the design; add the regression coverage below and complete the planned measurements before shipping. No confirmed HIGH or T1/T2/T3 defect found.

1. **LOW — Missing regression tests for D1’s motivating interactions.**  
   At `src/hlmemo/core/read_service.py:300–306`, placement follows tie ordering and partial-supersession demotion. That is the right location, but tests should combine placement with:
   - exact-score, same-title ties;
   - D-076 demotion.

   Assert that the original hit remains flagged, its superseder follows it, and all unplaced hits retain their baseline relative order.

2. **LOW — The beyond-head move needs explicit accounting coverage.**  
   `src/hlmemo/core/read_service.py:388–435` distinguishes an existing result beyond `n_fetch` from a newly inserted result. The current integration move test covers an in-head move. Add a beyond-head case asserting no duplicate, correct row/B3 hydration, unchanged total result count, and exact `omitted` across budget boundaries.

3. **LOW — Document adjacency as a contiguous superseder block.**  
   `src/hlmemo/core/supersession.py:327–341` deliberately permits `[old, newest, second-newest]`. Literally, both superseders cannot be “directly after” the old hit. Specify that the selected superseders form a contiguous, newest-first block immediately after their anchor. This matches the implementation and removes ambiguity from T3.

4. **LOW — Add a second-hop visibility regression.**  
   `src/hlmemo/core/read_service.py:424–435` correctly uses B3 for a fetched superseder’s own flags. Test `old → pulled → private`, where the caller can see the first two but cannot see the final superseder or its link. Assert that the complete serialized response contains no private clue or text. The existing depth test covers a visible second hop.

**Answers**

1. **T1/T2/T3:** No confirmed defect. Fresh resolution uses `QueryFilters.hit_where()` (`read_queries.py:912`); fetched hits receive B3-authorized flags. Insertions increment `total`; moves do not. Packing remains prefix-based with exact measurement. The planner’s rank and `placed` guards prevent moving an anchor after it has placed another hit. No tools/list definitions changed.

2. **D1: accept.** Placement must follow existing ordering to preserve adjacency and unaffected relative order.  
   **D2: accept.** Retrieved-but-unpacked superseders justify moving lower-ranked results. Update the plan to state this explicitly.  
   **D3: accept.** Preserve moved scores; use `0.0` for inserted hits. Document that response order now includes supersession placement and is not globally score-sorted.

3. **Keep rank-major, newest-first for now.** Round-robin improves coverage across anchors but can discard a relevant correction to the strongest hit. It also does not solve chains: moving an intermediate superseder prevents that hit from pulling its successor (`supersession.py:314–315`). Add the actual two-chain caps query as a regression and judge the **packed previews**. The supplied presence counts establish opportunity, not the ≥80% current-value bar or p95 ≤ baseline +10 ms.

4. **Important missing cases:** findings 1–4, plus the real caps query with competing chains and cap exhaustion.

**Verification:** all 11 targeted unit tests passed. Integration tests were inspected, not executed. Quality grading, latency, and G-SURF measurements were not rerun. No files changed.