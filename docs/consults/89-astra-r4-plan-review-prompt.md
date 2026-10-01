You are a senior release and security reviewer for HLMemo (a self-hosted MCP memory server). Review the PLAN in `docs/decisions/R4-RELEASE-PLAN.md`: a production release that enables the `memory.ask` research librarian with a Gemini 3.8 Flash (reasoning high) writer on ONE Hostinger VM. Nothing is implemented yet, and the goal is to find what would make this release unsafe or wrong BEFORE implementation.

## What you may read
- The plan (above), `CLAUDE.md`, `docs/decisions/DECISIONS.md` (D-108…D-197), `deploy/RUNBOOK.md`, and `docs/status/R3-PROD-DEPLOY.md`.
- The candidate code in the worktree `.claude/worktrees/agent-a2359ac13ecf7557f` (branch wf-memory-ask @ 372abdd): `src/hlmemo/...`, `deploy/...`, `profiles/...`. Also the backfill CLI in `.claude/worktrees/agent-a038bcc4743022b02` (branch wf-supersede-backfill @ 3f87b14): `src/hlmemo/cli/links.py`, `src/hlmemo/ops/backfill_links.py`.
- Verify the plan's file:line facts against the code. Where the plan is wrong about the code, that is a finding.
- Do NOT read anything under `docs/private/`, and do not read `.env` or any key file.

## Rules (follow exactly)
1. **Scope:** only this plan and the code/config it touches or relies on. No style or refactor opinions, and no findings about unrelated parts of the codebase.
2. **Severity:** use ONLY the rubric in plan §8 (CRITICAL / HIGH / MEDIUM / LOW). Tie every finding to one threat id from plan §7 (T1–T9), or "T-other" with a reason.
3. **Concrete findings only.** Each finding must have:
   - (a) `id` (R-1, R-2, …);
   - (b) severity;
   - (c) threat id;
   - (d) location: the plan section/step and/or file:line;
   - (e) the failure scenario: concrete inputs/state → the wrong outcome, in ≤ 3 sentences;
   - (f) evidence: quote ≤ 2 lines of code or plan text;
   - (g) the minimal fix;
   - (h) for CRITICAL/HIGH, a reproducing test or check (how to demonstrate it before the fix).
   No finding without a concrete scenario. No "consider…" items.
4. **Answer every checklist question below** with YES/NO/PARTIAL + ≤ 3 lines + file:line. A NO or PARTIAL that implies risk must also appear as a finding.
5. **Do not rewrite the plan.** At most 20 findings, ordered by severity.
6. **End with a verdict:** GO (no CRITICAL/HIGH), GO-WITH-FIXES (list the exact findings that must be fixed before deploy), or NO-GO (a CRITICAL remains with no viable fix inside this plan's scope).

## Checklist questions
- **Q1 (T1):** Does the proposed cost fix book Gemini thinking correctly for BOTH usage shapes (OpenRouter `usage.cost` present; Google OpenAI-compat with `completion_tokens` excluding thinking, and whether `completion_tokens_details.reasoning_tokens` or `total_tokens` is reliable)? Is the ledger `output_tokens` also corrected, so hourly/daily/monthly caps see the real spend?
- **Q2 (T1):** With `HLM_RESEARCH_PROSE_MAX_TOKENS=16000` and `HLM_RESEARCH_MAX_USD=0.12`:
  - can a single question's worst-case reservations (writer attempt + retries + luna fallback + planner/rerank/attribution) exceed 0.12, so it budget-stops routinely?
  - Can concurrent questions (MAX_IN_FLIGHT 4) × the worst-case reservations trip the HOUR cap at the proposed HOUR 3?
  - Show the arithmetic.
- **Q3 (T2):** Enumerate EVERY place the GEMINI_API_KEY value could land once the plan is executed: the temp key file, install_llm_env.sh output/journal, the `llm.env.release-*` snapshots kept for rollback, container env, `docker inspect`, logs, traces, check_librarian output, the authenticated probe's error messages, shell history, ps args. For each, is it safe? Does the llm.env snapshot machinery keep copies of secrets with correct permissions?
- **Q4 (T2/T6):** Is B5's fix (the installer resolves the writer key; the template has an empty `GEMINI_API_KEY=`) sufficient so that a later `install_llm_env.sh` run keeps the key, and a missing or wrong key FAILS `evaluate --release r4`? Could the new authenticated probe leak the key or spend meaningfully?
- **Q5 (T4):** With request durations up to 170 s:
  - what happens to uvicorn workers, the DB pool, MAX_IN_FLIGHT, the detached-request middleware, and other MCP tools (memory.query/write) under 4 concurrent memory.ask calls?
  - Is there a head-of-line or starvation risk?
  - What do Caddy (flush_interval -1, no response timeout) and the MCP client do on a 170 s call?
- **Q6 (T8):**
  - Can the writer silently fall back to luna in prod (breaker, truncation, key failure, Google 5xx) without an operator noticing?
  - Is `meta.flags.writer_used` enough, or must the plan add a metric/alert or ops status line?
- **Q7 (T3):**
  - Is applying curated links via `stack.sh exec -T api hlm links backfill --apply` safe: the head re-check for stale ids, a partial apply on error (one event? transactional?), replay determinism, and `--revert` correctness?
  - What happens to these links on `deploy.sh --rollback` (the dump restore) and on a later redeploy?
- **Q8 (T6):** Does the provenance/fingerprint check (llm_env_release.py FINGERPRINT_KEYS; RUNBOOK W:692-699) accept the new keys (writer profile, timeouts, the prose max tokens) or block deploy/rollback? Are the review-77 residuals (D-122) acceptable for this release or blocking?
- **Q9 (T6):** Is plan §6 (rollback) complete and executable as written? In particular:
  - the "behaviour-only" rollback (re-install the R3 env on R4 code);
  - whether the R3 env + 0009 table cause any issue;
  - the snapshot overwrite (one slot).
- **Q10 (T5/T7):**
  - Is the tracer guaranteed OFF in prod?
  - Is any memory.ask path able to include another project's items (scope/isolation), given the rerank/xref/temporal pull-ins?
- **Q11 (T9):** Before pushing main + r4-rc to the public origin, what exactly must be verified (gitleaks config, .gitignore for docs/private, large files, any key-like strings in DECISIONS/consults/RUNBOOK)?
- **Q12:** Is any step in §§2–5 missing, mis-ordered or unverifiable? Name it.

## Output format (markdown, nothing else)
1. `## Checklist`: Q1–Q12 answers.
2. `## Findings`: a table (id | severity | threat | location | one-line summary), then one detailed block per finding with the fields (a)–(h).
3. `## Verdict`: GO / GO-WITH-FIXES (list) / NO-GO (reason).
