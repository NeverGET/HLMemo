## 1. FIX AUDIT

Static review of `3d842c8`; no pytest or database operations.

- **S1 — CORRECT.** [read_service.py:507](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/read_service.py:507) resolves authorized survivor ancestry on every raw page. Drilldown returns survivor content without event payloads; card pinned sources expose authorized IDs/staleness ([read_queries.py:365](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/read_queries.py:365)). Those paths need no provenance walk.
- **S1b — CORRECT.** [read_service.py:413](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/read_service.py:413) filters embedded links against resolved endpoint authorization, including raw continuations.
- **C4 — CORRECT.** [read_queries.py:159](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/read_queries.py:159) authorizes all candidate lists using version scope; restrictive copied chunk scope cannot suppress hits.
- **C2 — CORRECT.** [replay.py:168](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/replay.py:168) inserts links only after every batch version/chunk exists, accommodating forward and cyclic references.
- **C3 — CORRECT.** [write_service.py:642](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:642) collects all overlapping segments; lines 679/911 deduplicate and supersede them before replacement.
- **S2 — CORRECT.** [write_service.py:470](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:470) authorizes the selected pinned/head endpoint before disclosing relationship mismatches.
- **C5 — INCOMPLETE.** Both entry points capture raw arguments before validation ([write_service.py:197](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:197), line 238); MCP handlers pass `raw=args`. Canonical sorting makes key-order-only retries replay successfully, and projection rebuild uses resolved items. However, identical retries of **pre-upgrade events with omitted defaults** now conflict; exact input below.
- **C1 — INCOMPLETE.** [middleware.py:117](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/middleware.py:117) buffers everything until the application returns. Authenticated `GET /mcp`, `Accept: text/event-stream`, with no `MCP-Protocol-Version`, reaches persistent SSE despite JSON response configuration: headers/pings never flush. Finite responses commit first correctly. Disconnecting between commit and send leaves a durable, unacknowledged write; retrying the same request ID recovers it.
- **C6 — CORRECT.** [worker/main.py:293](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/worker/main.py:293) places copied inserts, inferred inserts, and the lease-fenced UPDATE in one transaction. The earlier commit covers only planning reads.

## 2. REGRESSION RISK

[read_queries.py:239](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/read_queries.py:239) adds a **MATERIALIZED boundary, not a new limit**. All matches survive until authorization, temporal/status/kind filtering, aggregation, and ordering; the existing final limit remains.

No identifiers bypasses trigram search; many terms retain maximum similarity; `include_archived` filters before limiting; budgets operate downstream; raw/drilldown cursor pages never use this query. No new hit-dropping shape follows from this change. A trigram-only gold hit ranked 21 is excluded by the existing `T_MAX=20`, but that predates round 8.

## 3. NEW DEFECTS

1. **SSE withholding:** [middleware.py:118](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/middleware.py:118), with the exact GET above. Indefinite streams accumulate buffered messages without delivering headers.
2. **Upgrade breaks retries:** [write_service.py:567](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:567). Submit before upgrading, then retry identically afterward:

   `{"project":"aa","request_id":"00000000-0000-0000-0000-000000000001","client":"x","items":[{"kind":"fact","title":"t","body":"b"}]}`

   The old hash includes inserted defaults; the new hash does not: `E_REQUEST_ID_CONFLICT`. Close requests have the same regression.
3. **Readiness initialization race:** [app.py:142](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/app.py:142). Concurrent cold `GET /ready` calls share an unlocked cache: one can clear `hashes` before another reads it, producing false 503; overlapping calls also duplicate model loading.

## 4. O2 SPEC

**Test:** `test_worker_sigkill_after_vector_inserts_restarts_and_reclaims`.

Use an isolated Compose project/database, real baked models, one worker, and `unless-stopped`. Warm for ten seconds. Seed predecessor vectors and a revision containing one copyable and one novel chunk. Install a persisted, one-shot barrier immediately before `_mark_done`, after both inserts. An independent connection must see `running` and zero committed revision vectors.

Release the barrier into PID-1 self-`SIGKILL`; never manually restart. Assert automatic `RestartCount` increment and completion within `LEASE_SECONDS+60`: attempts=2, cleared lease, exactly one embedding per chunk/model/revision/preprocessing version, copied/inferred vectors correct, no duplicate job.

Expose a heartbeat every ≤10 seconds containing last committed job/time, ready-job count, and oldest-ready age; this distinguishes idle operation from stalled progress.

## 5. GO / NO-GO

**NO-GO:** resolve C1/C5 and readiness concurrency, prove O2, apply S4’s container recreation, then rerun G7 with all three real CLIs before declaring Phase 0 DONE.