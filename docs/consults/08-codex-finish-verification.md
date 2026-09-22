# Round 8 — D-027 implementation integration and verification

Date: 2026-09-22. Scope: finish the interrupted fixes from review 07; no contract changes, commit, deployment, or G7 client rerun.

## Co-architect exchanges and independent review

- Native co-architect `independent_review` found C5's new hash was raw but stored `payload.request` remained normalized, contradicting PHASE0-SPEC §1.1. Resolution: preserve verbatim request, hash that exact object, retain validated/defaulted items under `resolved.write.items` for deterministic replay. Both MCP handlers explicitly pass original arguments. Service, replay and MCP regressions cover this.
- The same review found the arbitrary 64-hop survivor provenance cap could reject valid history. Resolution: walk authorized predecessor versions with cycle detection; tests cover an 80-version chain and a cycle.
- Root repaired S1b fixtures by writing with a work device authorized on both projects, with source and appropriate targets shared into MAIN. Personal MAIN reads omit work-only and OTHER-only endpoints. S2 is unchanged. Cursor budgets now include the complete payload item.
- `infra_finish` integrated finite-response buffering, dependency readiness and fenced embedding tests. The reported redundant model-directory override and incorrect Starlette State access were already absent on disk; current code uses `default_model_dir()` and `getattr(app.state, ...)`, verified by readiness tests.
- Independent `final_review`, uninvolved in implementation, inspected read/write and infra changes against PHASE0-SPEC. It found no further read/write blocker, but identified default model baking disabled and missing clean test-database provisioning. Resolution: default bake enabled, host mounts removed, offline o200k cache baked, test-only bootstrap restricted to creating `hlm_verify` if absent.
- Follow-up independent review found those implementation gaps closed. Model/image behavior still requires actual build and offline smoke evidence. Worker progress monitoring and crash/restart behavior remain outside demonstrated coverage.
- Gate d exposed a real C4 performance regression: G3 stayed 0.930, but G4 p95 was 5279.4 ms. Independent read-only EXPLAIN on q012 showed the planner scanning 9,642 authorized chunks across four terms instead of using the trigram GIN. A matches-first MATERIALIZED boundary reduced this query from 5017.543 to 284.022 ms with exactly identical top-20 tuples. Root integrated that boundary, keeping authoritative version authorization before ranking/limit. Initial failed gate run was interrupted during remaining G2 work; full ordered verification restarted.

## Final verification evidence

All normal suites use explicit `HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_verify`; retrieval gates alone use `hlm_retr`. Read-only fixture validation confirmed 11,574 cached embeddings/chunks and no reload required. Alembic uses `phase0@head`.

- a: `All checks passed!`; `90 files already formatted`.
- b: `199 passed in 18.90s`.
- c: `83 passed in 17.37s`.
- d: `8 passed in 148.32s (0:02:28)`; Recall@5 0.930 (TR 0.971, DE/EN 0.909, identifiers 25/25), G4 p95 273.2 ms. G2: 1000 random calls, max used/limit 1.000, zero overflow; expected undersized requests rejected.
- e: `docker compose config -q` silent exit 0; `docker compose build api worker` exit 0, final lines `Image hlmemo:dev Built` twice.
- Rendered Compose assertions pass: DB publishes only 127.0.0.1, worker restart unless-stopped, models bake by default without mounts, test stage targets hlm_verify.
- Post-gate read-only cache check: hlm_retr still has 11,574 embeddings, 11,574 chunks and zero pending jobs.
- Additional O1/O3 evidence: `docker compose --profile test build test` exits 0 (`Image hlmemo:test Built`); runtime with `--network none` and no host mounts reports `Offline runtime: pinned model hashes, ONNX inference, o200k tokenizer: PASS`; test image with explicit safe HLM_TEST_DSN and `--network none --entrypoint pytest ... --version` reports `pytest 9.1.1`.

## D-027 evidence map

| Item | Status | Evidence |
|---|---|---|
| S1 | VERIFIED | test_raw_survivor_does_not_leak_restricted_correction; unauthorized-origin and 80-hop/cycle regressions |
| S1b | VERIFIED | test_payload_item_links_filtered_by_endpoint_authz, including cursor pages; test_drilldown_links_filtered |
| C4 | VERIFIED | test_stale_chunk_scope_cannot_hide_authorized_version (both directions); G3/G4 |
| C2 | VERIFIED | test_rebuild_with_forward_link_reference; test_rebuild_with_cyclic_batch_links |
| C3 | VERIFIED | test_spanning_correction_supersedes_all_overlapping_links; test_rebuild_identical_after_spanning_correction |
| S2 | VERIFIED | test_link_to_hidden_target_uniform_not_found |
| C5 | VERIFIED | test_idempotency_hash_is_verbatim_not_normalized; test_rebuild_preserves_verbatim_request_and_resolved_defaults; test_verbatim_idempotency_over_mcp (write + close) |
| C1 | VERIFIED | test_mcp_commit_failure_rolls_back_before_ack (real DB/MCP); test_response_waits_for_outer_commit (success/error/cancellation) |
| C6 | VERIFIED | test_stale_lease_rolls_back_copied_vectors_then_new_owner_completes (real DB) |
| S4 | VERIFIED (configuration) | Rendered Compose JSON assertion: every db port host_ip == 127.0.0.1; existing container not recreated |
| O1 | VERIFIED | readiness dependency/hash/inference tests, default baked-model build, offline runtime smoke |
| O2 | WEAK | Rendered worker.restart == unless-stopped; actual worker crash/restart and progress monitoring not tested/implemented |
| O3 | VERIFIED | Test image build + pytest 9.1.1 smoke; six test-only DB-bootstrap safety cases |

Remaining limits: no new contract decision required; no full Compose stack boot or G7 client rerun in this task. O2 monitoring and crash/restart evidence remain weak. No API/worker service was started, and existing DB port publication was not redeployed. The complete Compose test suite was not rerun inside the image; host suites and image dependency/bootstrap checks are the evidence above.

Logs for this session: `/private/tmp/hlmemo-{b,c,d}.log`, `/private/tmp/hlmemo-e-build.log`, `/private/tmp/hlmemo-test-build.log`; initial failed performance run `/private/tmp/hlmemo-d-initial-failure.log`. G4 hardware/measurement artifact: `HARDWARE.md`. Local checkpoint: `.fable/tasks/d027-finish-verify.md`.

The first c run exposed a test-fixture error: C6's revision used a later implicit valid_from, correctly producing a survivor and a second job. Giving original and revision the same explicit interval isolates the lease-fencing scenario; c then passed. Global Ruff also exposed pre-existing formatting/lint failures; fixes preserve fixture strings and frozen data, verified by the fixture suite.

No protected configuration or host model files were changed. API/worker services have not been started.
