# risk_check: show the lessons the judge drops. Threat model and severity rubric (before review)

Written 2026-10-07, before the change is reviewed. Trigger: the first real test drive (BACKLOG "First real test-drive
findings"). With the judge on, `memory.risk_check` returned `no_matching_evidence` after dropping 10 of 10 candidates.
The dropped set included the lesson that applied, which retrieval had ranked first; with the judge timed out, the same
lesson came back as the top warning.

## The change
An additive field. When the judge runs and drops candidates that passed the deterministic retrieval threshold, the
response lists the best of them as `dropped_by_judge: [{clue, title, why, source_project}]`, where `why` is the judge's
reason. `verdict`, `warnings` and `judged` keep their meaning, so the release-blocking live gate G-LIVE-C (catch rate,
false-warn rate) measures the same thing as before. The protocol (R18), the digest and the skill tell writers to read
`dropped_by_judge`, just as they read `warnings`.

## What is protected
- **Safety of the risk check:** a relevant lesson must reach the writer. A false `no_matching_evidence` is the failure
  this change addresses.
- **The verdict contract:** `verdict ∈ {warn, no_matching_evidence}`, D-014 (never "no risk"), and D-071 (G-LIVE-C
  false-warn ≤ 0.10).
- **Privacy and scope:** the new list must obey the same visibility as `warnings` (the projects the caller can read,
  device scope, the D-083 isolation) and the same budget packing (`omitted`).
- **Cost and latency:** no new LLM call. The judge's existing output is reused.

## Failure modes to look for
1. A dropped candidate that the caller could not see as a warning (another project, a device-scoped item, a closed
   or superseded item) leaks through the new list.
2. The new list is unbounded or pushes `warnings` out of the token budget.
3. `verdict` or `judged` changes for an existing input (G-LIVE-C drift).
4. Retrieval-only mode (judge down or timed out) reports a misleading or empty `dropped_by_judge` instead of omitting
   the field.
5. The judge's free-text reason echoes memory text that the redaction (librarian/redact.py) would have hidden, or
   secret-shaped text.
6. Docs tell writers something the code does not do.

## Severity rubric
- **HIGH:** failure modes 1, 3 or 5 with ordinary input; a relevant dropped lesson still invisible in the
  test-drive repro.
- **MEDIUM:** 2 or 4; missing tests for the listed cases.
- **LOW:** wording and docs.

## Review plan
Astra low and Sol xhigh in parallel, at most 2 rounds, a reproducing test for each HIGH. Before the review: the
test-drive repro on a local copy (the dropped lesson appears in `dropped_by_judge`), and the risk tests pass.
