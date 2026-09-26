# Consult 81 — consumer gate v2 (D-158): is it gameable, and does it reflect the consumer's needs?

Context: HLMemo is a self-hosted memory MCP server. Its research librarian tool `memory.ask` answers a coding agent's question from the project's memory with {answer, primary sources, related sources}. The owner delegated the release-gate definition to the consumer (a Claude coding agent, who is ALSO the implementer), with NotebookLM chat over the same sources as the reference system.

Read `docs/decisions/DECISIONS.md` entries D-130, D-136, D-137, D-141, D-150, D-155, D-156, D-157, D-158 (the tail of the file). D-158 is the pre-registered gate under review.

Answer in ≤ 40 lines:
1. **Gameability.** Where can the implementer (who defined the gate and will run it) pass it without the product being good for a consuming agent? Examples to check: the rater's leniency, the "−0.05 vs reference" margins, the source-scope faithfulness judged against up to 3 × 3200 chars, NotebookLM as a weak reference, the dev/hold-out separation, and the choice of subsets. Give a concrete fix for each real hole.
2. **Missing consumer needs.** Is anything a coding agent needs from a memory answer missing, e.g. stale versus current values (temporal correctness), or answer latency variance?
3. **The C2 floor change (.80 → .70 plus reference parity).** Is the rationale (the oracle ceiling of .84 with gold sources) sound, or is it moving the goalposts? Recommend a number.
4. **Verdict:** OK, or FIX-NEEDED with the ≤ 5 most important changes.
