# Blind check of a migrated project (TEMPLATE §7, D-216)

After a migration's prod import and the operator's queue check, a different agent answers the sealed truth-set
questions through production `memory.ask`. Two isolated graders score the answers against the sealed gold.

**The bar (D-216):**
- at least 0.80 of the answerable questions are correct;
- 0 answers state a superseded value as current;
- every negative question abstains.

On a grader split, the stricter grade counts. A failure is a finding, not a retry.

## Steps

Keep the work dir private (in the HLMemo repo: `docs/private/migration/<slug>/blindcheck/`).

```bash
W=docs/private/migration/<slug>/blindcheck
python3 tools/migrate/blindcheck/blindcheck.py extract --dir $W --truthset <truthset.jsonl> --sha256 <sealed sha> --project <slug>
python3 tools/migrate/blindcheck/blindcheck.py ask --dir $W            # code-saved memory.ask via a claude -p relay
python3 tools/migrate/blindcheck/blindcheck.py packets --dir $W --truthset <truthset.jsonl> --sha256 <sealed sha>
bash tools/migrate/blindcheck/run_graders.sh $W "${TMPDIR:-/tmp}/hlm-graders-<slug>"
python3 tools/migrate/blindcheck/blindcheck.py score --dir $W         # exit 0 = PASS
```

## Why the steps are separate (isolation)

| step | reads | never reads |
|---|---|---|
| `extract` | the truth set (it checks the seal) | — |
| `ask` | `questions.jsonl`: qid, project and question only | gold, quotes, the curated set |
| graders | the packets, the instructions and their own reader order | the key, the answers, the truth set |
| `score` | `key.json` and both grade files | — |

**How `ask` works:**
- It runs the user's configured `hlm` MCP server (production) through a headless `claude -p` relay. The relay may use
  only ToolSearch and the memory_ask tool, runs from an empty scratch cwd, and has `HLM_CAPTURE=off`.
- The raw tool result is saved **by code** from the stream-json output, never retyped by a model. A hand-retyping agent
  was stopped by an API safeguard once (D-216).
- The relay's tool input must equal the question verbatim.
- The server-side spend stops at `--cap`.

## Truth-set rows

```json
{"id": "T01", "question": "...", "gold": "...", "category": "fact", "lang": "en",
 "quotes": [{"file": "status/api/api-cache-ttl.md", "text": "verbatim quote"}],
 "must_not_state_as_current": ["the older value"]}
```

- Write 10–20 questions.
- Include at least one superseded value (`temporal-superseded`, with `must_not_state_as_current`) and at least one
  `negative`.
- Mix the languages the agents will ask in.
- Any category other than `negative` is answerable (`fact`, `temporal`, `procedure`, `lesson`, `layer`…).
- Seal the file (`sha256sum`) before the prod import and put the hash in the review package. A truth set that has to
  change after sealing gets a new version and a note; the gold answers stay as sealed.
