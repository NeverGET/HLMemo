# Embedding model landscape (2026-09-22)
Owner asked for the newest Google embedding model and alternatives; recall bake-off results will be in bench/embeddings/RESULTS.md.

## Google embedding models
| Model (Gemini API id = Vertex id) | Status / date | Max in | Dims / MRL | Task types | Price /1M (std / batch) | MMTEB (task mean) |
|---|---|---|---|---|---|---|
| **gemini-embedding-2** | GA 2026-04-22 (preview `gemini-embedding-2-preview` 2026-03-10) | 8,192 tok | 3072 default, 128–3072. Recommended sizes 768/1536/3072, re-normalized automatically | Given as a **prompt prefix**, e.g. `task: search result \| query: …`, `task: code retrieval \| query: …`, docs as `title: … \| text: …`. Also takes image, audio, video and PDF | $0.20 / $0.10; there is a free tier | **69.9**; MTEB Code **84.0** |
| gemini-embedding-001 | GA (June 2025) | 2,048 tok | 3072, MRL 128–3072. Manual re-normalize when truncated | `task_type` param: RETRIEVAL_QUERY/DOCUMENT, CODE_RETRIEVAL_QUERY, QA, FACT_VERIFICATION, etc. | $0.15 | 68.4; Code 76.0 |
| text-embedding-005 / text-multilingual-embedding-002 | Legacy (Vertex) | 2,048 | 768 | task_type | ~$0.15 | n/a |

Sources: ai.google.dev/gemini-api/docs/embeddings, …/changelog, …/pricing, deepmind.google/models/gemini/embedding, arxiv.org/html/2605.27295v1, embeddingcost.com/google (legacy row).
- **"Google V2" = `gemini-embedding-2`.** It supports 100+ languages. Its vector space is incompatible with 001, so switching means re-embedding everything.
- **Data terms:** on the paid tier, data is not used to improve Google's products. On the free tier it is. Google keeps data for 55 days for abuse monitoring either way (ai.google.dev/gemini-api/docs/usage-policies).

## Other candidates
| id | Dims (MRL) | Max tok | $/1M | MMTEB | Code |
|---|---|---|---|---|---|
| voyage-4-large / voyage-4 / voyage-4-lite (Jan 2026) | 1024; also 256/512/2048 | 32k | 0.12 / 0.06 / 0.02, batch −33%, 200M free tokens | not published | voyage-code-4 (Aug 2026) at $0.12 |
| pplx-embed-v1-4b / 0.6b (Feb 2026, open weights) | 2560 / 1024, MRL | 32k | 0.03 / 0.004 (OpenRouter) | retrieval subset nDCG 69.66 | "code search" claimed |
| Qwen3-Embedding 8B / 4B / 0.6B | 4096 / 2560 / 1024, MRL 32+, instruction-aware | 32k | 0.01 / 0.02 (OpenRouter) | 70.58 / 69.45 / 64.33 | yes |
| jina-embeddings-v5-text-small / nano (Feb 2026) | 1024 / 768, MRL | 32k / 8k | self-host | 67.7 | – |
| OpenAI text-embedding-3-large / small | 3072 / 1536, MRL | 8,191 | 0.13 / 0.02 | 58.93 (large) | weak. No successor as of 2026-09-20 |
| Cohere embed-v4 | 256–1536 | 128k | 0.12 | n/a | – |
| mistral-embed / codestral-embed-2505 | 1024 / 1536 | 8k | 0.10 / 0.15 | n/a | codestral is code-specialised |
| BGE-M3 | 1024 | 8k | 0.01 | 59.56 | – |

Sources: docs.voyageai.com/docs/pricing, openrouter.ai/voyageai/voyage-4, perplexity.ai/hub/blog/pplx-embed-…, huggingface.co/Qwen/Qwen3-Embedding-8B, huggingface.co/jinaai/jina-embeddings-v5-text-small, beri.net/…-2026 (OpenAI successor), buildmvpfast.com (Cohere figures, secondary source).

