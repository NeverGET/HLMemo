NO-GO

fixed | R1 unchecked fields — recursive scan and pre-parse invocation: src/hlmemo/core/secret_guard.py:48-72; src/hlmemo/core/write_service.py:362-364,407-409; src/hlmemo/core/lesson_service.py:123-124.
fixed | R1 error echo/chained ValidationError — secret keys become `<key>` and exception context is dropped: src/hlmemo/core/secret_guard.py:62-69; src/hlmemo/core/write_models.py:479-486.
fixed | R1 JWT documentation false reject — JWT removed from write rules as decided: src/hlmemo/core/secret_guard.py:26-34.
fixed | R1 PV-5 case variants — tags are stripped/case-folded: src/hlmemo/core/write_models.py:57-62.
not fixed | R1 forgeable `source` exemption — intentionally accepted residual, not counted in verdict: src/hlmemo/core/write_service.py:504-513.
fixed | R1 secret-shaped project/project_ids authorization echo — scanned before authorization: src/hlmemo/core/secret_guard.py:55-72; src/hlmemo/core/write_service.py:362-364.
fixed | R1 missing PV-3 blank reason — lesson parts and empty-string normalization covered: src/hlmemo/core/lesson_service.py:82-85; src/hlmemo/core/write_models.py:444-469.

NEW HIGH | Legitimate placeholder paths/URLs are rejected: src/hlmemo/core/secret_guard.py:30,58-60. Trigger: schema-valid `source.path="docs/xoxb-` + `placeholder-token.md"` returns `slack-token`.
NEW HIGH | Refused secret can still enter INFO logs: src/hlmemo/server/mcp_server.py:196-200,268-274. Trigger: `X-HLM-Client=ghp_` + 36 chars; envelope is redacted but `finally` logs the token verbatim.
NEW MEDIUM | Pre-validation eager list expansion can exhaust memory: src/hlmemo/core/secret_guard.py:71-72; src/hlmemo/core/write_service.py:362-364. Three million zero items (~6 MB JSON) measured 1.16 s/~489 MB RSS, below the 64 MiB transport cap.
NEW MEDIUM | Valid requests are scanned twice when `raw` and `req` alias: src/hlmemo/core/write_service.py:362-363. A 50×64,000-character request measured 123 ms once versus 261 ms on the actual double scan.
Checks | Direct repros passed; UUID, lowercase SHA-256, representative base64, ordinary URL/code did not match. Database pytest was not rerun because `HLM_TEST_DSN` is unset.