# Librarian model analysis (research agent, 2026-09-22)
Workload assumption: 60 jobs/day → 1,800/mo → 21.6M input (7.2M cacheable prefix + 14.4M fresh), 2.7M output. "$/mo" = no-cache → with-cache (list price, standard tier).

## Model comparison table
| Model (API id) | $/M in (miss) | $/M in (hit) | $/M out | Context | JSON/tools | Benchmark note | Est. $/mo | Source |
|---|---|---|---|---|---|---|---|---|
| DeepSeek `deepseek-flash` (V4.1 Flash; `deepseek-v4-flash` = 0731 now routed here) | 0.15 off-peak / 0.30 peak | 0.003 / 0.006 | 0.60 / 1.20 | 1M in / 384K out | json_object + tools, strict tools (beta) | AA Index v4.3 = 40; 0731 build = 34.5; #1 AutomationBench (69%); very verbose (89K tok/task) | 4.86 → 3.80 (peak 9.72 → 7.60) | https://api-docs.deepseek.com/quick_start/pricing ; https://artificialanalysis.ai/models/deepseek-v4-1-flash |
| OpenAI `gpt-5.6-luna` | 0.20 | 0.02 | 1.20 | 1.05M / 128K out (>272K in = 2× in) | Structured outputs + tools | AA ≈38 after Sept rescore (vs Haiku 18) | 7.56 → 6.26 (batch: 3.78 → 3.13) | https://developers.openai.com/api/docs/pricing ; https://developers.openai.com/api/docs/models/gpt-5.6-luna |
| OpenAI `gpt-5-nano` | 0.05 | 0.005 | 0.40 | n/a on page | Structured outputs + tools | Weak: AA-Omniscience 25% | 2.16 → 1.84 | https://developers.openai.com/api/docs/pricing |
| Google `gemini-3.1-flash-lite` | 0.25 | 0.025 | 1.50 | 1,048,576 / 65,536 | Structured + tools + caching + batch | AA 26 (v4.1.1) / 16 (v4.3); Omniscience −16 | 9.45 → 7.83 (batch ½) | https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite |
| Google `gemini-3.5-flash-lite` | 0.30 | no caching | 2.50 | 1M | Structured + tools | AA 36 (v4.1.1) | 13.23 (no cache benefit) | https://ai.google.dev/gemini-api/docs/pricing |
| Anthropic `claude-haiku-4.5` | 1.00 | 0.10 (write 1.25) | 5.00 | 200K / 64K | Structured + tools | AA 30 (v4.1.1) / 18 (v4.3) | 35.10 → 28.62 (batch ½) | https://www.anthropic.com/pricing |
| Alibaba `qwen3.8-flash` (Frankfurt) | 0.113 | 0.014 | 0.382 | 1M / 131K | JSON + function calling | Qwen3.8-Flash-Next AA 39.9 (v4.3) | 3.47 → 2.76 | https://www.alibabacloud.com/help/en/model-studio/qwen3-8-flash |
| Z.ai `glm-5.3-flash` | 0.15 | 0.03 | 0.50 | 1M / 128K; thinking cannot be disabled | Structured + tools + caching | AA 42 (v4.3), 3rd-best open-weights | 4.59 → 3.73 | https://docs.z.ai/guides/overview/pricing |
| Mistral `mistral-small-2603` (Small 4) | 0.15 (+10% EU endpoint) | −90% ≈0.015 | 0.60 | 256K | Structured + tools | No AA score found; vendor: MMLU-Pro 78.0 | 4.86 → 3.89 (EU: 5.35 → 4.28) | https://mistral.ai/pricing/api ; https://mistral.ai/news/mistral-small-4 |
| MiniMax `MiniMax-M3` | 0.30 | 0.06 | 1.20 | 1M | tools/structured | AA 44 (v4.1.1) | 9.72 → 7.99 | https://platform.minimax.io/docs/guides/pricing-paygo |

Not in table: Kimi `kimi-k3` $3/$0.30/$15 and Grok 4.6 $2/$6 — both out of cheap tier.

## DeepSeek V4 Flash 0731 deep-dive
- **The 0731 build is retired on the first-party API** (changelog 2026-09-10): `deepseek-v4-flash` is "temporarily routed" to DeepSeek-V4.1-Flash; canonical id is now `deepseek-flash`. 0731 is only available as MIT open weights / third-party hosts. https://api-docs.deepseek.com/updates
- Pricing (V4.1 Flash): hit $0.003 / miss $0.15 / out $0.60 off-peak; 2× during peak = 01:00–04:00 and 06:00–10:00 UTC Mon–Fri. Async jobs can be scheduled off-peak.
- Price history is volatile: 0731 launched at $0.14/$0.28/$0.0028, raised in August (~$0.22/$0.66/$0.007), cut again Sept 10. Budget with headroom.
- Specs: 1M context, 384K max output, `response_format: json_object` (no `json_schema`; strict schema only via tool `strict:true` beta), tool calls, Responses API, Anthropic-format endpoint.
- Thinking on by default (`reasoning_effort` low/high/max); reasoning tokens bill as output. AA measured 250M tokens on the index vs 130M median → real output cost can be 3–10× the 1.5K assumption. Set `thinking: disabled` or `low` for classification jobs.
- Gotchas: thinking mode rejects `tool_choice`; `reasoning_content` must be echoed back on tool turns or API returns 400; temperature ignored in thinking mode.
- Structured-output reliability: independent tests show schema-violation failures under adversarial input, `{"cursor":"null"}` type confusion, hallucinated tool calls. Wrap with validator + retry.
- Rate limits: no RPM/TPM; 2,500 concurrency cap per account.
- Caching: automatic disk prefix cache, 98% discount; fixed 4K system prefix will hit.
- **Privacy (blocking for EU)**: data collected/processed/stored in the PRC; policy allows use to train models; PRC law governs; no retention period stated; no public Art. 28 DPA; no EU region; under EU regulator scrutiny. https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html

## Recommendation
**Primary: `qwen3.8-flash` in Alibaba Model Studio Frankfurt. Fallback: `gpt-5.6-luna` (EU-residency project) or `mistral-small-2603` via `api.eu.mistral.ai`.**
- Cost is a non-issue: every candidate is $2–35/month; decide on JSON reliability + privacy.
- Qwen3.8-Flash: cheapest with cache ($2.76/mo), 1M context, JSON + tools, EU region, SOC 2, "never use your data for model training"; AA ≈40, on par with DeepSeek V4.1 Flash.
- GPT-5.6 Luna: best structured-output tooling (json_schema strict), 1M context, no training by default, 30-day abuse retention, EU data-residency projects + ZDR on approval; batch tier halves cost.
- Mistral Small 4: EU legal entity, 30-day rolling retention, GDPR-native DPA; 256K context enough; no AA score → run own eval.
- DeepSeek only if self-hosting open weights (MIT) or routed via an EU host — first-party API fails GDPR data-transfer requirements.
- GLM-5.3-Flash strongest cheap open model (AA 42) but thinking can't be disabled and training terms unclear; MiniMax uses API data to "improve services" by default.

## Caveats
- DeepSeek Open Platform-specific privacy page 404; relies on general policy + ToS.
- AA scores span index versions (v4.1.1 vs v4.3) — compare only within same version.
- Cache-hit economics assume prefix ≥ provider minimum (Haiku min 4,096 tokens, 5-min TTL).
- Gemini 3.8 Flash ($0.75/$3.75) introductory until 2026-12-31.
- Reasoning-token overhead not modeled; 1.5K output assumption optimistic for thinking models.
