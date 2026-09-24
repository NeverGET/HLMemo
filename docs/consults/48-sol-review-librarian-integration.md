## Verdict

**NO-GO** for R2. The librarian integration is on local `main`, but `memory.answer(accept)` can change user links and validity while the configured role is OBSERVER. The existing [integration test](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_w2c_questions.py:78) exercises that path and expects two links and one validity close.

## (a) Consult 47

- **Rule visibility — FIXED:** references are rechecked before every provider attempt, including retry and fallback.
- **TTL — PARTIALLY FIXED:** the clock is read after device and question lock waits. A later item lock wait can still carry an approval past expiry.
- **G-LIVE-B artifacts — FIXED:** fallback and production-chain artifacts now record live mode, three repetitions, class thresholds and provider calls. I did not rerun them.

## Findings

| Severity | File:line | Issue | Fix |
|---|---|---|---|
| **Critical** | [questions.py:197](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/questions.py:197) | `accept` applies link, validity and scope mutations without an effective-role check. | Under the role lock, reject or defer application while any touched project is OBSERVER; update the test. |
| High | [check_librarian.py:170](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/check_librarian.py:170) | Post-cutover evaluation can PASS with librarian OFF or a role above observer. | Make R2 evaluation require enabled=true, live mode and observer in API, librarian and heartbeat. |
| Medium | [check_librarian.py:110](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/check_librarian.py:110) | API risk-judge configuration errors are reported but do not fail the check. | Fail R2 configuration validation on error or an empty judge chain. |
| Medium | [worker.py:496](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/worker.py:496) | TTL is not rechecked after the item lock wait in batch application. | Compare against a fresh clock after that wait. |

The five recent merge commits include the W2b/W2c integration and R2 deploy preparation. [Trigger wiring](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/trigger.py:64) uses priorities **2/3/6**; import sends one item per write, and [replay](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/replay.py:251) restores `resolved.librarian_jobs`. Migration `0008` follows `0007`; eight tools are listed. `STATUS.md` records G-SURF **2758/3000**, which this static review did not remeasure.

Worker OBSERVER and batch approval paths do not apply user mutations; backfill queues jobs. Provider privacy checks are default-deny, and spend reservations are atomic across the import burst. Ordinary query/write do not await the LLM. Production `llm.env` reaches only API and librarian; the installer sends the key over SSH stdin and masks it in its summary.

## Must-fix before R2

- Close the `memory.answer(accept)` OBSERVER mutation path and verify zero user-item, link and validity changes.
- Make the post-cutover check enforce **ON + OBSERVER** and valid API judge configuration.
- Recheck batch TTL after the final lock wait; complete the outstanding R2 gates and rehearsal recorded in [STATUS.md](/Users/cemalkurt/Projects/HLMemo/docs/status/STATUS.md:31).

**Scope:** static read and Git review only; no tests, Docker or edits.