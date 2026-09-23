# bench — librarian model benchmark

Deterministic harness that evaluates candidate "librarian" LLMs (via OpenRouter,
OpenAI-compatible chat API) on the four background jobs HLMemo needs:

| task | fixture | output schema | score |
|---|---|---|---|
| T1 placement | `tasks/t1_placement.json` (10) | `{layer, topic_id, importance, stability}` | exact layer+topic_id AND \|importance diff\| <= 2 |
| T2 contradiction | `tasks/t2_contradiction.json` (10) | `{contradicts, supersedes, reason}` | exact contradicts+supersedes |
| T3 summarization | `tasks/t3_summarization.json` (5) | `{summary <=120w, clue_ids[3]}` | 0.7*Jaccard(clue_ids) + 0.3*length ok |
| T4 risk_check | `tasks/t4_risk_check.json` (8) | `{warn, matched_lesson_ids, message}` | 0.5*warn exact + 0.5*Jaccard(ids) |
| T5 JSON strictness | all calls | — | count of unparseable (after ```json fence strip) or schema-violating responses; those score 0 |

Fixtures are mixed English / Turkish / German.

## Setup

```bash
cd bench
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
# OPENROUTER_API_KEY must be in ../.env (never committed)
```

## Resolve model ids + pricing

```bash
.venv/bin/python discover_models.py     # writes models.json from GET /api/v1/models
```

`models.json` maps short aliases (`deepseek-v4-flash`, `gemini-flash`, ...) to exact
OpenRouter ids, USD/M-token prices, and whether `response_format` is supported.

## Run

```bash
.venv/bin/python run.py --models deepseek-v4-flash,gemini-flash --runs 1
.venv/bin/python run.py --models deepseek-v4-flash,gemini-flash-lite,gpt-5.6-luna,qwen3.8-flash --runs 3
.venv/bin/python run.py --models openai/gpt-5-nano --tasks placement,risk_check --limit 3   # raw id, subset, smoke
```

Options: `--runs N` repetitions per case, `--tasks` subset, `--limit N` first N cases per task,
`--reasoning low|off|default` (OpenRouter `reasoning` param; default `low`; auto-dropped on HTTP 400),
`--max-spend USD` abort guard (default 0.50), `--dry-run` (no API calls).

Every call uses: the same fixed system prompt (byte-identical across tasks/models so provider
prompt caching applies), `temperature=0`, `seed=42`, `response_format={"type":"json_object"}` when the
catalogue says the model supports it (auto-fallback to plain "JSON only" on 400), and
`usage: {include: true}` so cost comes from OpenRouter's response; if absent, it is computed from
`models.json` list pricing (`cost_source` says which).

## Output

`results/<timestamp>.json` — raw per-call records (latency ms, prompt/completion/reasoning tokens,
cost, parse/schema status, fail reason, parsed object, score detail) plus the summary.

`results/<timestamp>.md` — table: model | T1 | T2 | T3 | T4 | JSON-fail rate | avg latency |
total cost USD | est. monthly USD (60 jobs/day x 30 d x 12K in + 1.5K out tokens, list price,
no cache discount) | whether the low-reasoning request was honored (reasoning token count).

## Robustness / merging (added after the first full run)

- HTTP 429/5xx/transport errors are retried (429: 1s/2s/4s/8s backoff, 5 tries). If a call still fails it is
  recorded as `infra_error`: excluded from task scores and from the JSON-fail rate, counted in its own column.
- `--parallel N` evaluates N models concurrently (calls within a model stay sequential, so latency is per-call).
- SIGTERM/SIGINT writes `results/<ts>-partial.{json,md}` from the calls completed so far; `results/checkpoint.json`
  is refreshed every 25 calls and deleted on normal completion.
- `--reuse results/<prev>.json [--rerun model_a,model_b]` copies the calls of models already present in a previous
  raw file and runs only the missing (or `--rerun`) ones, then writes one merged results file. Use it to re-run a
  single rate-limited model without paying for the others again.
- Columns: `variance` = |run1 - run2| per case averaged over T1-T4 (needs `--runs 2`); `monthly list` vs
  `monthly cached` (observed OpenRouter cache-hit ratio billed at the model's cache-read price);
  `reasoning param` says whether `--reasoning low|off` was honored (reasoning tokens reported by usage).

## bench v2 (harder librarian suite, T5-T12)

v1 saturates (4-5 models at 98-100%). `--suite v2` runs eight harder families built around what the
librarian does in Phase 2-3: supersession choice (T5), contradiction vs compatible relations (T6),
Turkish→English query rewrite (T7), long consolidation with hallucination penalty (T8), risk_check over a
30-lesson library with silent cases (T9), abstain on unanswerable questions (T10), prompt-injection
resistance (T11) and strict long nested JSON (T12). Design, label rules, scoring, gold protocol and
anti-contamination rules: `v2/DESIGN.md`.

```bash
.venv/bin/python run.py --suite v2 --models openai/gpt-6-luna --runs 3 --max-spend 2
.venv/bin/python run.py --suite v2 --models m1,m2 --tasks T5,T9 --no-private          # subset, public pack only
.venv/bin/python run.py --suite v2 --models m1 --pack v2/tasks/t12_extract_review.json  # one pack
python3 v2/check_packs.py        # lint + sealed hold-out exclusion + privacy scan of all packs
python3 v2/gen_t12.py            # regenerate the T12 pack (deterministic)
```

Without `--pack`, v2 loads `v2/tasks/*.json` plus the private pack `../docs/private/bench-v2/*.json` when it
exists (owner machine only; gitignored). The v2 report (`results/<ts>-v2.md`) has, per model: macro mean,
per-family and per-tier means, public vs private, min across reps, rep std-dev, per-case rep difference,
JSON fails, latency, real cost and cost per correct answer (score >= 0.8), plus gate metrics
(T6 false-supersede rate, T9 catch / false-warn rate, T10 false-answer rate, T11 complied count,
T7 identifier hit rate, T8 coverage and hallucinated tokens). v1 behaviour is unchanged.
