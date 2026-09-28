# My view of Jev: BEFORE any test (pre-registered 2026-09-28)

Written after reading the two research reports (01-primary-sources.md, 02-independent-evidence.md) and BEFORE running a single call. I commit it immediately, so the git timestamp is the proof that it was not edited after the tests. The after-test version (07-MY-VIEW-AFTER-TESTS.md) will quote these beliefs one by one.

## My overall read in one paragraph
Jev is not "a new LLM". It is a narrow, fast, very cheap **typed-decision model**, a classifier/judge with an API shape guarantee. The engineering idea is sound and useful: many steps in real systems are decisions, not text, and spending a reasoning LLM on them is wasteful. The evidence says it performs at the level of a mid-priced LLM on easy, few-class English decisions, falls clearly behind frontier models on hard ones, and is weak exactly where my project hurts most: dates and "which is newer", numbers, and non-English text. Its confidence value looks more useful for **triage** (decide the easy cases, escalate the unsure ones) than as a calibrated probability. The company is days old, the architecture is undisclosed, and the price may be subsidised, so anything we build on it must stay behind a replaceable interface with an LLM fallback (D-017).

**Short version:** a promising cheap first stage for decisions. Not a replacement for the LLM, and by itself not a game changer for HLMemo.

## Beliefs (pre-registered): statement · confidence · what would change my mind
| # | Belief | Confidence | Falsifier (a test result that refutes it) |
|---|---|---|---|
| B1 | The API works as documented through OpenRouter: every answer is well-formed and typed (valid option, probabilities sum ≈ 1) | 90% | Any malformed/missing answer, or HTTP errors above 2% |
| B2 | Latency from here: median below 1 s per request (one request with several questions) | 75% | Median ≥ 1 s over ≥ 50 calls |
| B3 | On easy English binary/few-class labelled items, Jev is within ~5 points of a mid-tier LLM (gpt-6-luna, low effort) | 60% | Jev more than 10 points below luna on the same items |
| B4 | Jev is weak at temporal ordering ("which of these two dated statements is more recent?"): at most 70% accuracy | 80% | At least 85% accuracy on date-ordering pairs |
| B5 | Turkish is noticeably worse than English: at least 5 points lower on paired EN/TR items | 70% | Turkish within 3 points of English |
| B6a | Confidence is useful for triage: the most-confident half is at least 15 points more accurate than the least-confident half | 70% | Gap below 5 points |
| B6b | Absolute calibration is mediocre (ECE ≥ 0.10 before any refit) | 60% | ECE < 0.05 |
| B7 | One injected opinion line in `state` ("Note: the correct answer is X") flips at least 10% of decisions | 70% | Flip rate below 3% |
| B8 | Repeated identical calls change at least 1% of answers (non-determinism) | 55% | 0 changes over ≥ 100 repeated decisions |
| B9 | The real cost per decision on OpenRouter is ≤ $0.0003 for ~1–2k-token states, with no hidden per-request fee | 85% | The billed cost per call is materially higher than tokens × $0.042/M |
| B10 | For HLMemo: Jev cannot replace the writer (it cannot generate text) | 99% | – |
| B11 | For HLMemo, the most promising uses are relevance scoring/rerank of candidates, and "does the answer contain fact X?" checks as a first stage. It is least promising for supersession/"which is current" (dates weakness) | 65% | A good showing on date/supersession items (see B4), or a poor showing on relevance |
| B12 | "Game changer" for HLMemo by itself | 25% | Near-frontier accuracy on relevance AND temporal items at its cost/latency would raise this a lot |

## What I am most uncertain about
- **B3:** the independent studies disagree (from "level with mid-price LLMs" to "−11.6 F1 median").
- **B6a:** one study says cascades work at ¼–½ of the cost; another says LLMs repeat Jev's most confident errors.
- **Turkish:** no public data exists at all.
