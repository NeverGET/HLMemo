# Consult 103 — R4.3 WS-1 item 1: superseders pulled into memory.query hits (design consult, Astra low)

You are reviewing a design + implementation in the HLMemo repository (your working directory is the
worktree, branch `r4.3-ws1`, commit `cf08dff` on top of main `270cbc2`). Read-only. Answer in English.

## What to read
- Plan: `docs/decisions/R4.3-PLAN.md` §1 "Pull superseders into memory.query hits" and the owner answer
  (D-237): *"Keep the old hit visible and flagged; put its superseder right after it."*
- Diff: `git show cf08dff` (item 1 only). Key places:
  - `src/hlmemo/core/read_service.py` `query_parts` (~L209; hook ~L303-307; `total=` ~L359) and
    `_pull_superseders` (~L367).
  - `src/hlmemo/core/supersession.py` rule 4 in the module doc (~L31), `PULL_TOP`/`PULL_MAX` (~L276),
    `superseder_candidates` (~L280), `plan_superseder_pull` (~L294), `place_pulled` (~L327).
  - `src/hlmemo/db/read_queries.py` `superseded_hits` (~L817, B3, unchanged) and the new
    `superseder_chunks` (~L878).
  - `src/hlmemo/core/retrieval.py` `Fused.supersedes`, `render_hit` (~L339-365): additive keys.
  - `src/hlmemo/core/research_service.py` ~L1626: OFF in memory.ask's own search.
  - Tests: `tests/unit/test_r43_superseder_pull.py`, `tests/integration/test_r43_superseder_pull.py`.

## The design as built
1. Candidates: the flagged hits (B3 `superseded_by`, already authorised: link and superseder pass §4.4 (a),
   the temporal point and the statuses) among the first 5 of the FINAL order, in rank order; each one's
   superseders newest first (highest version id); a superseder version only with the first hit naming it.
2. Resolution: a superseder whose version is already in the deduped result (`ordered`, head or beyond)
   is resolved from it; any other one through ONE SQL (`superseder_chunks`) under the query's full hit
   predicate `QueryFilters.hit_where()` (authz (a) on the version row, temporal point, statuses, `kinds`,
   never the project card). Not returned = not placeable.
3. Selection (`plan_superseder_pull`): at most 2; depth 1 (a placed hit places nothing); skip if hidden by
   D-057, already placed, or already ranked ABOVE its hit or DIRECTLY AFTER it.
4. Placement (`place_pulled`): directly after the first hit naming it; a superseder the query ranked lower
   is MOVED (no duplicate; `omitted` unchanged); one the query did not return is INSERTED (score 0.0,
   chunk = best lexical rank, then vector distance, else chunk 0; `omitted` +1 via `total`). It gets its
   own B3 flag (`superseded_hits`) and `supersedes` = item clues of the top-5 hits naming it. Additive keys
   `pulled:"superseder"`, `supersedes:[clue]`; contract stays query/2. `pack_query` is unchanged
   (prefix packing; lower hits are cut first; `budget.used` exact).
5. Scope: ON for public `memory.query` and the synthesis path (`query_synthesize` → `query_parts`);
   OFF in memory.ask's `_search`. D-057 hiding is not re-run for placed hits.

## Deliberate deviations from the plan text (please judge them)
- **D1 hook location.** The plan says "after `superseded_hits`, before `newer_first_on_ties`". Built: after
  `superseded_hits` AND after `newer_first_on_ties` + `demote_partially_superseded`. Reason: inserting
  before them lets the exact-tie rule (same title, newer first) or the D-076 demotion move the superseder
  away from (or before) its hit, and an inserted hit between two tied same-title hits changes main's
  ordering of those existing hits. After them, the placement is exact and every other hit keeps main's
  relative order.
- **D2 "skip logical ids already present".** Built as "already ranked above its hit, or directly after
  it"; a superseder the query ranked lower is moved up. Reason (oracle on the 10-03 prod copy, 659 real
  planner queries, budget 3000, main's code): of 680 superseder entries on flagged top-5 hits, 314 were not
  in the fetched head, 173 in the head but NOT packed (invisible), 153 packed below the hit, 40 above it.
  With "present = in the head", the 173 invisible ones would never surface. A whole-scope superseder in
  the result already hides its target (D-057), so moves concern part-scope links in practice.
- **D3 score.** A pulled-in hit shows `score: 0.0`; a moved hit keeps its own score (scores are then no
  longer non-increasing down the list). `weak()` in synthesis reads only `hits[0]`, never a placed hit.

## Threat model (what must never happen)
- **T1 visibility leak**: a superseder the caller may not see (device scope, project scope, archived
  without include_archived, not valid/known at the as-of point, a pinned link the caller may not read or
  that does not apply to the hit's version, a superseder in another project not shared) is named or
  pulled; or a pulled hit reveals text/ids of anything hidden (its own `superseded_by` must be the same
  B3-authorised list).
- **T2 budget accounting**: `budget.used` not the exact o200k measure, `used > limit`, `omitted` wrong
  (double counting a moved hit, missing a pulled-in one), or the packer cutting anything but the tail.
- **T3 ordering**: a placed hit not directly after the first hit naming it; any other hit's relative order
  changed vs main; a cap or depth breach; nondeterminism; G-SURF (tools/list tokens) changed.

## Severity rubric
- HIGH: a T1 leak reachable by any caller; `used > limit`; a wrong `omitted`; a crash on a reachable input.
  A HIGH needs a concrete reproducer (inputs + expected vs actual).
- MEDIUM: a T3 violation, or a design choice (D1-D3) that undermines the owner answer or the plan's bar
  (≥80% of pulled hits state the current value; p95 +10 ms max).
- LOW: clarity, naming, tests missing a case.

## Questions
1. Any T1/T2/T3 defect? (cite file:line; a HIGH with its reproducer)
2. D1, D2, D3: accept, or change to what?
3. Selection order: per hit in rank order, newest first, cap 2 — the first flagged hit may take both
   slots. Better as round-robin across hits? (The 10-03 caps query: hits 1-4 form chains v764→v765→v772
   and v424→v425→v912, all part-scope.)
4. Is the test matrix missing a case that matters?

Reply as: verdict (GO / GO with changes / NO-GO), then numbered findings with severity, then answers.
