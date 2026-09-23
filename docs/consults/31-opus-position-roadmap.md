# 31 — Opus position on PHASE2-4-ROADMAP.md (written before reading Sol's review)

Date: 2026-09-23. Scope: docs/decisions/PHASE2-4-ROADMAP.md (draft by a Claude planning agent).

## Where I agree
- Contract freeze before fan-out (migration numbers, event kinds, librarian system device, tool budget, recorded LLM responses) is the right way to keep parallel agents from colliding.
- The librarian runs in shadow mode first, and invalidation is used instead of deletion. The core keeps working when the LLM is down (latency gate with the provider down).
- W0 (manual server-side device minting) comes first, and W3c decay stays LLM-free.

## Where I want changes
1. **A real-data evaluation must be a first-class workstream (W-E), before W2b.** Every retrieval-quality gate so far (G3 Recall@5 ≥ 0.90) is tuned on our own synthetic fixture. A real-data run on a private owner project (corpus A, 100 questions, blind ingester vs. question author) is in progress now. Its baseline must become the acceptance gate for W2b, W3a and W3b: "the librarian/summaries/consolidation must improve the real-data score by ≥ X and never regress a category by more than Y". A synthetic G3 threshold alone cannot justify the librarian.
2. **Agent-level A/B.** At least one gate should measure task outcome with vs. without memory (small fixed task set, recorded runs), not only retrieval. Otherwise "human-like memory" stays an unmeasured claim.
3. **Dogfooding earlier.** HLMemo's own memory should move to production as soon as W0 is done, not only at the end of Phase 4. The events are authoritative, so the librarian can reprocess history later. The final D-051 prod test still runs at the end.
4. **Scope risk.** 61.5 ideal-days + 30–50% is large for 4 phases on a 2 vCPU box. W2e, W4c and parts of W3d are the first cut candidates if the real-data eval shows they don't pay off. Each phase should justify itself on the eval, not only on the report's promise.
5. **RAM budget.** The api (1.2 GiB) and worker (0.8 GiB) run next to a new librarian service on 7 GiB. I'd like the roadmap to state a hard per-service mem_limit and prove it under load in the deploy gate.

## Questions for Sol
- Is the recorded-response ("cassette") approach enough for LLM-dependent gates, or do we also need a small live-model gate per release?
- Is the proposed `valid_from = mtime` + `source` field the right fix for the D-020 recorded_at conflict?
- Which parts of §4 (sync, packed share) would you cut or defer as too vague?
