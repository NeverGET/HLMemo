# D-037 write-path consultation — 2026-09-22

Native Astra implementer / coordinating Astra coworker exchange (this session):

- Implementer: authorize every revision and expected-version pin before acquiring request,
  session or logical advisory locks, then reload and authorize again after waiting. This keeps
  hidden targets uniformly E_NOT_FOUND without trusting a stale pre-lock snapshot.
- Coordinator: agreed; each advisory statement may temporarily use the request deadline for
  both lock_timeout and statement_timeout. Middleware supplies the remaining request budget
  in SET LOCAL hlmemo.request_db_timeout_ms; direct callers use Settings. Restore ordinary
  SQL timeout settings after the statement. The enclosing middleware deadline bounds the whole
  sequence of advisory waits.
- Implementer: project_card validates device_scope=all, including a service-side invariant.
  Corrections preserve outside-window link segments; card revisions replace all overlapping
  derived_from edges, including dropped sources. Record link survivor IDs and intervals in
  resolved.link_survivors; replay copies only immutable base-link fields. Record each version
  survivor's last_access_at explicitly (including null) so access-event ordering cannot change
  its copied value. Legacy events without this field keep the old replay fallback.
- Coordinator reviewed the implementation diff: no initial findings. Regression coverage
  includes contended hidden/absent probes, timeout restoration, replay/conflict/session outcomes,
  post-lock scope changes, link split replay, concurrent access replay, and 30 card revisions.

Scope: src/hlmemo write/replay plus tests; no deploy, Makefile, commits or migrations.

## Independent middleware review

The write worker inspected the separately implemented middleware/app/device changes: shielded
MCP cancellation cleanup and commit-before-ack remain intact; REST HlmError rolls back without
connection churn; inactivity resets only for body bytes and retains a total cap; registration
has a 16 KiB pre-lease cap; XFF requires a trusted peer and walks trusted hops right-to-left.
The separate admin pool avoids MCP FIFO contention and the existing exclusive device advisory
gate queues subsequent device readers behind revocation. No blocking finding in these paths.
Boundary: authenticated readiness and all revoke requests share the reserved pool; a flood
specifically targeting those protected endpoints could consume this capacity too. The scoped
regression exercises the requested ordinary MCP flood versus successful revocation.

Validation: dedicated hlm_d037, explicit HLM_MODELS_DIR, the four focused write/replay integration
modules completed with `59 passed in 5.18s`; focused Ruff lint/format are clean. Global neutral
acceptance gates remain the coordinator's responsibility.
