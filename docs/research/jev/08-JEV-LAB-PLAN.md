# Jev-Lab: make the system work WITH Jev (owner direction, 2026-09-28)

Owner: "The aim is not to put Jev in place of the LLM but to run it WITH the LLM. We must understand Jev well, synthesize
well and calibrate well. Set up a Jev-appropriate test rack following Jev's calibration guides, iterate to calibrate and
optimize Jev — especially where LLMs struggle. Not direct integration: make the system able to work with Jev so Jev
becomes part of the system; change the rules of the game to fit Jev."

Principle (from 02b: games are harness-driven): the model stays as is; the SYSTEM around it is shaped for it —
decomposed narrow questions, minimal relevant state, dates/numbers resolved in code, calibrated confidence, cascades to
the LLM on low confidence.

## Target decisions = where LLMs measurably struggled in HLMemo (D-192…D-195)
| Task | Decision (typed) | LLM pain (measured) | Labels available |
|---|---|---|---|
| A fact-presence (judge) | answer × fact → present / partial / absent; answer × gold → contradicts? | judge v1 under-scores correct ~.2, over-flags contradiction ~4× | ~300+ fact-level blind reader verdicts (verify, verifyO, final/base adjudication, writer grades) |
| B supersession | (newer, older) on the same subject → supersedes (whole/part) / refines / unrelated | automatic pairwise backfill precision .69; "older excerpt wins" | 123 curated (122 correct) + 40 auto (reader-classified) + 12 oracle pairs (+ negatives to label) |
| C relevance (rerank) | question × candidate → useful? / score | hub docs beat specific answers; LLM rerank 2.9 s | T8: 50 q × 30 candidates, gold-labelled |
| D support (validator, later) | sentence × excerpts → supported? | literal guard false positives | validator units + readings (few) |

## Phases
1. **Playbook ($0):** Jev's official guides + community practice → 09-JEV-PLAYBOOK.md (rules of the game: instructions/criteria
   writing, state design, primitive choice, decomposition, confidence & calibration, thresholds, dates/numbers in code,
   multilingual handling, known failure modes and mitigations, templates).
2. **Data ($0):** labelled sets for A, B, C with train/dev/test split BY QUESTION/SUBJECT (no leakage), label-quality notes;
   raw in docs/private/jev-lab/data/ (private memory content).
3. **Rack ($0):** task registry; versioned question designs; calibration fitted on train only; thresholds on dev only; test
   read once at the end; cascade simulation (Jev → LLM on low confidence); metrics (accuracy/F1/AUC, ECE, coverage at target
   precision, latency, $); iteration log; hard budget guard.
4. **Iterations (~$1–2):** 3–5 rounds per task: one hypothesis → one design change → measure; keep a log of what moved.
5. **Synthesis ($0):** "Jev-shaped HLMemo": which decisions go to Jev, which to the LLM, which to code; cascade thresholds;
   provider-agnostic interface (D-017: a "decision" provider type with fallback).

Working rules: ≤ 3 workstreams; timebox per phase; every claim measured; test split untouched until the end.
HLMemo stays paused; the local DB container may be started read-only-ish when item bodies are needed (owner: restart on resume).
