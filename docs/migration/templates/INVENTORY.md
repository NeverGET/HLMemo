# Inventory records (template, PLAYBOOK §5)

## Source table (`inventory/SOURCES.md`)
| source | path | files | bytes | date span | type | layer / freeze | importer | secret hits (file:line, rule id) |
|---|---|---|---|---|---|---|---|---|

## One record per document (`inventory/<group>.jsonl`, Full tier)
```json
{"path": "docs/<file>.md", "type": "spec|report|session|status|lesson|decision|prompt|other",
 "lines": 0, "dates": ["YYYY-MM-DD"], "status": "current|history|stale|unclear",
 "key_claims": [{"claim": "<one sentence>", "line": 0}], "superseded_by": ["<path or D-id>"],
 "value": "core|supporting|low", "layer": "original|bundle|post-freeze|none", "secret_hits": ["<rule id>@<line>"]}
```
Records name values only by rule id and line; a summary file per group lists counts, contradictions found, uncommitted
paths and anything the owner has to decide.
