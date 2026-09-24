# gpt-5.6-sol vs gpt-6-sol (codex, reasoning xhigh) — blind review bake-off, 2026-09-24
Task: adversarial merge review of W2e @ e2f30d8 (diff vs ad7ccc2) in a clean export (no .git, consults only up to 43). Neutral prompt (no hints). The answer key was written before reading the outputs (6 known defects K1–K6 from Sol 51, fixed in f64b65a).
| Key | gpt-5.6-sol | gpt-6-sol |
|---|---|---|
| K1 post-call filter keeps a sentence citing a now-unreadable item (critical) | 1 | 0 |
| K2 co-owned grants + token generation | 2 | 1 |
| K3 citation validator presence-only | 0 | 0 |
| K4 packing drops a cited hit | 0 | 0 |
| K5 the 6 s cap does not bound cleanup/recheck | 1 | 1 |
| K6 self-authored calibration / weak scoring | 2 | 2 |
| **Total /12** | **6** | **4** |
| Wall time | 477 s | 315 s |
Extra valid findings: 5.6-sol found that llm.env is not mounted into the api (a real prod blocker, fixed independently in R2) and that the 6 s cap defeats fallback given the 1/2/4/8 s retry backoff (open). 6-sol: client timeout = server cap; a negatives split filter; p95 excluding failures. No clear false positives from either.
Decision: gpt-5.6-sol becomes the coworker/reviewer model (D-084). Caveat: n = 1 task; re-check periodically.

## Addendum: gpt-6-astra at reasoning LOW (same task, prompt, tree and key)
| Key | gpt-5.6-sol xhigh | gpt-6-sol xhigh | gpt-6-astra low |
|---|---|---|---|
| K1 | 1 | 0 | 2 (mechanism exact, verified via render()) |
| K2 | 2 | 1 | 1 |
| K3 | 0 | 0 | 0 |
| K4 | 0 | 0 | 2 |
| K5 | 1 | 1 | 1 (the fallback defeated by retry backoff) |
| K6 | 2 | 2 | 2 |
| **Total /12** | 6 | 4 | **8** |
| Wall time | 477 s | 315 s | **166 s** |
| Tokens used | 224,955 | 139,945 | **66,702** |
astra's per-token price is ~5× sol's (OpenRouter list $10/$50 vs $2/$10), so its effective cost is in the same band as sol xhigh/max, with higher quality and ~3× the speed. It missed the llm.env→api mount and the token-generation pin (both found by 5.6-sol). The blind spots differ, so the union of astra-low and 5.6-sol covers 10/12.

## Addendum 2: nvidia/nemotron-3-ultra-550b-a55b:free (OpenRouter free tier), same task, tree and key (2026-09-24)
Score 0–1/12 over 4 runs. Mode A (codex agentic, wire_api=responses, custom permission profile): 0 and 1 (half of K2), 18–22 min per run, 2.2–4.2M input tokens, many NVIDIA 503s. Mode B (a single 784k-token prompt): 0 and 0, 51–166 s, ~300 reasoning tokens even at effort high. Every run said MERGE-WITH-FIXES on a known DO-NOT-MERGE change, and runs made 0–7 claims that the code contradicts. Only trivial valid extras. Verdict: not useful as a second reviewer (D-092). Details: NEMOTRON-SCORING.md.
