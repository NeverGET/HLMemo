## Verdict (FIX-NEEDED)

The privacy gate blocks redactor-detected secrets and rechecks device trust/expiry/generation, grant/admin status, project existence, and `policy.librarian != off` before every network attempt. `librarian_cross_project=exclude` still permits a query confined to that project under D-083. Query requests have no `device_scope`, so caller-supplied wording about `device:*` memory may reach the provider, although stored item text is never sent. Cache isolation, shared retrieval filters, flag-off byte identity, cap-before-fetch, and D-087’s rank bound relative to the post-cap baseline check out. Direct probes nevertheless found release-blocking guard and availability defects. The per-task fallback hook is absent from this export and requires separate final-R3 verification.

## Findings

| severity | file:line | trigger (concrete scenario) | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/core/language.py:100-105,235-277,307-326` | `src/hlmemo`→`src/server`, bare `pytest`→`tox`, and `'prod'`→`'dev'` all yield `protected_tokens=[]` and `check_rewrite.ok=True`, violating D-088’s verbatim path/command/quoted-string guarantee. | Conservatively protect ambiguous relative paths; recognize paired straight/curly single quotes; add command-context extraction or narrow the stated command guarantee. Add these exact regressions. |
| HIGH | `src/hlmemo/librarian/query_rewrite.py:402-404`; `provider.py:420-485,540-567` | A stalled primary uses the default background retries and consumes the 8-second deadline; a healthy fallback is never attempted. | Call `complete(..., attempt_policy="latency")` and test stalled/503 primary plus healthy fallback within the total deadline. |
| MEDIUM | `src/hlmemo/core/read_service.py:265-287,321-323` | With stale “API cache TTL is 60 seconds”, replacement “300 seconds”, and a Turkish query, the English branch finds both but demotion receives only Turkish terms: order remains stale-first `[old,new]`; combined terms produce `[new,old]`. | Pass a stable union of original and English terms to partial-supersession demotion; add the exact pair/link/query regression. |
| MEDIUM | `src/hlmemo/librarian/query_rewrite.py:144-146,268-310,402-404`; `provider.py:597-606` | A trusted read device can continuously issue distinct non-English queries. Calls have neither `lineage` nor `job_id`, so they can consume the shared hour/day/month budget and starve librarian jobs, although the global cap still prevents overspend. | Add stable lineage plus an atomic per-device/task quota or separate rewrite budget. |
| MEDIUM | `src/hlmemo/librarian/query_rewrite.py:254-303,346-358`; `read_service.py:344-347` | The 8-second deadline starts only after semaphore acquisition: the last of 32 tasks can wait about 120 seconds. When the queue is full, `schedule()` returns false but the response still says `pending`. | Start an absolute deadline at enqueue, include queue wait, and return `unavailable`/`dropped` when scheduling fails. |

## Ship-with-flag-ON assessment

No — fix the guard, fallback policy, stale-hit term handling, and bounded-admission semantics before enabling query rewrite in R3.