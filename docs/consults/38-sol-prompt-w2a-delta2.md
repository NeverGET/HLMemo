You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Your consult 37 (docs/consults/37-sol-review-w2a-delta.md) said DO-NOT-MERGE. The implementer added commit 3e19c15 on branch `worktree-agent-a21f9ce923d827a45` (worktree /Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45). Review `git -C <wt> diff 9047471 3e19c15`. Also read D-064 in docs/decisions/DECISIONS.md, which clarifies the DF staleness rule.
Claims:
- a privacy check before every provider attempt (retry and fallback); working-memory rules only when librarian-authored, rule-shaped and redacted;
- every cassette content shape normalized and redacted before persisting;
- the D-064 DF cache (≤5 s stale only during a refresh, unfiltered after a failed refresh or when an invalidation is >30 s old, one 2 s cold budget); G-L3 asserts stall/503 overlap;
- migration 0006 GIN ALTER + flush in a separate autocommit block with lock_timeout 3s, fail-fast with no partial state;
- an atomic lineage counter (llm_lineage_calls, INSERT … ON CONFLICT … WHERE calls < cap);
- replay restores run_after, and questions reference job_key;
- the librarian_questions columns are listed in the commit/tests.
STATIC REVIEW ONLY (no tests, docker or edits). Decide per item FIXED / PARTIAL / NOT FIXED, trying to break each. In particular:
- can the separate autocommit index block leave the DB with the table DDL committed but the indexes still fastupdate=on, and is a re-run then idempotent and correct?
- does the lineage counter over-count on provider failure (reserve without a call) and starve legitimate work?
- does the privacy re-check hold the same snapshot semantics as the apply-time recheck?
Also list anything that would still block MERGE for a librarian that runs in OBSERVER role only (proposals, no mutations) in production.
OUTPUT (≤ 400 words): ## Verdict (MERGE / MERGE-WITH-FIXES / DO-NOT-MERGE); ## Per-item status; ## Remaining findings (table, max 5); ## Blocking for observer-only production? (yes/no + why).
