# Consult 80 — `memory.ask` read-path scope/privacy review (DUAL: astra-low + sol xhigh; D-130)

You review a clean export of branch `wf-memory-ask` @ fe63fed (HLMemo: Python 3.12, Postgres 17,
MCP server). STATIC review, read-only; do not run tests, docker or network. Never open docs/private/.

## What changed (read these)
- `src/hlmemo/core/research_service.py` — the `memory.ask` loop (map phase in the request tx →
  `detach(hold_s)` → plan/retrieve/answer/[refine]/check/verify with DB phases on fresh pooled
  connections → final re-check → output). Internal drill WITHOUT access events.
- `src/hlmemo/core/memory_map.py` — the caller's VIEW (`load_view`) and the map text; L2 summaries
  shown only when `member_ids ⊆ view` (`load_summaries`).
- `src/hlmemo/librarian/tasks/research.py` — provider wrapper (privacy precheck before every
  attempt, lineage cap), deterministic claim/quote validation.
- `src/hlmemo/librarian/tasks/map_summary.py` — the librarian's L2 summary task (system privacy gate,
  cache table only), `alembic/versions/0009_memory_map.py`.
- `src/hlmemo/server/middleware.py` (`detach(hold_s)`), `src/hlmemo/server/tools/ask.py`,
  `src/hlmemo/server/mcp_server.py` (tools/list filter), `src/hlmemo/librarian/privacy.py` (bodies flag).
- Tests: `tests/integration/test_ask_research.py`, `tests/unit/test_ask_units.py`.
- Background: `docs/decisions/DECISIONS.md` D-062 (no tx across LLM calls, re-check), D-097 (the
  caller's own question text may be sent), D-130/D-136; `src/hlmemo/core/synthesis_service.py` (the W2e
  analog this mirrors).

## Threat model (fixed before the review)
Assets: item text/titles/labels/summaries of items the CALLER may not read, device-scoped items
(`device:*`, never sent to any provider even for their own device), items of projects with
`policy.librarian = off` (never sent), the fact that such items exist (their handles/ids).
Actors: (A1) an authenticated caller with read on project P trying to learn content outside its
authority through memory.ask (question text, crafted project arg, budget); (A2) a model (or a prompt
injection inside memory content) steering plan/refine/answer outputs to request or cite handles of
items it was never shown; (A3) concurrent authority changes during the ~10–20 s request (revocation,
grant removal, token rotation, policy change, item re-scoped or tombstoned); (A4) other projects'
data co-owned with P. Sinks: the provider request bodies (every message), the tool result (answer,
claims, quotes, primary, related, meta incl. queries), the map cache table, the DB (no mutation).

## Severity rubric (fixed before the review)
- HIGH: any path by which content, a title/label, a summary or a handle of an item outside the
  caller's authority (or device-scoped / policy-off) reaches a provider request or the tool result;
  a transaction/lock held across an LLM call; a memory mutation (event, version, access event, job)
  by memory.ask. A HIGH needs a concrete trace (file:line) — a reproducing test will be written.
- MED: a race where such content could leak only under a narrow timing window that the design
  claims to close; a spend-guard/cap bypass; replay/G6 breakage by the cache table.
- LOW: defence-in-depth gaps, misleading docs, test gaps.

## Questions
1. VIEW vs privacy gate: is every item that reaches a prompt (map lines/labels, summaries, excerpts,
   draft claims/quotes in check/verify, read_so_far titles in refine) covered by the gate ids of
   THAT call? Can a hit outside the VIEW reach `_drill` or a prompt?
2. L2 summaries: can a summary built from members the caller cannot read (co-owned, class-scoped)
   be shown? Can a stale summary of a superseded/tombstoned version be shown? Is the system gate in
   `map_summary` sound (device/class-scoped and policy-off items never members)?
3. D-062: any transaction or lock held across an LLM call on the API path or the direct path? Is
   `detach(hold_s)` safe (it only reschedules after the connection is released)?
4. The final re-check and `_AuthorityChanged`: is withholding correct and complete (answer, claims,
   quotes, queries)? Anything returned that was not re-checked?
5. Output: can `primary`/`related`/`claims[].support[].handle` ever name a handle that was not shown
   to the model in that request, or not in the caller's scope at re-check?
6. No mutation: any write besides the ledger tables (`llm_calls`, `llm_budget`, `llm_reservations`,
   `llm_lineage_calls`)? Does `read_service.query_parts` write anything?

OUTPUT (≤ 500 words): `## Verdict` (OK / FIX-NEEDED); `## Findings` table: severity | file:line |
issue | fix (max 8); `## Not a finding` (things you checked and consider sound, one line each).
