Independent Phase-0 final audit of HLMemo (repository: /Users/cemalkurt/Projects/HLMemo, commit on `main`).

HLMemo is a self-hosted long-term memory backend for CLI coding agents, exposed as one MCP server over streamable HTTP. Phase 0 = the LLM-free core. Its contract is docs/decisions/PHASE0-SPEC.md; binding decisions are docs/decisions/DECISIONS.md (D-001..D-032); gates are docs/decisions/VALIDATION-GATES.md. Earlier review rounds and their fixes are in docs/consults/07-*, 08-*, 09-*, 10-* and DECISIONS D-027..D-030. The test suite is green (199 unit/fixture, 99 integration, gate tests); your job is to find what the tests do NOT catch.

RULES
- STATIC REVIEW ONLY. Do not edit any file, do not run pytest, do not start/stop docker, do not run git write commands. You may read files and run read-only commands (`git log`, `git show`, `git diff`, `rg`, `grep`, `sed -n`, `cat`, `docker compose config`).
- Every finding must be concrete and reproducible: file:line, the exact input or sequence that triggers it, the observed vs expected behaviour. Findings without a concrete trigger will be rejected.
- Do not re-report items already closed in D-027..D-030 unless you can show the fix is still defeated (then give the defeating input).
- Prefer depth over breadth: 5 real defects beat 20 speculative ones. False positives are costly.

SCOPE — audit the whole Phase-0 implementation under src/hlmemo/** plus compose.yaml and Dockerfile, with priority on:
1. Authorization and scope: device × project grants, device_scope, pending/revoked devices, admin device 1, cursors, every read path (query, drilldown, raw, card, links).
2. Bi-temporal correctness: backdated corrections, survivors, link supersession, valid_at/known_at filtering, replay rebuild determinism.
3. Idempotency and durability: request_id handling, hash versioning, commit-before-ack (finite vs SSE), worker leasing/fencing/restart.
4. Token-budget guarantee: the metered text equals the wire text; can any path exceed the budget or be forced to E_BUDGET_TOO_SMALL unfairly.
5. Anything that would break or leak when this is deployed on a public VPS behind TLS (the next step, D-032).

OUTPUT — return ONLY this, in English, max ~900 words:

## Findings
A markdown table, most severe first, max 10 rows:
| # | Severity (High/Medium/Low) | Area | file:line | Trigger (exact input/sequence) | Observed vs expected | Suggested fix | Confidence (High/Medium/Low) |

## Checked and found sound
Up to 8 bullets naming specific mechanisms you examined and consider correct (file:line), so the absence of a finding is informative.

## Verdict
One line: GO or NO-GO for declaring Phase 0 DONE, and the single reason.
