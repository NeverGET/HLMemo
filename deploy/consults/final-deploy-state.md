Updated: 2026-09-23 — final deploy implementation and validation completed; no commit.
Goal: requested deploy fixes plus gate evidence; Phase 0 remains blocked on source IP integration.
Resume: /Users/cemalkurt/Projects/HLMemo main. Read deploy/FINAL-GATE-RESULTS.md and RUNBOOK.md.
  Scope only deploy/**, tests/deploy/** and Makefile deploy-*; no src/other worktree edits.
Next: source owner integrates D-039/D-041; rebuild bake-astra and rerun smoke_edge.sh. Current main
  ab9f8f5 logs Caddy172.30.39.3 instead of actual TLS client172.21.0.3; trusted proxy setting absent.
In flight: none. Deploy implementation, bootstrap tests, independent review and cleanup complete.
Done: frontend pinned172.30.39/24 + frontend-only alias; trust env; MCP64MiB/REST64KiB; protocol3
  runner guard and repeated OCI revision checks; observer newline; lint; provider-neutral bootstrap;
  RUNBOOK bootstrap-first and 8GB sizing. Steady limits5.25GiB, max6GiB incl migration.
Evidence: G-D1..G-D7 PASS; D13 PASS; 62 unittest tests OK in93.685s; ruff25 files clean; shellcheck
  clean; stdin fake SSH twice Deployment ready. 5MB body passed Caddy with temp64MiB outer-app cap; old SDK returned413.
  Actual Docker revision label matched HEAD. Bootstrap Ubuntu24 apt install + reruns and actual
  SSH key handover/reload rollback passed; systemctl mocked, active firewall untested. Actual unprivileged systemd PID1 failed mount permissions.
  stack.sh down -v bake-astra completed; no matching containers/volumes remain; bootstrap removed.
  Full evidence deploy/FINAL-GATE-RESULTS.md; private runtime logs /private/tmp/hlmemo-final-*.
Consult: native coarchitect independently confirmed frontend-only alias requirement, sizing,
  protocol check before checkout, revision check before reuse/start, legacy baseline recovery by ID.
  Bootstrap two-phase SSH requires actual key-authenticated deploy session before hardening.
  Uninvolved final reviewer found no new High/deploy-blocking Medium and independently passed9
  bootstrap tests; confirmed source blocker. Final diagnostic review confirmed proxy-only5MB PASS
  with temporary outer cap override; SDK413 is not application acceptance. Its sandbox recovery attempt was not counted PASS.
Pitfalls: ps/Docker need approved-shell execution; UV_CACHE_DIR in /private/tmp. Main source lacks
  D039 proxy parser and has4MiB body cap. No wildcard workaround. No cloud/real secrets/commit.
  No VPS boot, systemd PID1/active UFW/Docker daemon/fail2ban/unattended runtime, public ACME/IPv6/S3.
Checkpoint and consult kept under deploy/consults to respect user's explicit write scope.
