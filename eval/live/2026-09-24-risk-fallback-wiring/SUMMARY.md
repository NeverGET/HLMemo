# G-LIVE-C through the production wiring (D-094) 2026-09-24

`memory.risk_check` end to end (`RiskJudge(settings)`: judge_chain, latency policy,
per-profile breakers, Postgres spend guard + ledger) with the `deploy/llm.env.example`
mapping and the primary `openrouter-gpt6-luna` forced unavailable (base URL
http://127.0.0.1:9/v1, connection refused), so every judged verdict comes from
`HLM_FALLBACK_PROFILE__RISK_JUDGE=openrouter-qwen38-27b-fast` (`qwen/qwen3.8-27b`). Fixture
`tests/fixtures/risk` (40 positive / 40 negative), 4 reps, 4 s cap; breakers reset per case.

| judge chain | catch min / mean | false-warn max / mean (per rep) | judged | fallback p50 / p95 ms | provider $ | pass |
|---|---|---|---|---|---|---|
| openrouter-gpt6-luna → openrouter-qwen38-27b-fast | 1.000 / 1.000 | 0.075 / 0.044 (0.025, 0.075, 0.025, 0.050) | 299/320 | 1633.0 / 3237.4 | 0.1753 | PASS |

Ledger (risk_judge rows by profile/outcome):
`{"openrouter-gpt6-luna": {"http_error": 320}, "openrouter-qwen38-27b-fast": {"ok": 299, "timeout": 21}}`. Spend guard accounted $0.3992
(the refused primary attempts are settled at their worst case) of the $2 caps.
