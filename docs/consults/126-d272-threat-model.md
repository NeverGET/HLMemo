# 126: D-272 librarian shadow pipeline: threat model and severity rubric

Written before the review (D-266). The change is a release: it adds code and prod configuration.

## Change
- **`write_review`.** When `HLM_LIBRARIAN_SHADOW_PROFILE` is set, every job also runs the same candidate pairs through:
  - relate/v3 (opt-in prompt version) on the shadow profile;
  - the D-269 historical guard;
  - relate_verify on `HLM_LIBRARIAN_SHADOW_VERIFY_PROFILE` (max_tokens 2000);
  - finalize and `_proposals`.

  The result is written only to `request_extra["shadow"]`, the job's audit event, as a compact block of judgements, proposals, guards and call audits. Any exception is caught and recorded as `error`.
- **Shadow calls** go through the same Provider, so they share the budget guard, the ledger and the breakers. They carry their own uuid5 lineage, so they never use up the job's call cap.
- **`LlmProfile.cache_system`** puts a `cache_control` block on the system message in `build_body`. The caller's message list is not changed.
- **Two new profile files.** Claude Haiku 5.5 high and Opus 5.5 low: no temperature, cache on.
- **`hlm ops librarian shadow`.** A read-only report: live vs shadow supersessions, spend per profile.
- **Deploy.** `llm.env.example` sets both shadow profiles. The installer checks that these profiles exist and reads their keys. The fingerprint covers the two keys.

## Assets and threats
1. **Live librarian outcome.** A shadow run must never change the live plan: no proposals, questions, mutations, signals, outcome or call cap. Threats:
   - shared mutable state between the twins and the live pairs (Judgement objects, the counters, `plan.calls`);
   - an exception that escapes;
   - a DB error that aborts the job's transaction;
   - `CancelledError` handling;
   - lease or deadline overrun from the extra latency (Haiku high p95 ≈ 21 s per call).
2. **Spend.** The shadow spends against the hour/day/month caps (3/8/60 USD). Threats:
   - a runaway shadow (call loop, missing max_tokens);
   - shadow spend starving the live jobs or memory.ask;
   - cache writes priced above the reservation.
3. **Privacy.** Shadow prompts must not see more than the live ones. They use the same pairs, rules and precheck: the privacy gate runs before every attempt. Threats: a path that skips the precheck; owner data in the audit block beyond what the live audit already holds.
4. **Replay determinism.** Events are replayed from `resolved`, and the shadow is in `request` only. Threat: replay or projection code that reads `request` keys.
5. **Release state.** `llm.env` provenance and fingerprint, the r4 manifest check, rollback to an env without the shadow keys, and an unknown shadow profile on the host.

## Severity rubric
- **HIGH:**
  - the shadow can change any live outcome (proposals, questions, links, closes, signals, the job's call cap or outcome);
  - the shadow can spend without the budget guard, or loop;
  - the privacy precheck can be skipped;
  - the job can fail or hang because of the shadow;
  - the deploy can leave api and librarian on different envs.
- **MEDIUM:**
  - wrong or misleading shadow audit (the report pairs the wrong items, live supersessions miscounted);
  - fingerprint or installer gaps;
  - a prompt or schema mismatch that makes v3 answers fail the guards;
  - an unbounded audit block size.
- **LOW:** naming, docs, test gaps.

Every HIGH gets a reproducing test. At most 2 rounds; the owner decides any residual risk.
