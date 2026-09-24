# E2E production test, 2026-09-24 (R2, project `hlmemo-e2e`)

Raw, public-safe results of the first end-to-end production test: HLMemo's own memory imported as a
TEST into the separate project `hlmemo-e2e` on https://mcp.hlmemo.com (R2 = 6902f91, D-079; the
librarian in the OBSERVER role). The test project is wiped before the final release. All numbers are
in `e2e-results.json`. It holds aggregates only: no corpus-B question text, gold facts or per-question
ids, no tokens and no host addresses. Per-question outputs are private
(`docs/private/realdata-hlmemo/results-e2e/`).

## Method notes
- Import: `hlm import markdown|context|automemory` as the owner device (id 21), with the source set
  from the brief (docs/private excluded; gitleaks on a copy of the 130 source files: 0 findings).
- Load probe: 3 callers, 2 queries/s total, through the import and the first 10 minutes afterwards.
  The read-only reader device was minted with ops (`ci`, `hlmemo-e2e:read`, 8 h expiry).
- Client transport: the stock eval client (`eval/realdata/import_corpus.Mcp`) opens a new TLS
  connection and SSL context for each request, and it re-initializes MCP for each call because the
  server is stateless (no `Mcp-Session-Id`). That is 3 HTTPS round trips and about 950 ms per tool
  call. The e2e clients patched this: keep-alive and one initialize per client. All latencies are
  measured on the client from the owner's Mac and include about 95 ms of WAN round-trip time.
- Corpus-B dev (80 questions, spans pinned at 82200ae). The import is the newer 9ea3cad tree:
  section-level `hlm import` items (363) instead of the baseline's 91 whole-file items. `hlm import`
  titles (`<heading> · <path>`) were mapped to the path for source matching, because run_eval.py
  splits titles only on ` § `. The 5 `git:` spans cannot be reached, because the git log was not
  imported. In 68 of the 72 positive questions, the gold file still contains the answer keys.
- Synthesis: two samples (runs A and B). The operator graded every answered synthesis against the
  gold facts. Only the aggregates are published.
- risk_check: 12 planned actions that should warn, taken from HLMemo footguns in the imported docs,
  plus 8 neutral actions (the texts are in the JSON). Variant A is the project as imported. B adds
  the 3 auto-memory `feedback` files as lessons (what the importer should have done). C adds the 14
  bullets of the footgun file as atomic lessons.
- 0-mutation check: two `hlm export` snapshots of the project, one as of the import end (before any
  librarian job) and one after the backlog drained, compared with each other and with `librarian
  audit`.

## Headline
| step | result |
|---|---|
| import | 363 items (353 markdown + 1 context + 9 auto-memory), 249k tokens, 82 s wall, 0 failed / rejected / skipped; embeddings drained 4.9 min after the import |
| load probe | 1,434 queries, 0 errors; during import + 10 min p50 244 / p95 401 / max 1,904 ms |
| librarian | 368 jobs, 0 failed, drained in 76.6 min (3.4/min under load, 4.9/min idle, 1 job in flight); about $0.0013 per job; 310 proposals, 0 applied |
| retrieval (dev, b=3000) | hit@5 .500 (baseline .625), evidence R@5 .375 top-5 (.451), L2 .542 (.556), stale-claim top-3 .062 (.25); TR hit@5 .278 (.472) |
| synthesis | 44 answered: 36 correct, 3 partial, 5 with a false claim; 0/12 false answers on negatives; LLM path p50 1.9 s |
| risk_check | A 0/12 caught (no lessons were imported); B 1/12; C 4/12; 0/8 false warns; 1/20 timeouts in B and C |
| agent smoke | 4/5 answered in the top-3 previews, 5/5 after one drilldown |
