# G-LIVE-D: both profiles qualify for synthesis; fallback policy (W2e, 2026-09-24)

**Proposed (D-071 procedure): the `openrouter` (deepseek) fallback stays QUALIFIED for the
`synthesis` task** — it passes G-LIVE-D, so `profiles/openrouter.toml` does not list `synthesis`
in `disabled_tasks` (it keeps `risk_judge`, D-071). With the D-066 chain (primary
`openrouter-gpt6-luna`, fallback `openrouter`), a synthesis the fallback produced during a primary
outage is labelled `synthesis.tier:"fallback"` and the wrapper says "(fallback model)" (D-066: the
caller is told). A profile that fails G-LIVE-D is disqualified by adding `"synthesis"` to its
`disabled_tasks`; `librarian.tasks.synthesis.synthesis_chain` then drops it and an outage answers
`synthesis_unavailable` (`synthesis_reason:"unavailable"`), never a weaker model's text.

## Evidence (SUMMARY.md, results.json)

The 62 weak-evidence questions of the held-out `test` split (top RRF < τ_s = 0.0434), production
path with the 6 s cap, each profile alone, 3 reps. Fast path only (a key in one drilldown of the
top-3 clues): 0.435. Bar: synthesis accuracy (a key in the synthesis text) − fast path ≥ +0.03
on every rep.

| profile | synthesis acc min / mean | Δ min / mean | system acc min | weak negatives answered (max, of 8) | calls: outcomes | p50 / p95 ms | cost $ |
|---|---|---|---|---|---|---|---|
| openrouter-gpt6-luna | 0.597 / 0.608 | **+0.161** / +0.172 | 0.694 | 1 | 210 ok | 1170 / 2400 | 0.018 |
| openrouter (deepseek-v4.1-flash) | 0.629 / 0.656 | **+0.194** / +0.221 | 0.710 | 1 | 198 ok, 3 schema-retry ok, 7 schema fail, 3 timeout | 1395 / 2758 | 0.097 |

Both clear the bar by a wide margin. Deepseek answers a little more often (fewer abstentions) and
scores slightly higher, but it is less reliable (2–4 of 62 per rep end as `timeout` or
`schema_fail`, i.e. no synthesis) and 5.5× more expensive per call at the measured prices; the
primary stays `openrouter-gpt6-luna` (D-066). "system acc" = the synthesis when it answered, else
the fast path's drilled top-3.

Spend of the whole W2e live work: dev dry run on 64 cal questions $0.035, cassette recording
(105 calls) $0.054, G-LIVE-D $0.115 — **$0.204 in total**.
