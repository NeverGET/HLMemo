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
