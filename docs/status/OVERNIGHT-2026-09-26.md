# Overnight 2026-09-26: research librarian (D-130 → D-149)

## Outcome
The research librarian (`memory.ask`) is built on the server: branch wf-memory-ask @ 9bceb59, not merged, not released.

It improved a lot under a strict judge, but the D-130 Production-Ready gate is **not** reached yet. Per the owner's rule the release does not self-certify, so nothing was released tonight. Production still runs R3, whose memory holds the migrated HLMemo knowledge (552 items).

## What was built
- **Memory Map per project:** a path tree plus item and section handles (`vN`, `vN.M`). The L2 summaries are the "summarising" layer. They are written by an async `map_summary` task, cached and debounced.
- **`memory.ask` (read-only, caller's capabilities):**
  - Loop: plan → parallel queries and drill → answer as claims with 1–3 verbatim quotes each → completeness plus repair (copy-through, no-attribution). A refine step runs only on abstain.
  - Output: {answer, confidence, claims, primary ≤3, related ≤5, meta}.
  - Limits: caps per call and per question ($0.01, 100k tokens, 25 s).
- **Migration:** 0009_memory_map (create-only, rebuildable).

## Measured (34-question dev subset; strict judge deepseek-v4-pro; frozen key facts)
| Stage | Correct | Faithful | Recall | Abstain | p95 | $/q |
|---|---|---|---|---|---|---|
| Old synthesis (W2e) | .25 | – | .52 | 1.00 | 2 s | .0004 |
| Map loop V3 | .47 | .46 | .90 | 1.00 | 9 s | .002 |
| V8 (specificity) | .62 | .78 | .85 | .88 | 20 s | .004 |
| V10 (copy-through, no-attribution) | .62 | .85 | .87 | 1.00 | 28 s | .004 |
| **memory.ask (server)** | **.58** | **.87** | **.81** | **1.00** | **21 s** | **.004** |
| **Gate (D-130)** | ≥.80 | ≥.95 | ≥.85 | ≥.90 | ≤20 s | ≤.01 |

## Key findings
- **D-140, the judge is valid:** it agrees .967 with independent adjudication on correct.
- **D-140, quote/value checks dropped TRUE claims:** caused by Turkish suffixes, markup and reformatting. Fixed in the product and in the scorer (D-141).
- **D-142, the misses are compression:** in 9 of 10, the key fact was in the evidence but the answer generalised it away. A specificity prompt, copy-through and atomic claims were added.
- **D-145, the doc-level drill hurts** when it displaces ranked chunks.
- **Safety:** without per-call caps, a prompt can run away to 65k tokens, so caps were added (D-143).

## Last probes (D-150)
- A stronger answer model (luna-pro) does NOT raise correctness (.615, the same misses); it doubles the cost and pushes p95 to 38 s.
- The judge is accurate: correct .577 is exact. The true faithful is about .91, against the judge's .874.
- The next lever is SLOT-FILLING: decompose the question into the exact slots it asks for, extract each verbatim with its quote, then compose.

## Slot-filling probe (D-151)
- V11 slot-filling: p95 17.4 s (the first variant under 20 s) and $.0029/q, but correct fell to .385. Answers are too narrow: one slot is made for two-fact questions.
- The next combination to try is slots per fact plus a completeness pass.

## Open
- **Review 79 round 1 (FIX-NEEDED):** T1 scope isolation with exclude, T2 redaction before JSON, T4 per-attempt budget, T5 default OFF plus an R4 manifest, T6 negation-safe quote match. The fixes are in progress; round 2 is verification only.
- **Levers for the gate:**
  - (a) a stronger answer model, if latency permits;
  - (b) structured evidence → facts extraction before composing;
  - (c) judge false-negative re-audit (running);
  - (d) retrieval recall.

## Spend
About $5.4 of the $6 dev cap overnight. Prod spend is separate and within its 1/2/10 caps.
