## Verdict

FIX-NEEDED

## Findings

| severity | file:line | issue | fix |
|---|---|---|---|
| HIGH | `research_service.py:239-251,294-297,590-598`; `risk_queries.py:244-252` | `search_only` runs concurrently with the plan provider call while holding a transaction and device advisory/`FOR SHARE` locks. Reproducer: suspend `_search` after `fresh_ctx`; the plan HTTP request proceeds with locks held. | Complete search before plan, or start it only after plan. Add a barrier test proving no transaction remains open at provider entry. |
| HIGH | `privacy.py:135-153`; `research_service.py:384-389,462-469`; `research.py:237-246` | `NOT_CURRENT` masks simultaneous grant/policy loss and is exempted for previously sent IDs. After A is used, supersede A plus revoke its co-owner grant: check removes A’s excerpt/ID but sends the unchanged draft containing A’s answer and quotes. | Fail closed on every denied previously-sent ID, or track and remove all derived content provenance. |
| MED | `research_service.py:381,398,443-445`; `research.py:787-804` | Verify contains derived answer/claim prose, but per-attempt gates cover only quote-support IDs. A schema retry/fallback can resend prose derived from another source whose authority was revoked after the first attempt. | Gate every attempt on prompt provenance; conservatively use `gate_ids ∪ sent`. |
| MED | `privacy.py:82-170`; `synthesis_queries.py:57-73`; `research_service.py:657-672` | Version/project rows are read without locks during provider and final checks. An already-running re-scope, tombstone or policy update can commit after the stale read but before send/return. | Read relevant version/project rows `FOR SHARE` using a fixed lock order. |
| LOW | `research_service.py:45-47`; `middleware.py:456-465` | “Only ledger writes” is not literal over MCP: detach updates `devices.last_seen_at`. | Document the auth-telemetry exception and cover the MCP path. |

## Not a finding

- VIEW and `_drill` exclude unreadable, device-scoped and policy-off items.
- L2 summaries require every member in VIEW; stale superseded/tombstoned summaries are hidden.
- Final structured source handles are limited to shown and currently citable versions.
- Final authority loss withholds answer, claims, quotes and derived queries.
- `detach(hold_s)` itself releases before rescheduling.
- `query_parts` creates no persistent writes or access events; map-cache writes are background-only.
- Lineage attempt caps and cache/replay behavior are sound.
- Static inspection only; no tests, Docker, DB, network, or `docs/private/` access.