## OpenRouter availability (5 calls each from this Mac, short TR/DE/EN + identifier text)
| id | HTTP | dims (native → with `dimensions:768`) | median ms |
|---|---|---|---|
| google/gemini-embedding-2 | 200 | 3072 → 768 | **412** |
| google/gemini-embedding-2-preview | 200 | 3072 → 768 | 517 |
| google/gemini-embedding-001 | 200 | 3072 → 768 | 1202 |
| voyageai/voyage-4 / -4-large / -4-lite / voyage-code-4 | 200 | 1024 → 400 error (768 is not a valid size for Voyage) | 355 / 312 / 281 / 288 |
| perplexity/pplx-embed-v1-4b | 200 | 2560 → 768 | 270 |
| qwen/qwen3-embedding-8b (Nebius) | 200 | 4096 → 768 | 3320 |
| qwen/qwen3-embedding-4b (DeepInfra) | 200 | 2560 → 768 | 481 |
| openai/text-embedding-3-large / -small | 200 | 3072 / 1536 → 768 | 745 / 525 |
| mistralai/mistral-embed-2312 / codestral-embed-2505 | 200 | 1024 / 1536 → 422 error (no `dimensions`) | 315 / 338 |
| baai/bge-m3 | 200 | 1024 → 768 | 409 |
| intfloat/multilingual-e5-large | 200 | 1024 (`dimensions` ignored) | 2297 |
| nvidia/nemotron-3-embed-1b:free | 200 | 2048 | 197 |
| cohere/embed-v4.0, jinaai/jina-embeddings-v4, snowflake arctic-l-v2.0, nomic v2-moe, voyageai/voyage-3.5 | 400 "does not exist" | – | – |

Through OpenRouter, gemini-embedding-2 returned one vector per input. The Gemini API docs describe merging multiple inputs into one embedding, but that did not happen here.

## Recommendation
- **Primary: `google/gemini-embedding-2` through OpenRouter, stored at 1536 dims in a pgvector `vector` column with an HNSW index.** It has the best published multilingual score (69.9) and code score (84.0) of the hosted APIs I looked at. It can read 8k tokens, compared with 512 for your current e5. It was the fastest Google model in my tests (about 0.4 s). If storage or RAM gets tight, 768 dims loses only about 0.2 points, measured on 001.
- **Task prefixes: yes, but put them in the provider profile, not in code (D-017).** Queries get `task: search result | query: …`, and `task: code retrieval | query: …` when the query is about code. Memories get `title: <slot or none> | text: …`. Save the prefix version in the embedding metadata so you can tell later which vectors were made with which setup.
- **Fallback: `voyageai/voyage-4` at 1024 dims.** It goes through the same OpenAI-compatible endpoint and costs $0.06/1M. It was fast (about 355 ms) and gave the strongest separation between right and wrong answers in the mini test. voyage-code-4 is the variant for code-heavy memories. If you want an open-weight fallback you could later host yourself, use `qwen/qwen3-embedding-4b` at 1024 dims (MMTEB 69.45).
- **Cost is negligible.** Memory text runs to a few million tokens at most, which is under $1 at $0.20/1M. Use Google's paid tier (no training on your data) or OpenRouter with data-retention restrictions switched on.
- **Store the model id and dims next to every vector.** Both switches in the table above (Gemini 2 to Voyage, e5 to Gemini) need a full re-embed, so you need to know which model made each vector.

## Caveats
- **The mini test can't tell the models apart.** It was 8 documents and 6 queries, and every model got all 6 right. Pick with the `bench/` harness on real HLMemo memories.
- **Latency figures are weak.** They come from 5 calls each, one run, from this Mac. Qwen3-8B and e5-large were slow, and that depends on which host OpenRouter routed to.
- **Some scores aren't comparable.** MMTEB figures come from different reporters. Voyage and Cohere publish no MMTEB score. Perplexity's figure is for the retrieval subset only.
- **Some prices aren't from the vendor.** The Cohere row and the legacy Google rows come from third-party sites.
- **Data terms go through OpenRouter's routing too.** When calling through OpenRouter, check its data-policy setting as well as Google's terms.

Files are in `/Users/cemalkurt/Projects/HLMemo/bench/embeddings/research/`:
- `NOTES-2026-09-22.md`
- `openrouter-probe-2026-09-22.jsonl`
- `mini-sanity-2026-09-22.txt`

Test calls cost well under $0.01.