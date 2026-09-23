---
name: Testing feedback
description: integration tests must hit a real database, never mocks
type: feedback
---
Integration tests must run against a real Postgres database. Mocked database tests passed while
the production migration failed last quarter.
