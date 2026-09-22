Co-architect round 7: Phase-0 CODE REVIEW. The implementation of docs/decisions/PHASE0-SPEC.md landed in src/hlmemo/** with tests in tests/** (239 passing: G1, G2, G3 Recall@5=0.93, G4 p95=371ms, G5, G6, G7 server-side). Deviations are logged in DECISIONS.md D-026. You have read-only access; run `uv run --frozen pytest tests/unit -q` if useful (do NOT run integration tests; they need docker).

Review for defects that the tests would not catch. Answer in English, max ~900 words, ONLY:
1. SECURITY (max 6): authz bypasses, scope leaks (project_ids[] / device_scope on every read path incl. cursors, raw, link endpoints), token/cursor handling, admin device semantics, SQL injection surface (dynamic SQL in read_queries/write_queries), prompt-injection surface in the preflight block. Each: file:line, why, fix.
2. CORRECTNESS (max 6): bi-temporal edge cases (backdated corrections, survivors), idempotency/replay (payload_sha256, stored result), transaction boundaries (savepoints in MCP handler, worker lease fencing), budget metering vs wire text, RRF/dedupe. Each: file:line, why, fix.
3. OPERABILITY (max 4): compose/Dockerfile (models not baked — api container will fail to embed), worker restart semantics, migrations, logging/redaction of bearer tokens.
4. Top-3 fixes you would do BEFORE calling Phase 0 done, in order, each ≤ 2 hours.
5. GO / NO-GO for "Phase 0 done" once those top-3 are fixed.
