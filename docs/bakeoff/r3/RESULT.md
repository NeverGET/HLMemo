# R3 result — F13 + G-B security fixes (implementation round)
Gates A1–A4 run by the judge identically on both branches (A2 twice, A4 alternating W1/W2/W1/W2): both 4/4,
no flakiness, Recall@5 0.930 on every run, p95 within 270–287 ms on both (difference inside run-to-run noise).
Defects: blinded reproduction on neutral worktrees; each confirmed defect was ALSO tested on the other branch.
Scoring rule used (disclosed): a defect demonstrated on a branch is subtracted from that branch whether or not the
other side's reviewer reported it, so defects common to both cancel out.

| | Opus 5.5 | gpt-6-astra |
|---|---|---|
| Gates | 4/4 | 4/4 |
| Defects only in this branch | R09 High: under DB timeouts revoke raises QueryCanceled → HTTP 500, device stays trusted; R02 Low: connection leaked if cancelled during pool return | R04 Low: 10 s total body deadline 408s a slow link; R10 Low: every REST route error discards the pooled connection |
| Defects common to both | R01 Med, R05 Med, R06 High, R07 High, R08 Med | same |
| **Total** | **−12** | **−10** |
| Wall time | 10 min 10 s | 17 min 38 s |
| New regression tests | 11 | 34 |
| Its review of the other (confirmed / plausible / rejected) | 5 / 0 / 0 | 3 / 0 / 1 |

Rejected: R03 (the 413 on contract-maximum writes comes from the MCP SDK's pre-existing 4 MiB transport cap,
`mcp/server/transport_security.py:162`, not from either branch — recorded as a contract/SDK gap).
Outcome: bake/r3-astra merged to main (567a275); full suite 362 passed after merge.
