# Review 80 (dual: astra-low + sol-5.6 xhigh on the clean export 85abf82) — dispositions

Both reviewers: FIX-NEEDED, the same two HIGHs; sol adds one MED and one LOW. Round 1 of at most 2.
Every HIGH has a regression test that FAILS on 85abf82 and passes on the fix (verified by swapping the
three source files back to 85abf82 and re-running the tests).

| # | Sev | Finding (both unless noted) | Disposition | Test |
|---|---|---|---|---|
| 1 | HIGH | `NOT_CURRENT` masks a simultaneous grant/policy/scope loss of a source already SENT; the draft then carries its text into the next prompt | FIXED 9fefb6b: before every call AND every attempt, the sources of already-sent text are re-judged on the privacy rules alone (`privacy.gate(ignore_currency=True)`); any denial aborts (`E_UNAVAILABLE authority_changed`, nothing more is sent). A merely superseded source is pruned from later prompts with the draft claims/quotes citing it | `test_ask_superseded_then_policy_off_is_not_masked`, `test_ask_superseded_mid_request_never_rides_into_later_prompts`, `test_ask_grant_lost_after_send_withholds_everything` |
| 2 | HIGH | the original-question search (`search_only`) ran concurrently with the plan call, holding its transaction and the device `FOR SHARE` lock across the provider request (D-062) | FIXED 9fefb6b: the original question is searched in the map phase inside the request transaction, before `detach`; no DB work overlaps any provider call | `test_ask_no_transaction_is_open_during_any_provider_call` (each search is slowed inside its transaction; fails on 85abf82) |
| 3 | MED | the per-attempt precheck covered only the quote-support ids, not the sources of derived text (verify's answer, check's draft) | FIXED 9fefb6b: `Researcher.complete(carried_ids=sent)`: every attempt (retries, fallback) also re-judges the sources of text sent earlier | covered by #1 tests |
| 4 | MED (sol) | version/project rows read without locks in the final re-check: a concurrent policy change / supersession could interleave | FIXED: the final re-check reads the returned versions and their projects `FOR SHARE` (device → projects → versions) before judging them. The provider precheck-to-send window stays as the privacy module documents it (shared with W2d/W2e: an attempt in that window may complete; its result is re-checked) | existing re-check tests |
| 5 | LOW (both) | over MCP, `detach` refreshes `devices.last_seen_at` — "only ledger writes" is not literal | DOCUMENTED in `research_service` (auth telemetry, the same for every tool call; no memory write) | — |

Not findings (both): VIEW + `_drill` exclude unreadable / device-scoped / policy-off items; L2 summaries only
when every member is in the view; returned handles limited to shown and currently citable versions; final
authority loss withholds answer, claims, quotes and derived queries; `detach(hold_s)` reschedules only after
release; no persistent write by `query_parts`; lineage caps and cache/replay sound.
