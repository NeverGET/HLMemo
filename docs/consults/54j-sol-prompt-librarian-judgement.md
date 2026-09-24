# Consult 54j — librarian judgement + throughput + promotion stranding (implementer J's position)

You are gpt-6-sol, co-architect and adversarial reviewer of HLMemo. Read-only. Repo: the current
directory (branch of main 132dda6). Read first: docs/decisions/DECISIONS.md D-062, D-067, D-074,
D-076, D-077; docs/consults/50-sol-review-final-r2.md; src/hlmemo/librarian/{roles.py,guards.py,
worker.py,actor.py,candidates.py}, src/hlmemo/librarian/tasks/{write_review.py,apply_batch.py},
src/hlmemo/librarian/prompts/{relate,relate_verify}/v1.md, src/hlmemo/db/librarian_queries.py
(superseded_among), eval/live/run_w2b.py.

The hold-out (D-076) failed: proposal precision strict .42; whole-item closes end still-valid facts
(contradicts+close 2/8); duplicate 0/6; the auto 'action' tier 6/23 vs question tier 15/27; refine
direction errors; doc_chunks excluded from contradiction; approve-all staleness chains. Below is my
design. Critique it hard: what is wrong, unsafe, non-deterministic under replay, or will not move the
hold-out numbers? Answer in ≤ 60 lines, English, numbered per item, each with a concrete fix.

## 1. Promotion stranding (D-077 prerequisite), roles.py:137
`pending_apply_jobs` stops filtering accepted_pending questions by their RECORDED projects in SQL.
For every accepted_pending question with a batch (and no queued/running apply job of that batch):
touched = home ∪ project_ids ∪ action_projects(current rows). A project decision P releases it iff
P ∈ touched AND every touched project is ≥ assistant after the decision; a deployment decision
considers all. Jobs recorded in resolved.jobs (replay as-is). Test = Sol 50's scenario: the action's
item is later widened into C (observer override); deployment promotion does not release it;
promoting C does (then the apply's staleness recheck makes it `superseded`, not stranded).

## 2. Fact-level supersession (relate v2 + relate_verify v2)
relate v2 result adds `refiner` (new|old|none, refines only: the item that adds the detail),
`scope` (whole|part|none, supersessions only: whole = EVERY statement of the replaced item is
outdated) and `replaced_statements` (verbatim quotes, one per statement of the replaced item, only
with scope=whole). relate_verify v2 adds `replaces_all` (bool). A `version_close` is proposed ONLY
if scope=whole AND the replaced item's body was shown untruncated AND every body statement
(split on lines/bullets/sentence ends, ≥ 3 words) is covered by a verified quote (the quote occurs
in the statement and has ≥ 3 words or ≥ 50 % of its words, or the statement occurs in the quote)
AND the verifier says replaces_all. Otherwise scope=part: actions = `contradicts` link + a
`supersedes` link with props {scope:"part", quote:<the superseded span, verbatim>} and NO close;
question tier; `superseded_among` (read side, D-057) ignores supersedes links with props
scope=part so a partially superseded multi-fact item is never hidden. v1 outputs (no scope) are
treated as part → a v1 prompt can never close anything.

## 3. Stricter action tier
tier=action additionally needs (a) verifier agreement — every action-tier judgement gets a
second opinion ("confirm": same_subject ∧ ¬conflict for duplicate/refines; the existing supersede
rule for contradictions), batched with the high-impact pairs of the job; no agreement → question;
(b) both-side quotes that pin the conflict: both verified verbatim, sharing ≥ 1 content term and,
for a contradiction, differing in ≥ 1 content/number term (for dup/refines: sharing ≥ 2 terms);
(c) same kind; plus the D-067 same-class rule (same home, scope, unpinned, no experience).
Duplicate needs near-identical text: token Jaccard ≥ 0.9 on the normalized bodies; else refines
(if one side is decisively more specific) else a plain `relates_to` {relation:"relates"}.

## 4. Refine direction (deterministic)
The more specific item refines the more general one; specificity = its concrete tokens
(numbers/identifiers) are a strict superset of the other's, else its content terms cover ≥ 60 % of
the other's and it has ≥ 2 more; not decisive → the newer (valid_from, then recorded_at) refines
the older; still tied → the model's `refiner`. Each side having concrete tokens the other lacks =
two different values/attributes → the refines is dropped (none). Wrong model direction → flipped.
Counters refine_flipped / refine_dropped in the audit (request.guards), flags on the judgement.

## 5. doc_chunk contradiction
doc_chunk becomes a same-project candidate kind for fact and lesson subjects (never cross-project);
a doc_chunk subject gets the relation review when it carries dated/decision content (an ISO date,
a D-NNN id, decision words in EN/TR/DE, or an explicit evidence valid_from ≠ recorded_at), else
placement only. Pairs involving a doc_chunk may only raise contradictions (dup/refines → none,
counted). Same guards (a multi-statement chunk will practically never pass the whole-cover rule).

## 6. Concurrency (HLM_LIBRARIAN_CONCURRENCY, default 3)
The worker keeps up to N asyncio tasks in flight, leasing ONE job whenever a slot frees (priority
order kept); each job keeps its own connection, lease keeper, fenced done, lineage/precheck
contextvars. Spend guard = the existing atomic DB reservation; lineage ceiling = the DB claim.
New: `_assign_batches` takes pg_advisory_xact_lock(5, project) after the item locks (two jobs of one
project would otherwise both create the "one open batch" → unique violation). A budget pause stops
leasing; in-flight jobs finish or defer. `drain()` honours the same N. The ledger pool grows to
max(4, 2N).

## 7. Approve-all staleness chains (apply_batch)
Order the approved questions: links-only first, then by the earliest close cut (ascending), then id.
A question whose assessed item was closed EARLIER IN THIS BATCH is rebased, not superseded: its
redundant close (same item already closed at an earlier-or-equal cut) is skipped, its links apply
(status applied). External revisions keep G-Q3 semantics (superseded). A real conflict (a planned
supersedes y→x while this plans x→y) → the question becomes superseded AND a re-plan job
(write_review of its still-current subjects, recorded in resolved.jobs) is enqueued.

## 8. Gates
G-LIVE-B gains per-class precision, duplicate precision, refines direction, false-close; the fixture
gets an extension file (multi-fact partial supersession, near-identical duplicates, refines where
the OLDER item is more specific, doc_chunk decisions). Existing gold "duplicate" pairs whose texts
are not near-identical are scored as "relates" (the v2 label for a restated claim) — circular for
the dup/relates split only (deterministic by design), the model still has to raise the pair.
G-P1 re-recorded deliberately with v2 prompts (cassettes + golden).
