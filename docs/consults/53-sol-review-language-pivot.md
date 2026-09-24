## Verdict on D-081 vs the owner’s pure-English idea

Choose **D-081 as amended by the accepted D-082**: preserve the original and accept a validated, client-supplied English `index_en`, with the librarian filling gaps. English-only storage would make translation errors permanent and remove wording needed to verify evidence. The production result points to two problems: Turkish hit@5 is .278, and 74 of 200 top-five slots came from sections of one report that was never gold. The reported 7→14/24 gain from translated questions is a useful diagnostic, not a hold-out result.

## Concrete design

Migration 0009 should add separate, rebuildable rendition and rendition-chunk tables keyed to the original `version_id`, plus rendition embeddings. Store the source digest, language, provenance (`client` or `librarian`), validator and model versions, status, English title, and text. Original chunks must stay intact: their ordinals and offsets refer to the original body. Record accepted client output in the write event’s `resolved` payload and librarian output in a resolved event. Replay applies those recorded bytes; it never calls a translator. A revision gets a new rendition, while old renditions remain available for historical reads.

Index renditions separately for lexical, trigram, title, and vector search. Every candidate must join the original version for authorization and temporal checks. Collapse original and English matches to one canonical candidate **within each retrieval leg before RRF**, so a translation cannot vote twice. Keep the original shared-corpus DF denominator unchanged; count English source units once and invalidate its cache when a rendition arrives. Group imported sections by `system:path` before the `#` suffix, falling back to `logical_id`. Apply the ≤2-per-source top-five cap after deduplication and supersession, then backfill.

Label English previews as translations. A translated span needs a validated mapping to an original chunk ordinal; otherwise return an item-level clue and original preview. Drilldown and raw return the original. Meter the complete serialized response under G2.

Detect language on the raw query before opening the DB transaction. Leave short, mixed, and identifier-heavy queries unchanged. Agents should query in English; for a non-English query, search the original immediately and add a guarded English branch when a short-lived, per-device/project cache has a rewrite. Queue cache misses asynchronously with a bounded provider deadline; do not wait for a remote LLM on the query path. Reject rewrites that alter identifiers, numbers, paths, or commands. Apply project policy, redaction, and query-specific privacy checks before provider calls. Never send `device:*` text externally; a client-supplied rendition can be indexed under that source’s ACL.

## Risks and gates

Verbatim guards cannot detect reversed negation, temporal meaning, or modality. Sample translations against their originals and measure validator false acceptance. Check for text lost beyond E5’s 512-token window. Require zero protected-token drift; G5 isolation; G6 offline replay equivalence; G2 budget; G3 Recall@5 ≥.90; and G4 plus write-load G-L3 p95 ≤500 ms with the added index writes. Throttle and deduplicate backfill through the existing spend ledger: 368 production librarian jobs took 76.6 minutes to drain. Report rendition coverage, queue age, spend, and cold versus warm query latency.

## Minimal first slice and thresholds

Ship source grouping/capping and guarded query rewriting behind **independent flags**, before adding rendition storage. Measure baseline, cap only, rewrite only, and combined on frozen A/B at budget 3000. Prewarm rewrites for quality scoring and report cold misses separately. On B’s 36 answerable Turkish questions, require at least four net new top-five hits (10→≥14). On its 36 English questions, require zero additional misses: one miss already exceeds D-081’s one-point tolerance. Require overall W-E ≥+3 points, no category below −3, and no decline in evidence R@5, L2, temporal, or negative performance. Inspect capped-away gold sections: source hit@5 can credit the wrong section. Then add D-082 renditions and rerun the gates.

## Missing comparison

The current embedder is already multilingual E5-small. D-042 found a cloud embedding alternative slower and worse on hybrid recall, so E5 is not a proven bottleneck. Compare candidate recall before fusion, rewriting, diversity, another multilingual embedder, and a bounded top-20 multilingual reranker on the same hold-out and latency hardware.

[Review saved in the consultation log](/Users/cemalkurt/Projects/HLMemo/docs/consults/53-sol-review-language-pivot.md).