# tests/fixtures — G3 recall fixture

Synthetic "project world" for the Recall@5 gate (`docs/decisions/PHASE0-SPEC.md` §7,
`VALIDATION-GATES.md` G3). Generated, frozen, and hash-checked; **never hand-edited**.

## Files

| File | What |
|---|---|
| `gen_fixture.py` | deterministic generator (stdlib only, Python >= 3.12) |
| `g3/items.jsonl` | one item per line: `logical_key, project, kind, title, body, tags, stability, importance, valid_from, lang` |
| `g3/queries.jsonl` | one query per line: `qid, lang, query, gold_logical_key, identifier_heavy, template_id, gold_template_id` |
| `g3/SHA256SUMS` | digests of both jsonl files (`shasum -a 256 -c g3/SHA256SUMS`) |
| `test_fixture_frozen.py` | freeze gate: regenerates into a temp dir, asserts byte-identical output and SHA match |

## Regenerate

```
python3 tests/fixtures/gen_fixture.py            # writes tests/fixtures/g3/
python3 tests/fixtures/gen_fixture.py --out /tmp/x
pytest tests/fixtures/test_fixture_frozen.py     # freeze gate
```

Seed: `random.Random(20260922)`; UTC timestamps; output is byte-identical across runs and
across CPython 3.12/3.13/3.14 (no set/dict-hash ordering is used for any random choice).

## Counts (current freeze)

- Items: **2400** = 2000 in `fx-main` + 400 decoys in `fx-other`;
  languages tr/de/en = 801/800/799.
- Body length: 647–873 words (mean 759.6); markdown sections + an
  `## Environment` key/value block. Estimated chunks at ~300 words per 400-token chunk:
  ~5066 (`fx-main`) + ~1011 (`fx-other`); with the multilingual-e5
  tokenizer (TR/DE tokenize at roughly 2–3 tokens/word) the real chunk count is closer to
  the spec's ~5 per item (~10,000 + ~2,000).
- Queries: **100** = 34 TR / 33 DE / 33 EN;
  **25** identifier-heavy (>= 3 identifier tokens: service, env key, error code, path;
  9 TR / 8 DE / 8 EN); light queries carry <= 2 identifiers.
- Every query's `template_id` differs from the gold item's `gold_template_id`; every gold is a
  distinct `fx-main` item; every identifier token in a query occurs in the gold title/body.
- Paraphrase ratio (queries that do NOT share >= 50% of the gold title's content words):
  0.94 (gate in the generator: >= 0.60). Achieved via per-language synonym
  tables (`SYN`) — titles use the canonical word, queries only the alternatives — and
  morphological variants: TR suffix table with vowel harmony (-ler/-lar, -de/-da/-te/-ta,
  -nin/-in, -ye/-ya, -den/-dan) applied to identifiers, DE plural/compound/case forms
  (`des svc-x-Dienstes`, `svc-x-Instanzen`, `Konfigurationsschlüssel`, `E1234-Ausfall`),
  EN possessive/inflection (`svc-x's`, `the svc-x service`).

## Item world

- Entities: unique service names in `fx-main` (`svc-qx7`), env keys (`APP_DB_DSN`, 150 shared),
  paths (`src/hlmemo/server/app.py`, 200 shared), error codes (`E4193`, 300 shared), people,
  ISO dates, versions, ports. Each `fx-other` decoy uses a confusable service name (one
  character away from a real `fx-main` service) and the shared env/path/error pools.
  Main services are also cross-referenced as `svc2` in other bodies, so the retriever must rank
  the owning item above mere mentions.
- Templates (topics): arch, bugfix, envconfig, decision, incident, session, perf, deploy;
  kinds drawn per topic from fact, episode, lesson, experience, session_note, doc_chunk.
  `stability` skews stable for fact/lesson/doc_chunk, volatile otherwise; `importance` 1–10.
- Not represented in this JSONL (owned by other gates): `device_scope` rows, project cards,
  backdates/supersessions (G6), cross-language query/gold pairs.

## SHA256 (current freeze)

```
ff795d961d4a4ca9636de0c088f7de80ac7476f5f262cd2bfa0c26c5914b4659  items.jsonl
4aa3945edc41cdba128df35a599b88df54065009d7561f8d00525d4dc74b9dd8  queries.jsonl
```

## Change rule

The fixture is a frozen contract: the G3 threshold (Recall@5 >= 0.90) is tuned against it.
Any change to `gen_fixture.py` that alters the output, to the seed, or to the committed
`g3/*` files requires (1) an entry in `docs/decisions/DECISIONS.md` with the rationale,
(2) regenerating `g3/` in the same commit, and (3) `test_fixture_frozen.py` passing.
