# Consult 66 — CRITICAL dual review (D-085): librarian v3 (wf-librarian-v3 @ 0757c4b)

Your cwd is a clean export of 0757c4b (no .git, no secrets). `V3.patch` is the diff from main c52291c to 0757c4b, excluding cassettes, eval/live and the large G-P1 golden JSON. Spec: read the rows D-089, D-100 and D-101 in docs/decisions/DECISIONS.md, plus D-074, D-083, D-086, D-087 and D-095 (contracts that v3 must keep). The design consult is docs/consults/59-*. An earlier routine review is docs/consults/65-*. Read-only.

## What v3 adds (all default OFF except instrumentation)
1. **Instrumentation (always on):** a per-pair audit (candidate source/rank/score, primary raw output, guards before/after, verifier, terminal outcome, hashes), stored in the event's audit request with no migration. Export: `python -m hlmemo.ops librarian export`.
2. **`HLM_LIBRARIAN_V3_TEMPORAL`:**
   - recorded_at and provenance dates never decide direction.
   - Evidenced valid_from (|valid_from − recorded_at| > 5 min, treated as date-only) may decide direction, subject to the verifier (D-100).
   - A quoted claim-bound date may veto.
   - When evidenced dates conflict with the model's direction, the supersession is dropped and a contradiction question goes to the owner (D-101).
   - With equal or unknown time, direction is accepted only with a quoted explicit replacement AND a blind verifier's agreement.
   - relate/verify prompt v3 has no chronology hint.
3. **`HLM_LIBRARIAN_V3_CLOSE`:**
   - A verified whole-scope supersede yields a CLOSE proposal, applied only via owner approval in the assistant role. The observer never mutates.
   - Close time = the trusted date if it lies inside validity, else the approved "now" (the applying event's T at the linearization point).
   - Close and link are atomic and version-checked. Reversal is a compensating event: `librarian revert-close`; the question becomes `rejected` with `answer.reverted`.
4. **Candidate expansion:** a section quota (`HLM_LIBRARIAN_SECTION_TOP`, same source doc or decision id), `HLM_LIBRARIAN_DOC_EPISODE`, and configurable SAME_TOP/DOC_TOP. Existing candidates are never displaced.

## Focus (priority order)
1. **Data integrity of close and reversal:**
   - Can a close be applied without an owner answer, in the observer role, after the TTL (D-095), across a policy change (D-083), or on a stale version?
   - Is close+link atomic under concurrency, and is the lock order the same as J's (item locks → policy → staleness → batch/rule locks → final TTL check → event id)?
   - Does replay reproduce closes and reversals exactly? Can a reversal resurrect the wrong version or corrupt validity intervals (overlaps or gaps)?
   - Is a close time before valid_from or in the future possible?
2. **Privacy and scope:**
   - Does instrumentation persist or export secrets, device-scoped text or cross-project content?
   - Is the export access-controlled like other ops commands?
3. **Correctness of the temporal rules:**
   - Check the D-100 5-minute evidenced rule, day precision and the verifier's independence (blind, order-independent A/B, no flip).
   - Can clock skew or a client-asserted valid_from be abused to force a supersession direction?
4. **Flags off:** is behaviour byte-identical to main apart from the instrumentation? Does the instrumentation change replay determinism, event sizes, or latency on the write path?
5. **D-086 event policy:** does the new `privacy_denied` outcome follow it, and do budget/timeout pairs appear only in terminal failure events?

## Output contract (≤ 40 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Findings`: a table of severity | file:line | trigger (concrete scenario) | fix. Real defects only.
- `## Safe-to-enable`: one line per flag (TEMPORAL, CLOSE, expansion), yes/no plus the reason.
