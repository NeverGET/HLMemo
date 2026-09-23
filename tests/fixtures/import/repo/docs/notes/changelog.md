# Fixture changelog

Entries are appended at the bottom; each entry's heading carries its date.

## 2026-01-10 — Queue introduced

The worker queue replaced the cron sweep; jobs are leased for 120 seconds.

## 2026-02-01 — Lease renewal

Leases are renewed every 30 seconds while a job runs, so long embeddings no longer expire.
