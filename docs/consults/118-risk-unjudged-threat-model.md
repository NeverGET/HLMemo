# risk_check `unjudged` and frontmatter-free previews: threat model and severity rubric (before review)

Written 2026-10-08, before the change is reviewed. Trigger: the second real test drive (BACKLOG "Second test-drive
findings"). A short task text ranked the applicable lessons in the top 10 candidates but below `TAU` (0.045). The
judged call warned on the right lesson; a call whose judge timed out answered `no_matching_evidence` with 0 warnings
and nothing else to read. Reproduced on production with `mode: deterministic` (0 warnings) and `mode: auto` (the
judge warned on the right lesson). D-257's `dropped_by_judge` lists only candidates above `TAU` and only on judged
results, so it does not cover this case.

## The change
1. **`unjudged` (additive).** When `judged` is false (any reason: judge disabled, not requested, unavailable, timeout,
   guard, budget, no_detach, all privacy-withheld), the response lists the best candidates it did not warn on as
   `unjudged: [{clue, title, why, source_project}]` and `unjudged_omitted`. Best `det_score` first, retrieval order
   breaking ties, at most 3; candidates with `det_score` 0 (only a distant vector match) are left out. `why` is a fixed
   sentence (kind, lists, score, threshold, reason); the title passes the same redaction as `dropped_by_judge`
   (`_safe_title`). Packed after the warnings with the D-257 packer (the field names are parameters now). After a judge
   call (`called`), the entries go through the same D-062 re-check as warnings. Judged results carry no `unjudged`.
2. **Previews skip a leading YAML frontmatter block of chunk 0** (`core/retrieval.py`, `preview_text`). Imported
   files keep their frontmatter in the body; previews often showed `---\ntitle: …`. Drilldown and stored data are
   unchanged.
3. The risk tool description, the protocol (R17 pause line, R18), the digest line, the skills and the `hlm` preflight
   line name `unjudged`. tools/list stays at 2998/3000 tokens (G-SURF).

## What is protected
- **Safety of the risk check:** a relevant lesson must reach the writer; a false-empty `no_matching_evidence` is the
  failure this change addresses.
- **The verdict contract:** `verdict ∈ {warn, no_matching_evidence}`, D-014, and G-LIVE-C (catch and false-warn rates
  measure `verdict`/`warnings`, which must not change for any input).
- **Visibility:** the new list must obey the same visibility as `warnings`: the caller's readable projects, device
  scope, the D-083 isolation policy, current items only, and the D-062 re-check after time passed outside a transaction.
- **Secrets:** no secret-shaped text in a title reaches the response unredacted.
- **Budget:** `warnings`, `omitted` and the budget errors are unchanged for every input; `budget.used` stays exact and
  ≤ the limit.
- **Preview correctness:** stripping must only remove a real frontmatter block, never body text, and must stay linear.

## Known, accepted for this release
- Privacy-withheld candidates (`device:*`, `policy.librarian=off`, co-owned by an ungranted project) can appear in
  `unjudged`: they are items the caller may read; the privacy gate governs what is sent to the LLM, and such items
  already appear as deterministic warnings at `TAU_STRICT`. (`dropped_by_judge` excludes them because "the judge did not
  warn" would be false for an item the judge never saw.)
- The Redactor's known gaps (BACKLOG: JSON-style and quoted multi-word values) apply to these titles as to
  `dropped_by_judge`.
- When nothing relevant exists, `unjudged` still lists up to 3 weak candidates; each says it is below the threshold
  and unjudged. That is noise by design, accepted for the safety goal.

## Severity rubric
- **HIGH:** an `unjudged` entry from an item the caller could not get as a warning (another project, another device
  scope, an isolated project, a closed or superseded item, one made invisible during the judge call); an unredacted
  secret shape in the response; any change of `verdict`, `warnings`, `omitted`, `judged` or a budget error for an
  existing input; `budget.used` above the limit; a preview that drops body text that is not frontmatter, or a regex
  with super-linear backtracking on chunk-sized input.
- **MEDIUM:** a wrong or misleading count (`unjudged_omitted`), an empty `unjudged: []` list, an entry duplicated in
  `warnings`, the preflight line misreporting the result, docs that contradict the code.
- **LOW:** wording, comments, test gaps without a reachable failure.
