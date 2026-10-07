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

Run the script with the **HLMemo venv python**: `ask` looks the relay's token up through the hlmemo package.

```bash
W=docs/private/migration/<slug>/blindcheck
PY=.venv/bin/python
$PY tools/migrate/blindcheck/blindcheck.py extract --dir $W --truthset <truthset.jsonl> --sha256 <sealed sha> --project <slug>
$PY tools/migrate/blindcheck/blindcheck.py ask --dir $W --server <https://your-hlm-server> --device <device name>
$PY tools/migrate/blindcheck/blindcheck.py packets --dir $W --truthset <truthset.jsonl> --sha256 <sealed sha>
bash tools/migrate/blindcheck/run_graders.sh $W "${TMPDIR:-/tmp}/hlm-graders-<slug>"
$PY tools/migrate/blindcheck/blindcheck.py score --dir $W         # exit 0 = PASS
```

`ask` exits 1 when no answer is `ok`, when an answer asked in this run came back `no_call`, `relay_mismatch` or
`exception` (the relay is broken, so nothing was measured), or when the spend cap left questions unasked (`--cap`
must be above 0). Look at `answers/` and `ask.log`, fix the cause (server, device, token, cap) and run `ask`
again: saved `ok` answers are kept and saved failures are asked again (`--keep-failed` keeps them as findings).
Every file the tool writes is 0600 (also over an older file) in 0700 directories, and every line it prints or
logs is masked with the migration kit's masking.

## Why the steps are separate (isolation)

| step | reads | never reads |
|---|---|---|
| `extract` | the truth set (it checks the seal) | — |
| `ask` | `questions.jsonl`: qid, project and question only | gold, quotes, the curated set |
| graders | the packets, the instructions and their own reader order | the key, the answers, the truth set |
| `score` | `key.json` and both grade files | — |

**How `ask` works:**
- It calls the `hlm` server at `--server` (production) through a headless `claude -p` relay started with
  `--restricted --strict-mcp-config --mcp-config <file>`: no user settings, hooks or plugins, and `hlm` is the only
  MCP server. The config is a 0600 temp file whose header reads the bearer from the relay's environment
  (`${HLM_DEVICE_TOKEN}`); the token comes from `HLM_DEVICE_TOKEN` or the keychain for `--server`/`--device` and is
  never written to disk or printed. (Without `--mcp-config`, `--restricted` drops the user-scope `hlm` server and
  every question comes back `no_call`.)
- The relay may use only ToolSearch and the memory_ask tool, runs from an empty scratch cwd, and has
  `HLM_CAPTURE=off` and `HLM_BRIEF_DIGEST=off`.
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
