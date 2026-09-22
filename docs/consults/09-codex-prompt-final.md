Co-architect round 9: FINAL review of the D-027 fixes you yourself finished in round 8. You are now reviewing your own implementation work, so be adversarial with it: assume the round-8 author (you) made mistakes, and try to break the fixes rather than confirm them.

STATE
- The round-8 work is now COMMITTED (commit 3d842c8). `git show 3d842c8 --stat` and `git show 3d842c8` give you the exact diff.
- Independently re-run by the orchestrator: ruff clean, 199 unit/fixture + 83 integration + 8 gate tests green, G3 Recall@5 0.930, G4 p95 262.8 ms.
- Status recorded in DECISIONS.md D-028: 12/13 D-027 items VERIFIED, O2 WEAK.
- Your own round-8 caveats: O2 crash/restart + progress monitoring missing; S4 verified only in rendered compose config (the running dev db container still publishes on all interfaces until recreated); G7 not re-run after these changes.

HARD RULES
- Read-only. Do not edit, do not commit, do not run pytest (it needs a DSN and the orchestrator owns the databases). You may read files and run `git show`/`git diff`/`git log`, `rg`, and `docker compose config`.

TASK — answer in English, max ~700 words, ONLY these sections:

1. FIX AUDIT. For each of S1, S1b, C4, C2, C3, S2, C5, C1, C6 examine the committed code (not the test names) and state: CORRECT / INCOMPLETE / WRONG, with file:line and, where not CORRECT, the precise input that still defeats it. Pay particular attention to:
   - S1: is survivor provenance resolved for EVERY read path (raw, drilldown, cursor pages, card pinned sources), or only the one the test exercises?
   - C5: verbatim hashing — is the raw dict captured before ANY pydantic coercion for both write and call_the_day, including through the MCP handler (`handlers.py` passes `raw=args`)? What happens on replay when the stored raw differs only in key order?
   - C1: the response is now deferred until after the outer commit — what happens for streaming/SSE responses, and for a client that disconnects between commit and send?
   - C6: is the lease fence check inside the SAME transaction as the vector insert, with no earlier commit, for BOTH copied and inferred vectors?
2. REGRESSION RISK. The C4 fix first caused a 5279 ms p95 and was then repaired at read_queries.py:239 with a query limit that preserves GIN usage. Is that limit correct for ALL query shapes (no identifier terms, many terms, include_archived, large budgets, cursor pages), or can it silently drop a gold hit that the unlimited query would have found? Name the shape that would break it, if any.
3. NEW DEFECTS introduced by the round-8 diff (max 4, file:line + failing input).
4. O2 SPEC. Write the exact crash/restart test you want for the worker (name, setup, kill method, assertion) and the minimal progress-monitoring signal the worker should expose. Be concrete enough to implement directly.
5. GO / NO-GO for declaring Phase 0 DONE, one line, given that G7 (three real CLIs) still has to be re-run afterwards.
