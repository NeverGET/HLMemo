## Findings

| # | Severity (High/Medium/Low) | Area | file:line | Trigger | Observed vs expected | Suggested fix | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | High | Revocation | `src/hlmemo/server/devices.py:98` | Default settings. A device’s `call_the_day` waits successively on four advisory locks for 4 seconds each. Admin revocation starts immediately after that request’s authentication. | Each target wait satisfies the 5-second lock timeout; its 16-second transaction satisfies the 30-second transaction limit. However, revoke’s unchanged **15-second statement timeout** cancels its `FOR UPDATE` before the target finishes. Revocation rolls back, returns HTTP 500, and leaves the device trusted. Increasing only `lock_timeout` does not guarantee successful bounded revocation. | Align revoke’s statement timeout with the transaction bound, allowing time for mutation and commit; map exhausted deadlines to retryable errors. | High |
| 2 | Medium | Hidden-item oracle | `src/hlmemo/core/write_service.py:673` | Another device holds a private item’s advisory lock for over 5 seconds during revision. A project writer submits a revision or `expected_versions` entry targeting that item. | Target locking precedes visibility authorization. The hidden target produces `E_UNAVAILABLE` with SQLSTATE `55P03`; an unused ID produces `E_NOT_FOUND`. This exposes hidden-item activity through an error distinction, despite fixing direct head disclosure and overwrite. | Check target visibility before acquiring advisory locks, then reload and reauthorize after locking to prevent races. | High |
| 3 | Medium | Pool exhaustion / readiness | `src/hlmemo/server/middleware.py:222`; `src/hlmemo/server/app.py:163` | Configure the supported `pool_max_size=1`, then send one authenticated `GET /ready` with a complete body. | Middleware holds the sole connection and device `FOR SHARE` while readiness requests **another** connection. It pins the entire pool until the 10-second acquisition timeout and falsely reports an unavailable database. Concurrent authenticated probes can produce the same saturation with larger pools, obstructing other requests, including revocation. | Reuse the request connection for readiness’s DB check, and release it before model verification; avoid nested pool acquisition. | High |
| 4 | Medium | Valid large requests | `src/hlmemo/config.py:170` | Submit a contract-valid `memory.write` containing 50 items, each with 64,000 three-byte Unicode characters, and sufficient acknowledgement budget. | Bodies alone occupy **9,600,000 bytes**, exceeding the new 8 MiB default. Middleware returns 413 although §3 permits these item counts and character lengths. JSON escaping can further increase valid wire sizes. | Size the default cap to accommodate documented payload limits and encoding overhead, or explicitly reconcile a smaller transport limit with the contract. | High |

## Strengths

- Revision authorization checks every current segment’s project membership and device scope, including cards and `expected_versions`, before head comparison.
- Body ingestion enforces actual-byte limits and an overall deadline before protected requests acquire a connection.
- Normal finite responses, including chunked JSON, commit and release their connection before sending bytes.
- Normal SSE streams commit and release before headers; the send lock also gates concurrent pings.
- Pool connections receive DB-side safety nets, and last-seen updates use a short lock timeout.

## Overall

**Do not merge as-is:** bounded successful revocation remains broken, a concurrent hidden-item oracle survives, and readiness and large-request contract failures remain.