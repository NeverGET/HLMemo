# Embedding bake-off on the G3 fixture (vector-only) — 2026-09-22

**Question:** would a cloud embedding model improve HLMemo retrieval enough to justify replacing the local
`intfloat/multilingual-e5-small` (384-d, ONNX CPU)?

## Method
- Corpus: all 9,642 `fx-main` chunks (2,000 logical items) of the frozen G3 fixture, read-only from DB `hlm_retr`.
- Queries: the 100 G3 gold queries (34 TR / 33 DE / 33 EN; 25 identifier-heavy).
- Identical pipeline for every model: L2-normalise, exact cosine top-50 (numpy), dedupe by `logical_id`
  (best chunk wins), gold = originating logical id. Recall@5, Recall@10, MRR@10.
- Baseline e5-small uses the **stored** DB chunk vectors + local `Embedder.embed_query` (with its `query:` prefix).
- Cloud models via OpenRouter `/api/v1/embeddings`, batches of 96, 4 concurrent requests. Raw text unless noted.
  - `gemini-embedding-2@1536+prefix`: queries `task: search result | query: <q>`, chunks `title: none | text: <chunk>`.
  - `qwen3-embedding-4b+instruct`: query-side `Instruct: ...\nQuery:` (corpus reused from the raw run — its cost row is not new spend).
- Query latency: 20 sequential single-query calls from this Mac (network round trip to OpenRouter included; local = in-process).
- Corpus embed time is wall time at concurrency 4 (gemini-embedding-2 first run hit Vertex 429s and was retried).
- "non-ident vs e5": paired per-query comparison on the 75 paraphrase-heavy (non-identifier) queries at R@5;
  +win = model hits where e5 misses, -loss = the reverse; exact two-sided McNemar p.

**Production context (not recomputed):** full G3 hybrid (lexical + trigram + e5 vector, RRF) Recall@5 = **0.930**.

## Results
| model | dims | R@5 | R@10 | MRR@10 | TR/DE/EN R@5 | ident R@5 (n=25) | non-ident R@5 (n=75) | non-ident vs e5 (+win/-loss, McNemar p) | corpus cost $ | corpus embed s | query lat ms med/p90 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| local/multilingual-e5-small | 384 | 0.80 | 0.86 | 0.695 | 0.68/0.85/0.88 | 0.92 | 0.76 | +0/-0, p=1.000 | 0.000 | n/a (stored) | 4/5 |
| openai/text-embedding-3-small | 1536 | 0.78 | 0.89 | 0.560 | 0.59/0.82/0.94 | 0.84 | 0.76 | +8/-8, p=1.000 | 0.077 | 68 | 523/527 |
| openai/text-embedding-3-large | 3072 | 0.73 | 0.79 | 0.569 | 0.76/0.64/0.79 | 0.80 | 0.71 | +9/-13, p=0.523 | 0.500 | 84 | 664/781 |
| google/gemini-embedding-001 | 3072 | 0.93 | 0.96 | 0.837 | 0.94/0.94/0.91 | 1.00 | 0.91 | +15/-4, p=0.019 | 0.549 | 147 | 1063/1201 |
| baai/bge-m3 | 1024 | 0.87 | 0.92 | 0.846 | 0.91/0.79/0.91 | 1.00 | 0.83 | +10/-5, p=0.302 | 0.035 | 130 | 683/1741 |
| mistralai/mistral-embed-2312 | 1024 | 0.87 | 0.89 | 0.774 | 0.74/0.94/0.94 | 1.00 | 0.83 | +10/-5, p=0.302 | 0.497 | 62 | 276/527 |
| google/gemini-embedding-2@1536+prefix | 1536 | 0.92 | 0.94 | 0.865 | 0.91/0.97/0.88 | 1.00 | 0.89 | +12/-2, p=0.013 | 0.743 | 59 | 193/243 |
| google/gemini-embedding-2@1536 | 1536 | 0.85 | 0.91 | 0.781 | 0.79/0.91/0.85 | 0.96 | 0.81 | +9/-5, p=0.424 | 0.732 | 56 | 205/272 |
| voyageai/voyage-4 | 1024 | 0.79 | 0.84 | 0.630 | 0.82/0.79/0.76 | 0.96 | 0.73 | +8/-10, p=0.815 | 0.231 | 41 | 243/312 |
| perplexity/pplx-embed-v1-4b@1024 | 1024 | 0.90 | 0.92 | 0.871 | 0.94/0.88/0.88 | 1.00 | 0.87 | +11/-3, p=0.057 | 0.116 | 52 | 182/299 |
| qwen/qwen3-embedding-4b@1024 | 1024 | 0.56 | 0.63 | 0.422 | 0.68/0.70/0.30 | 0.68 | 0.52 | +7/-25, p=0.002 | 0.077 | 71 | 256/305 |
| qwen/qwen3-embedding-4b@1024+instruct | 1024 | 0.63 | 0.71 | 0.497 | 0.68/0.73/0.48 | 0.92 | 0.53 | +4/-21, p=0.001 | 0.077 | 71 | 252/302 |

Total OpenRouter spend: **≈ $3.56** (cap $5).

## Interpretation
- Only three cloud models beat e5-small on the non-identifier (paraphrase) subset by a meaningful margin:
  gemini-embedding-001 (0.91 vs 0.76, +15/-4, p=0.02), gemini-embedding-2 **with task prefixes** (0.89, +12/-2, p=0.01)
  and pplx-embed-v1-4b (0.87, +11/-3, p=0.06). bge-m3 and mistral-embed gain ~7 pts but are not significant at n=75;
  OpenAI te3-small/large, voyage-4 and qwen3-embedding-4b are no better or worse than e5-small.
- Prefixes matter: gemini-embedding-2 goes 0.85 → 0.92 overall (non-ident 0.81 → 0.89) with task prefixes. The biggest gain
  is Turkish (e5 TR R@5 = 0.68; gemini-001 0.94, pplx 0.94, bge-m3 0.91). Identifier-heavy queries are already fine vectorially.
- The best vector-only numbers (0.90–0.93) only **equal** today's hybrid (0.930). Whether a cloud vector lifts the hybrid
  above 0.93 is unmeasured — that is the next experiment (swap the vector leg, keep lexical+trigram+RRF). Costs: re-embedding
  the corpus is cheap ($0.04–0.75 per 10k chunks) but query latency goes from ~4 ms local to ~180–1100 ms per query, plus
  a network dependency and memory content leaving the host. A self-hostable candidate worth testing locally is bge-m3 (open weights, 0.87).

## Reproduce
```
.venv/bin/python bench/embeddings/run_embed_bench.py --cap 5 --models <ids>   # cached in cache/, reruns are free
.venv/bin/python bench/embeddings/make_report.py
```
