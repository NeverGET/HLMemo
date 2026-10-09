## Verdict

**NO-GO** — one new F1 path-expansion HIGH remains.

1. **HIGH — query/fragment stripping and malformed-bracket expansion — `src/hlmemo/librarian/tasks/research.py:2081-2104`.**
   Excerpt ``Use `POST /api/allow|deny?role=user` exactly.``; answer ``Use `POST /api/deny`.`` is kept on
   R4.2 and rejected on main: a required suffix disappears. Mixed alternation+nested/unbalanced tokens
   also expand. Minimal fix: preserve query/fragment byte-exact in every variant or reject expansion;
   reject all bracketed tokens unless `_brackets_flat(token)` is true.

2. **Round-1:** URL and bracket parsing PARTIAL because #1 remains; restate CLOSED; independent dangling
   sentence CLOSED; preview CLOSED; citation reachability CLOSED; MAIN_REPAIR manifest CLOSED/obsolete;
   merge-before-cap CLOSED; prompt/schema audit version CLOSED with all four files byte-identical to main.
   Focused TR/DE cases (`Bunun yerine`, `Bu nedenle`, `Stattdessen`, `Dieser`, `Bu servis`,
   `Diese Version`) behaved as intended.

3. **Change-set B intact.** Exact declared Google list-wrapped 503 settles at $0 and falls back;
   undeclared/malformed bodies remain worst-case; post-reservation exceptions settle once and ledger.

4. **Prod `fda8fa0`:** ancestor; no migration/deploy-tool change; two baked Google profile files gain the
   policy; prod llm.env needs no new key; rollback restores prior image/profile policy; no reverse
   migration is needed.

5. **Pre-existing main literal weaknesses do not independently block R4.2.** The blocker is the new F1
   acceptance above.

Validation: targeted answer-quality/provider/integration and deploy/profile tests passed; diff/check and
prompt SHA checks passed. No Docker-compose rehearsal, live provider call, or prod smoke was performed.
