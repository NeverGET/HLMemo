## Verdict (FIX-NEEDED)

Do not merge yet. Atomicity/replay, D-083 authorization, historical-kind protection, and concurrent-update serialization appear sound, but accepted supersedes can corrupt valid-time history and some updates cannot be compensated correctly. Validation: 34 unit tests passed; one tokenizer test was blocked by the read-only temp environment. Integration tests were inspected, not run.

## Findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/core/write_updates.py:367-377`; `src/hlmemo/librarian/actor.py:239-294` | With current segments `[Jan,Jun)` and `[Jun,∞)`, a carrier dated March makes `_v3_cut` accept March because it lies in any segment. Supersede then destroys `[Mar,Jun)` history that the quoted head never represented. | Select the cut against the targeted open head, falling back to `now` as revise does. Add multi-segment live/replay coverage. |
| HIGH | `src/hlmemo/core/write_updates.py:387-425`; `src/hlmemo/librarian/reversal.py:355-392` | If the carrier also declares the same ordinary `supersedes` link, the update omits its tagged link mutation. Revert reopens the target but leaves the whole-supersession link live; a historical link-only update becomes non-revertible. | Reject the duplicate as a per-update conflict, or adopt/tag the single resolved link so compensation owns it. |
| MEDIUM | `src/hlmemo/core/write_updates.py:201-202,287-292` | A finite middle correction has the greatest version ID, while the currently valid open survivor has a lower ID. A query’s valid clue gets `version_conflict`; the greatest clue gets `not_current`. | Select and stale-check the version valid at the write clock, rather than blindly using `max(version_id)`. |
| MEDIUM | `src/hlmemo/core/write_updates.py:422-425`; `src/hlmemo/db/write_queries.py:229-233` | A prior edge `[July,∞)` merely overlaps a backdated update starting March, so insertion is suppressed while the target closes in March, leaving no supersession link for `[Mar,Jul)`. | Require full interval and scope coverage; otherwise reject independently or split/supersede the existing edge with recorded compensation. |

## Safe-to-enable

No — the wrong-segment cut can alter historical validity, and the link ownership defects make successful updates incompletely reversible.