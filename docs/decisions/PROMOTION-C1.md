# Librarian promotion, one class: C1 "a newer fact replaces part of a current fact" (2026-11-21)

Cemal (2026-11-21): "artık librarian promote edilecek noktaya getirelim". This file is the plan, the threat model
and the review rubric for the code. The decision that opens it (superseding D-246 for this class) is written after
the reviews, and the grant on production needs Cemal's OK per project.

## Why a class grant, not the role ladder
A code study of 2026-11-21 showed that the existing ladder cannot do this promotion safely:
- `autonomous` never applies the part class: `auto_ok` requires `close_ok`, which only a whole-scope supersession
  gets (`guards.finalize`).
- `autonomous` (and `assistant`) would instead act on other classes: action-tier `relates_to` links, whole closes,
  `pair_check` links without a verifier, and every pending answer (`accepted_pending` released, approved batches
  leasable, `memory.answer` accept applying at once).

So the role stays `observer` on every project. C1 is a separate grant, checked next to the ladder, that lets the
write review apply exactly one action of exactly one class. Nothing pending is released; answers stay labels.

## The class C1 (all conditions, decided in code)
A write-review judgement is C1 only if every one holds:
1. relation `contradicts`, supersedes `new`, scope `part`;
2. the action tier after `guards.finalize` (verifier agreed, quotes pin the claim, same kind);
3. both items are kind `fact` (no decision rows: the `decision_record` flag excludes; no lesson, episode,
   session note, experience, doc chunk);
4. `same_class`: same home project, identical `device_scope`, neither item pinned;
5. the newer item's `valid_from` ≥ the older's;
6. a non-empty, verified outdated-span quote;
7. the older item not stale (the assessed version is still the head) and no pending question for the pair;
8. the pair (newer logical, older logical) was never reverted (below);
9. the project's daily C1 budget is not used up.

Measured (private files, fresh-data check of 2026-11-20 and the check of 2026-11-14): the action tier of this class
was correct 17 of 17 on fresh data (v4) and 41 or 42 of 42 before; the bar is precision ≥ .90.

## What C1 applies
Only the `supersedes` link, newer → older, `scope: part`, the quote, **pinned to the older version**
(`dst_version_id`), props `{by: "librarian", auto: "C1", decision: <D-id>, ...}`. No `contradicts` link, no
`version_close`, nothing deleted. Everything else in the same job keeps today's path (questions).

## The switches (all must be on)
1. Process: `HLM_LIBRARIAN_C1=on` in llm.env (default off). Off = today's behaviour exactly.
2. Per project: the latest `librarian` event `{op: set_class, class: C1, on: true|false, decision, project}` for
   that project says on. Recorded by `hlm_ops.sh librarian class set C1 on|off --project P --decision D-NNN`
   (admin). No project-less grant: each project is opted in by name.
3. Every project the proposal touches is granted (lowest over `prop.project_ids`, as D-074).
Daily cap: `HLM_LIBRARIAN_C1_DAILY_MAX` applies per project per UTC day (default 10); beyond it, a question.

## Reversible, and seen
- `hlm_ops.sh librarian class applied [--project P] [--since D] [--json]`: every C1 link with both items' clues
  and titles, the quote and the job event, for the weekly labels (an auto apply writes no question row).
- `hlm_ops.sh librarian class revert <link_id> --reason '…'`: ends the link in one evented step (like the
  explicit-link revert), records the pair as reverted (never auto again: rule 8), and checks demotion.
- **Automatic demotion:** after a revert, if reverted ÷ applied over the project's last 30 days is above .05, the
  same event records `set_class C1 off` for that project (actor: the operator running the revert, reason
  `auto-demotion`). Re-granting needs a new decision.
- Readers: `superseded_by` entries and `memory.raw` links show `applied_by: "librarian (C1)"` for C1 links, so an
  agent can tell an automatic correction from a writer's or operator's.

## Threat model
| # | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | A wrong C1 link hides a still-true statement (query demotes, brief drops `superseded-part` items) | precision bar, part scope only (item never closed), pinned link (a later revision of the older item is not flagged), daily cap, revert, auto-demotion | one wrong link per false positive until the weekly label; brief loses that item |
| T2 | Promotion releases the pending queue or other classes | role stays observer; C1 path is separate and applies one action kind; tests assert observer behaviour unchanged for every other class | none if tests hold |
| T3 | Prompt-shaped item text steers the model into a C1 link | verifier on another model, verified verbatim quote, kind and project constraints, cap; nothing is deleted | a crafted pair in a granted project can produce one part link per day-cap slot |
| T4 | Cross-project or device-scope leak through a link | same home project, identical `device_scope`, lowest grant over recorded project_ids, `policy_blocked` | none known |
| T5 | Grant set by a non-admin or by a guest account | `set_class` requires admin, recorded as an event; per-project only | — |
| T6 | Re-apply loop after a revert | reverted-pair check reads ended links too | — |
| T7 | Gate or deploy script silently allows mutations in observer | observer gate unchanged; the probe project is never granted; a test proves an ungranted project gets no C1 mutation | — |
| T8 | Replay/restore consistency | the link insert and revert are ordinary `link_insert` / `link_supersede` mutations already replayed by db/replay.py | — |

## Severity rubric (for the two reviewers)
- **HIGH:** any path where C1 applies outside the 9 conditions, applies anything other than the pinned part link,
  releases pending questions, acts on an ungranted project, or cannot be reverted; a grant without admin; a
  privacy or cross-project leak. Each HIGH gets a reproducing test.
- **MEDIUM:** a revert or demotion edge that leaves a wrong link in place; an audit gap (an applied link missing
  from `class applied`); a gate that would pass a broken release.
- **LOW:** wording, naming, logs.

## Steps
1. Code + tests in a worktree (this plan as the brief).
2. Two review agents in parallel (correctness/regressions; security/privacy/contracts), on different models; ≤ 2
   rounds.
3. Local gates: lint, unit, integration on an own DB, `make gate-release` on a clone of the retrieval world.
4. Decision D-286 (supersedes D-246 for C1 only), Cemal's OK, deploy, `HLM_LIBRARIAN_C1=on`, grant per project
   with Cemal naming the projects; gates `--no-drill`; watch `class applied` weekly.

## Cemal's choice of projects (2026-11-21)
The first grants go to the four projects the measurement came from; others are added after a week of watching.
Guest accounts' projects are never granted. (The names are in the private review files.)
