# tests/fixtures/risk — W2d risk-check fixture (G-R1, G-R2, G-R4, G-LIVE-C)

Hand-written, public, pinned by sha256 (`SHA256SUMS`; `_risk_fixtures.py` refuses a changed file:
a fixture edit is a gate change and needs re-recorded cassettes and a new G-LIVE-C run).

| File | What |
|---|---|
| `library.json` | 50 lessons: past HLMemo mistakes paraphrased from the public `docs/decisions` (D-0xx) and `docs/consults` (stdin swallowing D-035/D-059, umask D-045, test-DB truncation D-056, zsh word-split/glob, gitleaks false positives D-011, alembic heads D-026, GIN pending lists D-063, …), plus 4 distractor facts. Placement exercises every scope rule: home project, another granted project, `hlm-global` (granted; policy `librarian: off`), an UNGRANTED project (L50), a `class:work` lesson (L22, invisible to the personal reader) and a `device:<reader>` lesson (L16, never sent to an LLM). |
| `cases.json` | 40 positive tasks (a gold lesson must be among the warnings: catch) and 40 negative tasks (any warning is a false warn): 20 hard near-misses (`near`: keyword overlap, but the task already follows the fix or is another context) and 20 ordinary tasks. EN/TR/DE. `split`: `cal` (calibration) / `test` (held out from calibration), 20+20 each. |

## Calibration of the deterministic constants (`core/risk_service.py`)

`HLM_RISK_CALIBRATE=1 pytest -s tests/integration/test_w2d_risk_calibration.py` grid-searches
`VEC_MAX_DIST × TAU` on the cal split (maximize catch − false-warn subject to catch ≥ 0.70, ties →
higher TAU); `TAU_STRICT` = the smallest threshold ≥ TAU with at most one cal negative whose best
privacy-withheld candidate reaches it. Result (2026-09-24): `VEC_MAX_DIST=0.16`, `TAU=0.045`,
`TAU_STRICT=0.045`; cal catch 0.95 / false-warn 0.50, test catch 1.00 / false-warn 0.55; the
judge's candidate recall@10 is 1.00. The deterministic false warns are the hard near-misses
(20/20; ordinary negatives 1/20): retrieval cannot tell "the task repeats the mistake" from "the
task already applies the fix" — that is the LLM judge's job, and why the deterministic verdict is
only the fallback (`judged:false`, `judge:"retrieval_only"`).

### Independent validation (Sol 41 #6): bench v2 T9 public pack

`tests/integration/test_w2d_risk_t9.py` maps `bench/v2/tasks/t9_risk_check.json` (30 lessons,
18 positive / 7 negative tasks, authored separately for the librarian bench) to lessons and runs
the same deterministic stage. At `VEC_MAX_DIST=0.16`, `TAU=0.045`: **catch 0.83, false-warn 0.71**
(5/7; 5 of T9's 7 negatives are hard near-misses); candidate recall@10 1.00. Same picture as the
fixture (catch high, near-misses fire), so the thresholds stand; the gate there is catch ≥ 0.70.

| VEC_MAX_DIST / TAU | W2d fixture (80) catch / false-warn | T9 (25) catch / false-warn |
|---|---|---|
| 0.16 / 0.045 (chosen) | 0.975 / 0.525 | 0.83 / 0.71 |
| 0.16 / 0.047 | 0.875 / 0.475 | 0.72 / 0.43 |
| 0.16 / 0.048 | 0.775 / 0.375 | 0.61 / 0.43 |
| 0.15 / 0.047 | 0.825 / 0.45 | 0.56 / 0.29 |

0.047 trades ~10 points of catch for ~5–28 points of false-warn and leaves T9 at 0.72 catch (13/18,
at the G-R2 bar); pooled over both sets catch − false-warn is 0.38 at 0.045 vs 0.36 at 0.047. For a
fallback path a missed known mistake costs more than a dismissible warning (D-014), so 0.045 stays.
