# D-035 completed checkpoint — 2026-09-22

Goal: implement review12/D-035 deploy fixes without commit/cloud/real secrets. Complete.
Root: /Users/cemalkurt/Projects/HLMemo, main. Scope only deploy/**, tests/deploy/**;
Makefile deploy targets unchanged. Initial docs/consults/13-codex-prompt-d035.md untouched; concurrent work tracked it and added
untracked docs/consults/14-codex-prompt-d037.md, also untouched. Final HEAD observed 5c14485.
Scope forbids .fable/docs edits, so checkpoint/consult evidence is stored here.
Memory: project-memory skill read docs/status/STATUS.md; deploy truth is D-035 + review12 + live files.

Done: uploaded/detached runner, EOF child input, atomic final status/cleanup; internal API+Caddy
checkpoint before public HTTPS; new-layout pinned rollback; success-only markers/prune;
restore migration; empty dotenv; canonical external backup directories; bad ssh drop-in cleanup;
IPv6 exact host remediation documented. 32 deploy tests pass; separate stdin regression and fake
SSH end-to-end reach Deployment ready. G-D1..G-D7 pass; actual Caddy local ready healthy; D13 passes.
Evidence and 14 new test names: ../GATE-RESULTS.md. Runtime logs /private/tmp/hlmemo-d035/.
Only bake-astra bound 127.0.0.1:18080/18443 used. down -v complete; zero containers/volumes remain.

Co-architect consultation (native inherited-session agent): recommended file upload+setsid/nohup,
entry EXIT handler, atomic status, API+Caddy loopback validation before external-only failure,
image baseline guard, markers after internal cutover, entire-client-process-group kill test,
exclusive-lock stale secret sweep. Implemented; original scope forbids docs/consults recording.
Independent reviewer reran four tests: Ran 4 tests in 10.484s / OK. No blocker found. Reported two
missing explicit EOF redirects and stale comment; corrected. Docker official Engine27 and port
publishing docs verified IPv6 daemon settings. Root additionally fixed observer log creation race,
subshell cleanup/status ownership, and D13 macOS canonical-path comparison.

Next: user/neutral reviewer can inspect changes; no commit requested or made. No task work remains.
Limits: real VPS SSH/cloud-init, public ACME/DNS/IPv6, S3 and systemd timer unverified. SIGKILL/power
loss cannot run cleanup; next locked deploy removes stale secret config. Internal rollback still
restores snapshot and loses later writes; external-only failure leaves new writes untouched.
