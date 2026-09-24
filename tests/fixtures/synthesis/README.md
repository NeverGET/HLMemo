# tests/fixtures/synthesis — W2e synthesis fixture (τ_s calibration, CI replay, G-LIVE-D)

Hand-written, public, pinned by sha256 (`SHA256SUMS`; `_synthesis_fixtures.py` refuses a changed
file: a fixture edit is a gate change and needs a new calibration, re-recorded cassettes and a new
G-LIVE-D run).

## Corpus

HLMemo's own public `docs/` at the pinned commit **`ad7ccc2`** (main at the W2e fork; `git archive`,
`docs/private` is untracked and refused defensively), imported into project `syn-docs` through the
`hlm import markdown` code path (`--section-chars 8000`, the G-I4 granularity): **316 items / 926
chunks** (decision rows, consults, research incl. the Turkish deep-research report, bake-off
reviews, status, usage). Undated items get the import time as `valid_from` (D-072); the fixture pins
them to 2026-09-23T00:00Z after the import so that synthesis prompts (whose excerpts carry a date)
and therefore the cassette keys are stable across runs.

## Questions (`questions.json`)

227 rows: **217 answerable** + **10 negatives** (no answer anywhere in the corpus). The answerable
questions were written by the W2e implementer from 260 chunks sampled at random (fixed seeds) from
the pinned corpus: one question per usable chunk, phrased the way an agent would ask (paraphrased,
not copied, so part of them are weak for the fast path), EN 216 / TR 7 / DE 4, including
cross-lingual cases (English questions over the Turkish report and Turkish consults). `keys` are
verbatim answer strings (any one answers; matching = NFKC + casefold + collapsed whitespace, as in
`eval/realdata/run_eval.py`); `file` is the source. `split` = `cal` when `sha256(id) mod 10 < 4`
(96 rows), else `test` (131, held out: τ_s and the prompt were tuned on `cal` only). Not the
corpus-B hold-out set (never opened).

## τ_s calibration (`core/synthesis_service.TAU_S`)

`HLM_W2E_CALIBRATE=1 pytest -s tests/integration/test_w2e_synthesis_calibration.py`: for every
answerable question at budget 3000 (the preflight default) — the fast path's top-hit RRF score,
whether the fast path answers it (`fast3`: a key in one `memory.drilldown` of the top-3 clues) and
whether the synthesis input holds the answer (`oracle10`: a key in the top-10 hit chunks, an upper
bound for synthesis). τ_s = the threshold that best separates the questions the fast path misses
from those it answers on the **cal** split (max Youden J = P(weak | miss) − P(weak | answered),
midpoints between observed scores, ties → the lower τ). Result (2026-09-24): **τ_s = 0.0434**.

| split | n | weak (< τ_s) | Youden J | fast3 on weak / strong | oracle10 on weak | fast misses flagged |
|---|---|---|---|---|---|---|
| cal | 92 | 43 (0.467) | 0.420 | 0.581 / 0.898 | 0.767 | 18/23 |
| **test (held out)** | 125 | 62 (0.496) | 0.268 | 0.435 / 0.698 | 0.726 | 35/54 |
| all | 217 | 105 (0.484) | 0.317 | 0.495 / 0.786 | 0.743 | 53/77 |

Weak evidence is where the fast path fails (0.435 vs 0.698 on the held-out split) and where the
top-10 chunks still hold the answer for about 3 of 4 questions: the room synthesis can use. On the
held-out split J drops from 0.42 to 0.27 (the separation is real but noisy at this n); weak share
stays near one half, so synthesis runs for about half of the preflight questions.

## Gates that use it

* `tests/integration/test_w2e_synthesis.py` (CI): the 105 weak questions (both splits) replayed from
  `tests/cassettes/w2e/synthesis.jsonl` (primary profile, recorded 2026-09-24 for $0.054:
  `HLM_W2E_RECORD=1 HLM_W2E_ENV_FILE=<.env>`): 100% of the answers cite only returned hits, carry
  their markers, stay ≤ 400 tokens and within `budget.used`.
* `tests/integration/test_w2e_glive_d.py` (G-LIVE-D, live, opt-in `HLM_GLIVE_D=1`): the weak
  questions of the held-out split, both profiles, 3 reps; results in `eval/live/<date>-synthesis/`.
  2026-09-24 (62 questions, fast path 0.435): `openrouter-gpt6-luna` synthesis 0.597 min / 0.608
  mean (Δ +0.161 min), `openrouter` (deepseek) 0.629 / 0.656 (Δ +0.194 min): both PASS
  (`eval/live/2026-09-24-synthesis/FALLBACK-POLICY.md`).
