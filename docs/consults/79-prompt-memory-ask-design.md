# Consult 79 — `memory.ask` (research librarian, D-136) design check (routine, astra-low)

You are reviewing a DESIGN before/while it is implemented on branch `wf-memory-ask` (from main adb6134) of
HLMemo (Python 3.12, Postgres 17 + pgvector, MCP server). Read-only. Answer in ≤ 600 words: a verdict
(OK / FIX-NEEDED) and numbered findings with severity (HIGH = scope/privacy leak or data mutation,
MED = wrong behaviour / gate risk, LOW = polish). Read the code you need: `src/hlmemo/core/synthesis_service.py`
(the closest analog, W2e), `src/hlmemo/librarian/tasks/synthesis.py`, `src/hlmemo/librarian/privacy.py`,
`src/hlmemo/core/read_service.py`, `src/hlmemo/server/middleware.py` (detach), `src/hlmemo/librarian/worker.py`,
`src/hlmemo/db/replay.py`, `docs/decisions/DECISIONS.md` D-130/D-133/D-136. Never open docs/private/.

## Spec (D-136)
Read-only tool `memory.ask(question, project?, token_budget?)`: plan (map + question) → 3–5 queries + ≤ 6 map
sections → RRF fuse → drill ≤ 12 chunk handles → answer {answer in the caller's language, confidence, abstained,
primary ≤ 3 {handle, path, quote}, related ≤ 5 {handle, path}, meta {queries, calls, cost_usd, latency_ms}}; a
COMPLETENESS pass; one refinement only on abstain/low confidence; ≤ 5 LLM calls. Caller capabilities, privacy
precheck on every text sent, spend guard, provider task `research` (per-task fallback). Memory Map per project
(~6k tokens, path tree, item handles vN(k), section entries vN.M, spread truncation) with async LLM L2 summaries
(task `map_summary`, cached by source digest, debounced refresh, best-effort). Flag `HLM_RESEARCH_ENABLED`.

## Design as planned
1. **Tool / transaction shape (D-062).** app-bound handler. Phase A in the request transaction (savepoint):
   resolve the project (default = the device's only readable project, else E_INVALID_ARG), `read` grant, load the
   caller's VIEW = current active non-card items of the project with `device_scope ∈ {all, class:<caller class>}`
   (never `device:*`), every `project_id` of the item readable by the caller AND `policy.librarian <> 'off'` (the
   privacy gate's rules, evaluated in SQL+Python); load cached L2 summaries. Then `detach(hold_s=research_timeout)`
   commits + returns the request connection. **Middleware change:** `detach` gets an optional `hold_s`: after the
   release it reschedules the request's asyncio deadline (15 s `request_db_timeout_s`) to `now + hold_s` (capped),
   because the loop needs ~10–20 s and no DB resource is held any more. Every later DB phase uses a fresh pooled
   connection (`reconnect`), first re-resolving the device (trusted, unexpired, same token generation, current
   grants) into a fresh AuthContext, then runs `read_service.query_parts` per query (the memory.query path, same
   scope rules) and an INTERNAL drill (`version_live` + `chunk_spans`, NO `access` event, NO last_access_at touch).
   Every hit/drilled handle whose version is not in the VIEW is dropped before any prompt (so device-scoped hits
   of the caller, co-owned items with an ungranted/policy-off project, items written after phase A never reach the
   LLM). Final re-check (fresh short tx): authority + every returned handle still citable (current, active, not
   device:*, visible, read grant on every project), else dropped; no quoted primary left → abstained.
2. **Privacy precheck.** Before every provider call: `privacy.gate` over EXACTLY the version ids whose text is in
   the prompt (map: every item of the rendered map + summary members; excerpts: their versions), with
   capabilities {trigger_device_id, token_generation, question = caller's readable projects}. A denied id is removed
   (map re-rendered / excerpt dropped) and the gate re-run; the provider's own `precheck` hook re-runs the strict gate
   immediately before each attempt (retries + fallback), raising → the call is not sent. `privacy.load_items` gets a
   `bodies=False` mode (the map gate covers ~550 ids).
3. **Map.** Deterministic, rendered per request from the VIEW (per-version structural entries cached in-process by
   version_id — versions are immutable). File key = `source.path` without `#anchor`, else parsed from the title
   (" § ", " · path", "git log <day>"), else a kind cluster. Budget fill: header + group + one line per file
   (mandatory; spread-truncated if even that overflows), then L2 summaries (largest files first, ≤ 40 % of budget),
   then section entries round-robin by size (priority = chunks/(1+taken), each file's candidates in bit-reversal
   "spread" order). Large items (> 2 chunks) are drillable only by chunk handle.
4. **L2 summaries.** Table `memory_map_summaries` (migration 0009_memory_map, CREATE TABLE only, not in
   `replay.PROJECTION_TABLES`, rebuildable = deletable): (project_id, source_key) PK, digest = sha256(sorted member
   version ids), member_ids bigint[], summary, profile, prompt_version, status/failures, updated_at. Written ONLY by
   the librarian process (`LibrarianWorker.maybe_map_summaries`, every 60 s, ≤ N groups per cycle, debounce: newest
   member older than 120 s, failure back-off). Members = current active items of the file with device_scope='all'
   and every project policy on (system gate; no device). Shown to a caller only if member_ids ⊆ the caller's VIEW
   (never leaks a class-scoped/ungranted item, never shows content of a superseded version; a newly added item only
   makes it incomplete until the refresh). Not a job row (jobs are event projections).
5. **LLM.** One task `research` (one system prompt with JOBs plan / answer / check / refine, one schema, job-specific
   validation) so the per-task fallback key is `research`; latency attempt policy with per-call deadlines inside a
   total `HLM_RESEARCH_TIMEOUT_S` (30 s). Calls: plan, answer, [refine, answer2 only if abstained/low], check
   (completeness: sees question + draft claims + all excerpts, returns the full revised answer with a `missing`
   list first) → ≤ 5. The original question's search runs concurrently with the plan call.
6. **Validation.** Excerpt ids = handles. A claim's quote must occur in a cited excerpt after normalisation (NFKC,
   casefold, quotes, markdown chars, whitespace, numerals `(?<=\d)[,.](?=\d)` → '.' so TR "1,6" = "1.6"); if found in
   another SHOWN excerpt it is re-attributed; the returned quote is the ORIGINAL substring of the excerpt. Literals
   (numbers/ids/paths) must be supported (synthesis.claims/supported + numeral normalisation). quote+literals ok →
   kept; literals ok, quote not → downgraded (its handle → related, confidence lowered); literals not ok → dropped.
   Answer-text sentences with literals unsupported by any shown excerpt or the question are dropped. No quoted claim
   left → abstained. Primary = model primaries with a verified quote (else related).
7. **No mutation.** memory.ask writes no event, version, chunk, link, job, access event or last_access_at; the only
   writes are the spend guard's `llm_calls`/`llm_budget`/`llm_reservations`/lineage rows (ledger).

Questions: (a) any scope/privacy hole in 1/2/4? (b) is the `detach(hold_s)` middleware change safe? (c) is
`member_ids ⊆ VIEW` the right summary rule? (d) anything that would break replay/G6 or the gates?
