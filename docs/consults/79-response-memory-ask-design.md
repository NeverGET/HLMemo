# Consult 79 — implementer's dispositions (wf-memory-ask)

Astra-low answered FIX-NEEDED (in Turkish, `79-astra-memory-ask-design.md`). Each point and what was built:

1. **HIGH — removing a source does not clean derived text.** Accepted. (a) Before EVERY provider call the
   strict gate runs over this call's ids ∪ every id already sent; an already-sent item now denied for a
   privacy reason (not merely superseded) aborts the question with `E_UNAVAILABLE reason=authority_changed`
   before anything else is sent. (b) The final re-check covers every sent id: any authority loss withholds
   the answer, the claims and the queries (abstention, `abstain_reason=authority_changed`). (c) An excerpt
   excluded mid-request (superseded) is pruned from later prompts together with the draft claims/quotes
   that cite it (own review, same class). Tests: `test_ask_grant_lost_after_send_withholds_everything`,
   `test_ask_revoked_device_mid_request_is_e_auth`, `test_ask_superseded_mid_request_never_rides_into_later_prompts`.
2. **MED — `detach(hold_s)` is not a total deadline.** Accepted: the handler has its own absolute deadline
   (`HLM_RESEARCH_TIMEOUT_S`), per-call caps, a re-check reserve, optional steps only with time left, the
   concurrent search is cancelled and awaited on failure. `detach(hold_s)` only reschedules after a
   successful commit + release, never shortens, is capped (`DETACHED_HOLD_MAX_S`), and a second detach is a
   no-op (existing guard). Tests: `test_ask_over_mcp_outlives_the_request_db_deadline` + its negative
   control `test_ask_without_the_hold_hits_the_request_deadline`.
3. **MED — literal/quote checks are no faithfulness guarantee.** Accepted, and the coordinator's gate-v1
   addendum went further: claims carry 1–3 verbatim quotes that must TOGETHER contain every literal of the
   claim; the answer keeps only sentences grounded in the kept claims' quotes (the question is no longer
   evidence); the completeness output goes through the same validation; a self-check call (quotes only)
   narrows or drops claims not fully entailed. Numeral normalisation is limited to a DECIMAL comma
   (`1,6` = `1.6`; a thousands-style `1,600` is left alone).
4. **MED — "≤ 5 calls" vs fallback attempts.** Accepted: `meta.calls` = logical calls (≤ 5) and
   `meta.attempts` = provider requests; a per-question lineage caps attempts at `MAX_ATTEMPTS` (9) in the
   database (`llm_lineage_calls`). Test: `test_ask_attempts_are_capped_per_question`.
5. **LOW — summaries/replay.** Accepted: summary members are re-gated in every map-bearing call
   (`MemoryMap.gate_ids`), a summary without members is never shown, the table is outside
   `replay.PROJECTION_TABLES` (no FK to projection tables), and deleting it is harmless (tests delete it).
