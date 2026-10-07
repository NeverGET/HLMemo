NO-GO — HEAD `ecec1e2`; clean tree; 26,900 direct packing comparisons passed; DB tests not rerun (`HLM_TEST_DSN` unset).
- Round 1 HIGH title redaction — partially | `src/hlmemo/core/risk_service.py:256`: known Redactor cases fixed, but a new secret leak remains below.
- Round 1 MEDIUM warning/budget regression — fixed | `src/hlmemo/core/risk_service.py:294`
- Round 1 MEDIUM visibility/lifecycle tests — fixed | `tests/integration/test_risk_dropped_by_judge.py:139`, `:154`, `:282`
- Round 1 LOW misleading no-match summary — fixed | `src/hlmemo/cli/preflight.py:171`
- Round 1 LOW deterministic `why` contract — fixed | `src/hlmemo/core/risk_service.py:265`
- Round 1 LOW unchanged-packing documentation — fixed | `docs/decisions/DECISIONS.md:2352`
- NEW HIGH — `src/hlmemo/core/risk_service.py:260`: title `password="Correct Horse Battery Staple 123"` passes the write guard and Redactor unchanged because the assignment regex stops at whitespace (`src/hlmemo/librarian/redact.py:53`); `dropped_by_judge[].title` exposes the passphrase.
- NEW MEDIUM — `src/hlmemo/core/risk_service.py:318`: at budget 256, a valid 200-character Japanese title makes the dropped entry not fit; both dropped fields disappear and `src/hlmemo/cli/preflight.py:187` falsely reports “found no matching past lesson.”
- NEW LOW — `src/hlmemo/cli/preflight.py:172`: with 1 packed and 2 omitted dropped entries, line 179 says it “lists 3,” although only one is readable.