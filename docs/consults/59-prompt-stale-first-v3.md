# Consult 59 — design: librarian v3 for stale-first (routine consult, D-085)

The repo is at main (cwd). Read docs/decisions/DECISIONS.md rows D-057, D-067, D-072, D-074, D-076, D-086 and D-087 first. Do not modify files.

## Problem
G-E-TEMP stale-first means an outdated item ranks above the current gold item in the L2 top-3. The gate is ≤4 of 15 on corpus A. It stayed at 8/15 after librarian v1 and v2.

A deterministic diagnosis was run on the pinned v2 run: no LLM calls, candidate lists rebuilt in a disposable DB, all 99 sampled proposals map inside them. Findings:
- **No link was applied on any stale-first pair.** None of the 15 A cases and none of the 7 B cases has an applied `supersedes` link on its (stale, current) pair.
- **Candidate misses:** 8 of the 15 pairs never reach the relate prompt.
  - The candidate caps are `librarian/candidates.py:36/38` (SAME_TOP 8, DOC_TOP 3). Raising them to 24/8 would add 2 of the A cases.
  - The kind compatibility rule (`candidates.py:45/53`) never pairs doc_chunk with episode. Fixing it adds 1 A case, whose gold label is questionable, and 1 B case.
  - COS_MIN has no effect with E5.
- **Judge misses:** 7 of the 15 pairs reach the prompt and yield no proposal, a contradicts-only link, or a dropped supersede.
  - The drops come from the `supersedes_against_time` guard (`guards.py:441-446`) and the `verifier_direction_disputed` guard (`guards.py:565-569`).
  - In the eval corpora valid_from is the file's last commit date or mtime, and git-day episodes are dated 00:00. In production, D-072 imports keep the server's recorded_at, and file dates are provenance only.
  - The per-pair judgements were not exported, so "judged none" cannot be told apart from "verifier rejected".
- **Retrieval position:** on corpus A the current item sits outside the top-3 in all 8 cases (hit rank 4–28).
  - Reordering a pair (newer above older) fixes 0 cases.
  - Only CLOSING the stale item, or PULLING the current item up next to or above the stale one, can move the gate.
  - Upper bound with a perfect judge: A 8 → 4 by closing, or 8 → 3 by closing plus pull-up.
- **Read side:** the read side ignores contradicts-only links. The shipping read side (J, 6a96ba1-style, statement-aware) is neutral, and v2's partial links gave zero net effect. Rule 3 as of a65a8f5 (move a partly superseded item behind its superseder) cost −2.7/−4.8 points and will not ship.
- **Precision:** v2's strict precision is .48 (pooled .54). Partial supersedes score .68; contradicts-only scores .12, and most of those are real updates whose supersede was dropped.

## Constraints
- Observer stays until the hold-out gates pass (D-076). In the assistant role, closes are owner-approved. widen_scope is always owner-applied (D-086).
- D-067: LLM usage is engineered, not trusted. Every mutation must pass deterministic guards.
- No read-side rule may make any category worse than −3 on G-E-W2b. A must-not-regress test exists for multi-fact items.
- Provider-agnostic (D-017).

## Questions
1. **Levers:** Which levers, in what order, form v3? Rank them by expected stale-first gain per unit of precision risk.
2. **Time guard:** How should `supersedes_against_time` treat temporal evidence? The options are server recorded_at, source provenance dates (commit or mtime), and in-text dates. Consider both the eval corpora and a real Phase 5 import, where every item gets the same recorded_at.
3. **Pull-up rule:** Is a read-side pull-up rule acceptable, and in what exact form? For example: when a stale item S with an APPLIED whole-scope supersede by C appears in the fetched set, inject C directly above S, even when C was not retrieved. Give its failure modes and the precondition that keeps it neutral when links are wrong.
4. **Close vs demote:** Should a whole-scope supersede close S (validity end) or only demote it at read time? Consider reversibility, replay and the owner-approval flow.
5. **Instrumentation:** What must the next hold-out run export (per-pair judgements etc.) so that "judged none" and "verifier rejected" can be told apart?
6. **Candidate budget:** What is the cost/latency impact of raising the caps to 24/8? Is there a smarter candidate expansion, such as always including items that share a source path or title with the new item?

## Output contract (≤ 40 lines)
- `## Recommendation`: v3 = an ordered list of changes, each with its file:line anchor and the expected A stale-first delta.
- `## Guards`: the exact rules for the time guard and the direction verifier.
- `## Read side`: pull-up yes or no, plus the exact rule and precondition.
- `## Measurement`: the exports and the gates for the next hold-out run.
- `## Risks`: at most 5 bullets.
