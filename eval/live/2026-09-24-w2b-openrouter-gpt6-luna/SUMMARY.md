# G-LIVE-B 2026-09-24 — profile `openrouter-gpt6-luna` (`openai/gpt-6-luna`)

Verdict: **PASS** · reps 3 · calls 228 · JSON-fail 0 (0.0%) · infra errors 0 · p50/p95 3165/5030 ms · cost $0.03184989

| metric | worst over reps | mean | threshold |
|---|---|---|---|
| placement | 0.984 | 0.984 | ≥ 0.90 |
| contradiction exact | 0.989 | 0.993 | ≥ 0.90 |
| false supersede | 0.000 | 0.000 | ≤ 0.02 |

Per class (rep 0): contra_new 1.00 (n=13), contra_none 1.00 (n=3), contra_old 1.00 (n=3), duplicate 0.90 (n=10), near_miss 1.00 (n=77), none 1.00 (n=66), refines 1.00 (n=7)

Fixtures: tests/fixtures/w2b/*.json (sha256 pinned in eval/live/run_w2b.py). Outputs are redacted; raw provider responses are never stored.
