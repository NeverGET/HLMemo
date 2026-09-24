# G-LIVE-C: the deepseek fallback fails; fallback policy (W2d, 2026-09-24)

**Adopted (orchestrator ruling on Sol review 41): the `openrouter` (deepseek) profile is NOT
qualified for the risk judge.** `profiles/openrouter.toml` lists `disabled_tasks = ["risk_judge"]`,
so `librarian.risk_judge.judge_chain` drops it. During a primary outage, when the librarian is
disabled, or when no qualified profile is configured, `memory.risk_check` returns the deterministic
verdict with `judged:false`, `judge:"retrieval_only"` and `reason` naming the cause (e.g.
`unavailable`, `timeout`, `disabled`); the `hlm` wrapper labels it "RETRIEVAL ONLY, not judged by
the librarian LLM (<reason>)". A profile qualifies again only by passing G-LIVE-C
(`HLM_GLIVE_C=1 HLM_GLIVE_C_PROFILES=<name> pytest tests/integration/test_w2d_glive_c.py`) and
removing the entry in the same change.

Note: `openrouter` is also the code default profile (`config.DEFAULT_PROFILE`) and the one the
owner's local `hlm.toml` names; with it as the PRIMARY, risk_check is retrieval-only everywhere.
D-066's switch of the primary to `openrouter-gpt6-luna` enables the judge.

## Evidence

G-LIVE-C (SUMMARY.md, 3 reps, production path, 4 s cap): `openrouter-gpt6-luna` PASS (catch 1.000
min, false-warn 0.050 max); `openrouter` (deepseek-v4.1-flash) FAIL (catch 1.000, false-warn 0.250
max / 0.242 mean). Deepseek's false warns are the hard near-misses (the task already applies the
fix: N01 N04 N05 N09 N10 N12 N13 N14 N19 …), the weakness bench v2 T9 showed (D-066: 43%).

One experiment (1 rep, not a gate run): a stricter, generic abstention instruction as a profile
override lowered deepseek to false-warn 0.150 (cal 0.20, test 0.10), catch 1.000 — still failing:

```toml
[prompt_overrides.risk_judge]
system_append = "  Before listing a lesson, name to yourself the exact risky step it warns about and look for that same step in the task. If the task does the safe variant the lesson's fix recommends, or does not contain the step at all, the lesson does not apply. A warning that does not apply is as wrong as a missed one."
```

For comparison, the retrieval-only verdict (G-R2) has catch 0.975 / false-warn 0.525 on the W2d
fixture and catch 0.83 / false-warn 0.71 on the independent T9 pack (hard near-misses fire).

Next: qualify a non-OpenAI fallback for the risk judge in the Phase 5 bench (D-066 candidates:
deepseek-v4-pro-0813, glm-5.3, kimi-k2.6, qwen3.8-27b).
