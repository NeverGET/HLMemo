# Consult 44 — W2b/W2c implementation review request (implementer B → gpt-6-sol)

You are reviewing the W2b (placement, contradiction, cross-project check) and W2c (questions +
`memory.answer`) implementation of HLMemo on branch `worktree-agent-a06aaf3159b7fb21f` (the current
working directory). Binding specs: `docs/decisions/PHASE2-4-ROADMAP.md` (CC-1..CC-5, §2 W2b/W2c, §4b),
`docs/decisions/DECISIONS.md` D-058, D-062, D-066, D-067, D-069. Read-only review; be concrete (file:line).

Files to review (new or changed):
- `src/hlmemo/librarian/trigger.py`, `core/write_service.py` (`_librarian_jobs`, `WriteDeps.librarian_enqueue`)
- `src/hlmemo/librarian/tasks/write_review.py` (the W2b handler), `librarian/candidates.py`, `db/librarian_queries.py`
- `src/hlmemo/librarian/guards.py` (D-067), prompts `librarian/prompts/{place,relate,relate_verify}/v1.*`
- `src/hlmemo/librarian/worker.py` (`apply`, `_plan_questions`, `_assign_batches`, `_apply_approved`), `librarian/actor.py`
  (`materialize`, `version_close`, `signal_upsert`, batches), `librarian/roles.py` (batch decision)
- `src/hlmemo/librarian/questions.py` (`memory.answer`, expiry, query notices), `server/tools/answer.py`
- `src/hlmemo/core/read_service.py` (D-057 supersession, `librarian` block, `query/2`), `core/supersession.py`
- `src/hlmemo/db/replay.py`, `alembic/versions/0008_librarian_tasks.py`, `src/hlmemo/ops/librarian.py`

Design summary (what I claim):
1. Trigger: write/call_the_day (priority 3) and import (6) enqueue `librarian_write:<event_id>` (≤ 4 versions
   per job, split keys `…:<k>`) in the SAME transaction; descriptors recorded in `resolved.librarian_jobs`.
2. Candidates: current grants ∩ enqueue-time `question` capability ∩ policy≠off; every project of a
   candidate must be in that set; scope all|class:<own>, never device:*; same-project top-8 (compatible
   kinds) + cross top-5 lesson/experience/fact; drop rule (cos < 0.80 and no lexical hit) BEFORE the cut.
3. D-067: cited ids only; quote evidence (2+ words of each side) else capped at question; supersession
   must follow t_valid; calibrated tiers (low never action); high-impact (contradicts+supersedes, cross
   dup/refines→widen) needs the verifier (`relate_verify`, decomposed same_subject/conflict/current, by
   default on the OTHER profile); disagreement downgrades (plain contradiction question) or drops.
4. Roles: observer writes version_signals (client-unset fields; client importance copied as src=client)
   + questions in batches (≤25 open per open batch); 0 links, 0 closes. autonomous applies only
   `auto_ok` proposals (high conf, verified, quotes ok, same home, same scope, not pinned, no experience,
   newer valid_from ≥ older). assistant applies approved batches per question under that question's own
   capabilities (recheck; stale → superseded; recheck fail → authority_lost). widen_scope never applied
   by the librarian.
5. Invalidation = `version_close`: supersede every current row overlapping [cut, ∞), re-insert the part
   before the cut as survivors (same chunks, embed jobs recorded), nothing after the cut. Deviation: the
   roadmap says "through the write service"; the write service has no validity-close op (a correction
   re-creates a survivor after the cut). widen_scope accept DOES go through the write service.
6. W2c: `memory.answer` authz = write on the project + write on union(question projects, action
   projects from current rows); unreadable subject → E_NOT_FOUND; idempotent per request_id (stored ack);
   non-open or past expires_at → E_VERSION_CONFLICT{status}; accept rechecks staleness (→ superseded, no
   mutation); custom → re-plan job; every answer → librarian-rule fact (note dropped if it reproduces
   item text). Expiry: worker sweep every 10 min, one `librarian` event per project.
7. Read side: an applied `supersedes` link between two hits hides the superseded one (authz(a) +
   temporal_live on the link); exact-score ties with the same normalized title → newer first. Query adds
   `contract_version: "query/2"` always and `librarian: {pending_questions, notices ≤3}` (template text
   only) budgeted to ≤10% of token_budget, packed BEFORE the hits (deviation from "after hits": the
   preflight must see them).

Please answer:
A. Correctness/safety defects (privacy, capability, role ladder, replay determinism, idempotency, races),
   ranked by severity, each with file:line and a concrete fix.
B. Is the D-067 design sound (esp. the verifier decomposition and the tier table)? What would you change
   before G-LIVE-B is treated as release-blocking evidence?
C. Are the two deviations (5, 7) acceptable? If not, what exactly instead?
D. Anything in the gates (tests/integration/test_w2b_pipeline.py, test_w2c_questions.py,
   test_gp1_replay.py, test_gp3_isolation.py, eval/live/run_w2b.py) that does not prove what it claims.
Verdict line: MERGE / MERGE-WITH-FIXES / NO, with the must-fix list.
