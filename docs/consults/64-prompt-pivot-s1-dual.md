# Consult 64 — CRITICAL dual review (D-085): pivot-s1-r3 = slice 1 of the English pivot (query rewrite + source cap) on current main

Your cwd is a clean export of c91ee72 (no .git, no secrets). `PIVOT-S1.patch` is the diff from main f8c13d8 to c91ee72; COMMITS.txt lists the commits. Read the rows D-081, D-082, D-088, D-090, D-087 and D-083 in docs/decisions/DECISIONS.md. The earlier reviews are docs/consults/58-*-review-l-pivot.md; they covered slice 1 and slice 2 together, and only slice 1 ships here.

This ships in release R3 with **HLM_QUERY_REWRITE ON** in production, and HLM_RETRIEVAL_SOURCE_CAP OFF. Read-only; do not modify files.

## Implementer's claims (verify, don't trust)
1. **Flags off:** with both flags off, behaviour AND the MCP surface (instructions, tool schemas) are byte-identical to main.
2. **Query rewrite pipeline:**
   - The language is detected on the raw query before the DB transaction. Short, mixed and identifier-heavy queries are left unchanged.
   - A non-English query searches the original immediately. The English branch is added only on a cache hit keyed per (device, token generation, project): in memory, 30 min, per API process.
   - A cache miss schedules an in-process asyncio task (query_rewrite.py:285) with an 8 s deadline. The privacy/policy gate runs before every attempt; a query containing a secret is never sent; the spend guard applies.
   - A guard rejects any added, dropped or changed number, identifier, path, command or quoted string. It is now case-sensitive, NFC-exact and verbatim (D-088 #1). B1 changed classification: slash-joined prose ("okuma/yazma") is not protected, a Turkish suffix after a sentence-final dot is stripped, and quotes are paired left to right.
   - The result carries `query_rewrite: applied|pending|unavailable`.
   - It writes no job or event (D-086 N/A). The only DB writes are the spend ledger and budget rows.
   - The provider task name is exactly `query_rewrite`, which the per-task fallback hook (HLM_FALLBACK_PROFILE__QUERY_REWRITE) will use.
3. **Source cap:** it is applied before the fetch cut and is best-effort when no alternative exists. MERGE DECISION: main's D-087 statement-aware demotion now runs LAST, after the cap.
4. **Gates:** unit 678, flags off and on; integration clean flags on, and off after storm re-runs; G3 0.980; G4 p95 306/308 ms; G-L3 p95 430–469 ms under load from other agents.

## Focus
- **Privacy** (highest priority):
  - Can any query text reach the provider when it contains a secret, when the project has the librarian off or `librarian_cross_project=exclude`, or when the query is device-scoped? What exactly does the gate check before each attempt?
  - Can the rewrite cache leak across devices, token generations, projects or capability scopes? Consider token rotation, revocation and scope changes.
  - Does the English rewrite branch respect exactly the same scope/visibility filters as the original query?
- **Correctness:**
  - Does the rewrite guard (after B1) still protect every real identifier/path/command/flag? Try to construct a rewrite that changes one and is accepted.
  - Is the flag-off byte-identity true, including the D-087 demotion order change when the cap is off? Is the D-087 guarantee (rank_new ≤ max(baseline, 6a96ba1)) preserved when the cap is on?
- **Availability:**
  - Can a slow or failing provider, or a flood of distinct non-English queries, block queries, grow memory without bound, or exhaust the spend guard for the librarian? Check the cache bounds, the number of concurrent tasks and the deadline.
  - Is `pending` handled without blocking?
- Spend guard and ledger accounting for rewrite calls.

## Output contract (≤ 40 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Findings`: a table of severity | file:line | trigger (concrete scenario) | fix. Real defects only.
- `## Ship-with-flag-ON assessment`: one line, yes/no plus the reason.
