# D-038 completed checkpoint — 2026-09-22

Goal: all six review15/D-038 fixes and requested gates; complete without commit.
Resume: /Users/cemalkurt/Projects/HLMemo, main. Read ../D038-GATE-RESULTS.md for exact
commands, last lines, all 15 new tests and limitations. Only deploy/** and tests/deploy/**
changed by this task. No src or HLMemo-bake/fix-d037 edits. Existing/concurrent docs changes
preserved. Scope excludes docs/consults/.fable writes, so checkpoint/consult stored here.
Next: no authorized work remains; no commit made. Compose changes require staged migration
rather than automatic deploy; existing hosts need deploy-user ownership of /etc/hlmemo.
In flight: none. bake-astra down -v complete; no containers or volumes remain.

Done: immutable per-SHA image adoption/build/reuse with atomic env publication and recovery;
runner from fetched SHA; missing runner fail-fast; PID/heartbeat/overall bounded observer;
individual prerequisites; legacy runner lock/initial-clone compatibility; prior Compose
model equality guard; unique daily backup names + retention tie fix; Ruff debt cleared.
Verification: 47 tests / 80.385s / OK; Ruff All checks passed!, 7 files already formatted;
G-D1..G-D7 pass; real backup/wipe/restore G-D4 and D13 pass; fake SSH stdin children reach
Deployment ready; failed B build followed by restore uses A image and A Alembic. Exact
logs /private/tmp/hlmemo-d038-*.log and /private/tmp/hlmemo-d038-gates/.

Consultation: inherited native Astra coarchitect reviewed immutable-image architecture,
recommended API/worker identity agreement and avoiding existing tag mutation (implemented).
Root caught legacy-runner lock and first-clone dirt hazards; worker implemented compatible
handoff, validated with real isolated Git fixture. A gate/review worker uninvolved in edits
ran real Docker gates and independently reran 15 selected regressions (22.374s / OK), plus
env rename fault injection and N4 no-mutation probe; no blocking finding. Root additionally
caught root-owned /etc/hlmemo blocking atomic replace; cloud-init/RUNBOOK/preflight fixed.
Final root suite includes image-publication failure, tag conflict and tag reuse regressions.

Pitfalls: macOS sandbox prohibits ps, so PID tests run escalated locally; Terraform provider
handshake also needed escalation. No real VPS/SSH daemon/OOM/public DNS/ACME/IPv6/S3/systemd
proof. SIGKILL cannot invoke recovery traps; observer now detects and reports missing status.
Internal rollback restores pre-upgrade dump and can discard later writes. No cloud resources,
real secrets, Git commit/push or application source mutation.
