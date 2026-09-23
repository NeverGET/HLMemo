# G-LIVE-C: the deepseek fallback fails; policy proposal (W2d, 2026-09-24)

G-LIVE-C (SUMMARY.md): `openrouter-gpt6-luna` PASS (catch 1.000 min, false-warn 0.050 max);
`openrouter` (deepseek-v4.1-flash) FAIL (catch 1.000, false-warn 0.250 max / 0.242 mean). The bar
is not lowered. Deepseek's false warns are the hard near-misses (task already applies the fix:
N01 N04 N05 N09 N10 N12 N13 N14 N19 …), the same weakness bench v2 T9 showed (D-066: 43%).

One experiment (1 rep, not a gate run): a stricter, generic abstention instruction as a profile
override (D-017: model quirks live in the profile) lowered deepseek to false-warn 0.150 (cal 0.20,
test 0.10), catch 1.000, $0.023. Still above 0.10.

```toml
[prompt_overrides.risk_judge]
system_append = "  Before listing a lesson, name to yourself the exact risky step it warns about and look for that same step in the task. If the task does the safe variant the lesson's fix recommends, or does not contain the step at all, the lesson does not apply. A warning that does not apply is as wrong as a missed one."
```

For comparison on the same fixture, the deterministic fallback (`judged:false`, G-R2) has catch
0.975 and false-warn 0.525 (hard near-misses 20/20, ordinary 1/20).

Options (owner/orchestrator decision; the mechanism for each is in place):

1. **Exclude the fallback from the risk judge**: `disabled_tasks = ["risk_judge"]` in
   `profiles/openrouter.toml`. During a primary outage risk_check answers deterministically
   (`judged:false`, `judge:"unavailable"`). Honest, but on this fixture strictly worse than option 2
   on both metrics.
2. **Keep deepseek as a labelled fallback judge (recommended for now)**: its answers carry
   `judge:"ok_fallback"`, so the calling agent is told the verdict came from the fallback tier
   (D-066 outage behaviour) and can treat warnings as advisory; add the override above.
3. **Qualify a better non-OpenAI fallback for this task** in the Phase 5 bench (D-066 candidates:
   deepseek-v4-pro-0813, glm-5.3, kimi-k2.6, qwen3.8-27b) and switch when one passes G-LIVE-C.
