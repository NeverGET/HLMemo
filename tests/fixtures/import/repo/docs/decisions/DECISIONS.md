# Fixture — Decision Log (append-only)

Format: `D-NNN | date | status | decision | rationale | consequences`

D-001 | 2026-01-05 | ACCEPTED | The fixture service stores sessions in Postgres, not Redis. | One database is enough at this size. | Session reads go through the pool.
D-002 | 2026-02-11 | ACCEPTED | Retries back off exponentially up to 30 seconds (supersedes the fixed 5-second retry). | Thundering herd after outages. | The client caps attempts at 8.
D-003 | 2099-01-01 | PROPOSED | A decision dated in the far future. | A typo in the date. | Must be rejected per item, never imported undated.
