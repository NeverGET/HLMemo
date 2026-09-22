# Validation gates (deterministic, must pass locally in docker compose before any VPS deploy)
Status: PROPOSED 2026-09-22 (merged Claude consults/00 + codex consults/01). Not yet implemented.

| Gate | Predicate | Tooling |
|---|---|---|
| G1 boot | `docker compose up` → `memory.health` OK within 60 s; restart preserves all acknowledged events | pytest + compose |
| G2 budget | For 1,000 random (query, budget∈[500,8000]) pairs, serialized response tokens ≤ budget (pinned tokenizer); undersized budgets rejected with a clear error | pytest |
| G3 recall | Frozen 100-query TR/EN/DE/identifier fixture over 10,000 chunks: mean Recall@5 ≥ 0.90 | fixture in tests/fixtures |
| G4 latency | Warm p95 ≤ 500 ms for `memory.query` with 3 concurrent callers, incl. query embedding, on recorded hardware | locust/k6 or pytest-benchmark |
| G5 isolation | Unauthorized project reads always fail; 1,000 randomized cross-project probes return 0 leaks | pytest |
| G6 durability | Duplicate request_id → single effect; concurrent revisions → explicit conflict; crash/restart, replay, backdated correction, backup/restore all preserve expected temporal answers (valid_at / known_at) | pytest + pg_dump/restore |
| G7 clients | claude-code, codex, agy (pinned versions) each complete write → query → drilldown → raw against compose | scripted smoke per CLI |
| G8 secrets | gitleaks clean on tree; `.env` untracked | pre-commit + CI |
