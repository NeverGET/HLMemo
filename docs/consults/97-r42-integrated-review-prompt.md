# Consult 97 — R4.2 integrated release review (D-085 critical: Astra low + Sol xhigh)
Worktree root = branch r4.2. Review `git diff main..r4.2` (main = 7ceefa4 or 130c345; the branch merges two change sets).

Change set A (ROUND 1 of 2 for this set) — memory.ask answer quality (merge f4d416c, from 9e5244f):
- F1 literal check accepts deterministic notation expansions: `a|b|c` alternation inside a token/path, optional brackets `x[y]` (research.py expand_notation / notation_hay / _prose_keep). Numbers, dates, ids never recombined.
- F2 main-sentence repair: one extra writer call ("restate" JOB in prompts/research/v3.md + v3.2.md + schemas) restates the main point using only excerpt literals; re-validated; failure keeps the rest of the answer without the main sentence. `refers_back` drops a sentence opening with an anaphoric connective (Instead/This/It/These/That, TR/DE too) after a dropped sentence. Flag HLM_RESEARCH_MAIN_REPAIR (default on; fingerprinted, absent = on; commit 4a3d520).
- F3 collapse_windows merges overlapping/adjacent windows (prose mode only; claims/cite unchanged so cassettes replay); rerank preview skips frontmatter / CURATOR NOTE / VERIFIED / Date-estimated blocks and a title-repeating heading.
- F4 clip() ends on a line/list boundary near the limit.
Measured (blind, sealed sets): a code-heavy migrated project .63 → .88; another set 42/45 → 41/45 (one borderline split); hold-out 38/50 → 37/50, contradictions 3 → 2, fabricated 1/10 → 0/10; controls show no code-caused regression (temporal 6/13 old vs 6 and 8/13 new on the same DB). Known: F2 restate returned empty twice on one question; the writer sometimes abstains empty by design.

Change set B (INFO only — it already had 2 review rounds; the owner will accept residual risk) — merge 68be664 of fix-writer-unavailable incl. post-round-2 fixes a8ec1b7: settle-once on any post-send exception (corrupt gzip), removal of the shared evidence-key veto after duplicate-key rejection, strict policy validation at startup, policy only from profile files.

Threat model / rubric (written before the review):
- HIGH = the answer path asserts something the excerpts do not support (F1 expansions accepting a wrong literal; F2 restate introducing unsupported content or bypassing validation); a privacy/scope leak through the new prompt/restate/preview path; budget/spend accounting broken by the restate call; the merge silently drops a behaviour of either set.
- MEDIUM = measurable quality regression risk (F3 merged windows hiding provenance — a merged window is cited with its best handle; drill-down may miss part of the quoted text), prompt growth, determinism of cassette replays, release-env fingerprint mistakes.
- LOW = docs/tests.

Questions:
1. F1: construct inputs where an expansion accepts a literal the excerpt does not support (e.g. `v1|v2` vs `v12`, numbers adjacent to `|`, nested brackets, URLs with `|` in query strings). 2. F2: can the restate output smuggle unsupported claims past validation? Budget handling of the extra call. Anaphora drop false positives (a valid sentence starting with "This" dropped). 3. F3: correctness of the merge; provenance of merged windows; preview skip patterns hiding real content. 4. Merge correctness between A and B (research.py, config.py): any lost behaviour. 5. Release safety on prod fda8fa0: prompt hash/version changes, fingerprint/manifest, startup with prod's installed llm.env (no MAIN_REPAIR key), rollback.
Output: `## Verdict` (GO / GO-with-fixes / NO-GO) then numbered findings (severity, file:line, failure scenario, minimal fix). HIGH needs a concrete reproducer. ≤ 80 lines.
