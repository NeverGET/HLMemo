# D-034 implementation consultation and independent review — 2026-09-22

Scope: six adjudicated deployment defects. Native Fable workers inherited the session model;
one independent read-only reviewer challenged the implementation. Records live under deploy/
to respect the user's explicit file boundary (no docs/ edits, no commit).

Design exchange: deployment snapshots must never enter calendar rotation. Chosen implementation
uses SHA/UTC/unique names and explicit keep-N pruning (default5), recording previous-ref and
previous-dump before downtime. pg_dump runs live. Optional deploy upload failure warns. fd flock
covers backup/restore, with deploy holding the same operation lock throughout downtime/recovery.

Recovery review: merely restarting old tags after a migration failure is unsafe. Capture rendered
configuration and old running image IDs before rebuild; restore pre-upgrade schema when migration
has started; restore old checkout mounts and start pinned services with --no-deps, never migrate.
Automatic recovery necessarily discards writes newer than the live snapshot; documented in RUNBOOK.

Reviewer found and implementation fixed: INT/TERM previously bypassed ERR recovery; repeated stop
failure before migration prevented restarting old services; hardcoded backup path masked backup.env.
Reviewer independently injected faults into actual deploy script with local transport/runtime doubles:
`persistent-stop exit= 8 restored= True restores= 0`
`term-during-migration exit= 143 restored= True restores= 1`
Initial independent regression rerun: `Ran 13 tests in 8.259s` / `OK`.

Reviewer inspected rendered Compose: AWS/S3 absent from every service; admin/registration/cursor
secrets only API; DB receives exactly PostgreSQL settings. Production ports omit host_ip and include
443/udp; localhost mode retains 127.0.0.1. Environment/port assertions now executable regressions.

Root verifies actual local TLS/MCP/backup drills separately; final evidence in ../GATE-RESULTS.md.
No cloud boot, SSH production deploy, public IPv6/ACME or genuine object-store upload was attempted.

Final integration found that Compose JSON already escapes dollar literals. Removed redundant
escaping from rollback capture; real Compose input/output roundtrip now tests healthcheck and
single-quoted dotenv literals. Probe/backup readers decode serialized dollars once before using
credentials. Final suite: 18 tests / OK. Both live backup drills rerun successfully afterwards.
