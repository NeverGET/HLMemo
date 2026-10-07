# Truth set format (template, PLAYBOOK §13)

The file `docs/private/migration/<slug>/truthset.jsonl` holds one JSON object per line; it stays private, and its
sha256 goes into `REVIEW.md` before the prod import. 10 questions (Light) or 15–20 (Full).

| field | meaning |
|---|---|
| `id` | `T01` … |
| `question` | asked verbatim through `memory.ask`; the relay never sees anything else |
| `lang` | `en`, `tr`, …; both languages if the project writes in two |
| `category` | `fact`, `temporal`, `temporal-superseded`, `procedure`, `lesson`, `negative` (must abstain), `layer` (crosses a freeze date) |
| `gold` | the answer a grader expects |
| `quotes` | verbatim quotes from the final set with their file, supporting the gold |
| `must_not_state_as_current` | older values that an answer may mention as history but not as current |

Coverage: at least one `temporal-superseded`, at least two `negative`, a `procedure`, a `lesson`, and for layered
legacy memory at least one question on each side of the freeze date and one where a later note replaced an earlier fact.

Versioning: when a content fix after sealing changes a quote, write `truthset.v<n>.jsonl` with the same gold answers and
record both hashes and the reason in `REVIEW.md`.

Example (invented content):
```json
{"id": "T01", "question": "What cache TTL does the API use now, and why was it raised?", "lang": "en", "category": "temporal-superseded", "gold": "300 seconds; at 60 seconds the cache stampeded at peak load.", "quotes": [{"file": "status/api/api-cache-ttl.md", "text": "The API cache TTL is 300 seconds."}], "must_not_state_as_current": ["60 seconds"]}
{"id": "T02", "question": "Which payment provider does the project use for refunds?", "lang": "en", "category": "negative", "gold": "Memory does not say; the project has no payment integration.", "quotes": [], "must_not_state_as_current": []}
```
